import os
import torch

import cv2
import random

import numpy as np

from torchvision import transforms

from pytorch3d.renderer import TexturesUV
from pytorch3d.ops import interpolate_face_attributes

from PIL import Image

from tqdm import tqdm

# customized
import sys
sys.path.append(".")

from lib.camera_helper import init_camera
from lib.render_helper import init_renderer, render
from lib.shading_helper import (
    BlendParams,
    init_soft_phong_shader, 
    init_flat_texel_shader,
)
from lib.vis_helper import visualize_outputs, visualize_quad_mask
from lib.constants import *


def get_all_4_locations(values_y, values_x):
    y_0 = torch.floor(values_y)
    y_1 = torch.ceil(values_y)
    x_0 = torch.floor(values_x)
    x_1 = torch.ceil(values_x)

    return torch.cat([y_0, y_0, y_1, y_1], 0).long(), torch.cat([x_0, x_1, x_0, x_1], 0).long()


def compose_quad_mask(new_mask_image, update_mask_image, old_mask_image, device):
    """
        compose quad mask:
            -> 0: background
            -> 1: old
            -> 2: update
            -> 3: new
    """

    new_mask_tensor = transforms.ToTensor()(new_mask_image).to(device)
    update_mask_tensor = transforms.ToTensor()(update_mask_image).to(device)
    old_mask_tensor = transforms.ToTensor()(old_mask_image).to(device)

    all_mask_tensor = new_mask_tensor + update_mask_tensor + old_mask_tensor

    quad_mask_tensor = torch.zeros_like(all_mask_tensor)
    quad_mask_tensor[old_mask_tensor == 1] = 1
    quad_mask_tensor[update_mask_tensor == 1] = 2
    quad_mask_tensor[new_mask_tensor == 1] = 3

    return old_mask_tensor, update_mask_tensor, new_mask_tensor, all_mask_tensor, quad_mask_tensor


def compute_view_heat(similarity_tensor, quad_mask_tensor):
    num_total_pixels = quad_mask_tensor.reshape(-1).shape[0]
    heat = 0
    for idx in QUAD_WEIGHTS:
        heat += (quad_mask_tensor == idx).sum() * QUAD_WEIGHTS[idx] / num_total_pixels

    return heat


def select_viewpoint(selected_view_ids, view_punishments,
    mode, dist_list, elev_list, azim_list, sector_list, view_idx,
    similarity_texture_cache, exist_texture,
    mesh, faces, verts_uvs,
    image_size, faces_per_pixel,
    init_image_dir, mask_image_dir, normal_map_dir, depth_map_dir, similarity_map_dir,
    device, use_principle=False,
    hits=1,
    xray_mesh=None
):
    if mode == "sequential":
        
        num_views = len(dist_list)

        dist = dist_list[view_idx % num_views]
        elev = elev_list[view_idx % num_views]
        azim = azim_list[view_idx % num_views]
        sector = sector_list[view_idx % num_views]
        
        selected_view_ids.append(view_idx % num_views)

    elif mode == "heuristic":

        if use_principle and view_idx < 6:

            selected_view_idx = view_idx

        else:

            selected_view_idx = None
            selected_hit = None
            max_heat = 0

            print("=> selecting next view...")
            view_heat_list = []
            for hit in range(hits):
                for sample_idx in tqdm(range(len(dist_list))):
                    mesh = xray_mesh[hits * sample_idx + hit] if xray_mesh is not None else mesh
                    view_heat, *_ = render_one_view_and_build_masks(dist_list[sample_idx], elev_list[sample_idx], azim_list[sample_idx], 
                        sample_idx, sample_idx, view_punishments,
                        similarity_texture_cache, exist_texture,
                        mesh, faces, verts_uvs,
                        image_size, faces_per_pixel,
                        init_image_dir, mask_image_dir, normal_map_dir, depth_map_dir, similarity_map_dir,
                        device)

                    if view_heat > max_heat:
                        selected_view_idx = sample_idx
                        selected_hit = hit
                        max_heat = view_heat

                    view_heat_list.append(view_heat.item())

            print(view_heat_list)
            print("select view {}, hit {} with heat {}".format(selected_view_idx, selected_hit, max_heat))

 
        dist = dist_list[selected_view_idx]
        elev = elev_list[selected_view_idx]
        azim = azim_list[selected_view_idx]
        sector = sector_list[selected_view_idx]

        selected_view_ids.append(selected_view_idx)

        view_punishments[selected_view_idx] *= 0.01

    elif mode == "random":

        selected_view_idx = random.choice(range(len(dist_list)))

        dist = dist_list[selected_view_idx]
        elev = elev_list[selected_view_idx]
        azim = azim_list[selected_view_idx]
        sector = sector_list[selected_view_idx]
        
        selected_view_ids.append(selected_view_idx)

    else:
        raise NotImplementedError()

    return dist, elev, azim, sector, selected_view_ids, view_punishments, selected_hit


@torch.no_grad()
def build_backproject_mask(mesh, faces, verts_uvs, 
    cameras, reference_image, faces_per_pixel, 
    image_size, uv_size, device, textures_idx=None):
    # construct pixel UVs
    renderer_scaled = init_renderer(cameras,
        shader=init_soft_phong_shader(
            camera=cameras,
            blend_params=BlendParams(),
            device=device),
        image_size=image_size, 
        faces_per_pixel=faces_per_pixel
    )
    fragments_scaled = renderer_scaled.rasterizer(mesh)

    # get UV coordinates for each pixel
    if textures_idx is None:
        textures_idx = faces.textures_idx
    faces_verts_uvs = verts_uvs[textures_idx]

    pixel_uvs = interpolate_face_attributes(
        fragments_scaled.pix_to_face, fragments_scaled.bary_coords, faces_verts_uvs
    )  # NxHsxWsxKx2
    pixel_uvs = pixel_uvs.permute(0, 3, 1, 2, 4).reshape(-1, 2)

    texture_locations_y, texture_locations_x = get_all_4_locations(
        (1 - pixel_uvs[:, 1]).reshape(-1) * (uv_size - 1),
        pixel_uvs[:, 0].reshape(-1) * (uv_size - 1)
    )

    K = faces_per_pixel

    texture_values = torch.from_numpy(np.array(reference_image.resize((image_size, image_size)))).float() / 255.
    texture_values = texture_values.to(device).unsqueeze(0).expand([4, -1, -1, -1]).unsqueeze(0).expand([K, -1, -1, -1, -1])

    # texture
    texture_tensor = torch.zeros(uv_size, uv_size, 3).to(device)
    texture_tensor[texture_locations_y, texture_locations_x, :] = texture_values.reshape(-1, 3)

    return texture_tensor[:, :, 0]


@torch.no_grad()
def build_diffusion_mask(mesh_stuff, 
    renderer, exist_texture, similarity_texture_cache, target_value, device, image_size, 
    smooth_mask=False, view_threshold=0.01, textures_idx=None
    ):

    mesh, faces, verts_uvs = mesh_stuff
    mask_mesh = mesh.clone() # NOTE in-place operation - DANGER!!!
    
    if textures_idx is None:
        textures_idx = faces.textures_idx

    # visible mask => the whole region
    exist_texture_expand = exist_texture.unsqueeze(0).unsqueeze(-1).expand(-1, -1, -1, 3).to(device)
    mask_mesh.textures = TexturesUV(
        maps=torch.ones_like(exist_texture_expand),
        faces_uvs=textures_idx[None, ...],
        verts_uvs=verts_uvs[None, ...],
        sampling_mode="nearest"
    )
    # visible_mask_tensor, *_ = render(mask_mesh, renderer)
    visible_mask_tensor, _, similarity_map_tensor, *_ = render(mask_mesh, renderer)
    # faces that are too rotated away from the viewpoint will be treated as invisible
    valid_mask_tensor = (similarity_map_tensor >= view_threshold).float()
    visible_mask_tensor *= valid_mask_tensor

    # nonexist mask <=> new mask
    exist_texture_expand = exist_texture.unsqueeze(0).unsqueeze(-1).expand(-1, -1, -1, 3).to(device)
    mask_mesh.textures = TexturesUV(
        maps=1 - exist_texture_expand,
        faces_uvs=textures_idx[None, ...],
        verts_uvs=verts_uvs[None, ...],
        sampling_mode="nearest"
    )
    new_mask_tensor, *_ = render(mask_mesh, renderer)
    new_mask_tensor *= valid_mask_tensor

    # exist mask => visible mask - new mask
    exist_mask_tensor = visible_mask_tensor - new_mask_tensor
    exist_mask_tensor[exist_mask_tensor < 0] = 0 # NOTE dilate can lead to overflow

    # all update mask
    mask_mesh.textures = TexturesUV(
        maps=(
            similarity_texture_cache.argmax(0) == target_value
            # # only consider the views that have already appeared before
            # similarity_texture_cache[0:target_value+1].argmax(0) == target_value
        ).float().unsqueeze(0).unsqueeze(-1).expand(-1, -1, -1, 3).to(device),
        faces_uvs=textures_idx[None, ...],
        verts_uvs=verts_uvs[None, ...],
        sampling_mode="nearest"
    )
    all_update_mask_tensor, *_ = render(mask_mesh, renderer)

    # current update mask => intersection between all update mask and exist mask
    update_mask_tensor = exist_mask_tensor * all_update_mask_tensor

    # keep mask => exist mask - update mask
    old_mask_tensor = exist_mask_tensor - update_mask_tensor

    # convert
    new_mask = new_mask_tensor[0].cpu().float().permute(2, 0, 1)
    new_mask = transforms.ToPILImage()(new_mask).convert("L")

    update_mask = update_mask_tensor[0].cpu().float().permute(2, 0, 1)
    update_mask = transforms.ToPILImage()(update_mask).convert("L")

    old_mask = old_mask_tensor[0].cpu().float().permute(2, 0, 1)
    old_mask = transforms.ToPILImage()(old_mask).convert("L")

    exist_mask = exist_mask_tensor[0].cpu().float().permute(2, 0, 1)
    exist_mask = transforms.ToPILImage()(exist_mask).convert("L")

    return new_mask, update_mask, old_mask, exist_mask


@torch.no_grad()
def render_one_view(mesh,
    dist, elev, azim,
    image_size, faces_per_pixel,
    device):

    # render the view
    cameras = init_camera(
        dist, elev, azim,
        image_size, device
    )
    renderer = init_renderer(cameras,
        shader=init_soft_phong_shader(
            camera=cameras,
            blend_params=BlendParams(),
            device=device),
        image_size=image_size, 
        faces_per_pixel=faces_per_pixel
    )

    init_images_tensor, normal_maps_tensor, similarity_tensor, depth_maps_tensor, fragments = render(mesh, renderer)
    
    return (
        cameras, renderer,
        init_images_tensor, normal_maps_tensor, similarity_tensor, depth_maps_tensor, fragments
    )


@torch.no_grad()
def build_similarity_texture_cache_for_all_views(meshes, faces, verts_uvs,
    dist_list, elev_list, azim_list,
    image_size, image_size_scaled, uv_size, faces_per_pixel,
    device,
    hits=1,
    xray_mesh=None
    ):

    num_candidate_views = len(dist_list)
    similarity_texture_cache = torch.zeros(num_candidate_views*hits, uv_size, uv_size).to(device)
    
    print(f"Number of Meshes: {len(meshes)}")

    print("=> building similarity texture cache for all views...")
    for i in tqdm(range(num_candidate_views)):
        for j in range(hits):
            print(f"Processing view {i} hit {j}")
            mesh = meshes[i*hits+j]
            textures_idx = None
            if hits > 1 and xray_mesh is not None:
                # faces = meshes[j].faces_packed()
                faces = None
                # textures_idx = mesh.textures.faces_uvs_packed()
                textures_idx = xray_mesh.visible_texture_map_list[i * hits + j]
            # else:
                # mesh = meshes
                # textures_idx = None
            cameras, _, _, _, similarity_tensor, _, _ = render_one_view(mesh,
                dist_list[i], elev_list[i], azim_list[i],
                image_size, faces_per_pixel, device)

            similarity_texture_cache[i*hits+j] = build_backproject_mask(mesh, faces, verts_uvs, 
                cameras, transforms.ToPILImage()(similarity_tensor[0, :, :, 0]).convert("RGB"), faces_per_pixel,
                image_size_scaled, uv_size, device, textures_idx=textures_idx)

    return similarity_texture_cache


@torch.no_grad()
def render_one_view_and_build_masks(dist, elev, azim, 
    selected_view_idx, view_idx, view_punishments,
    similarity_texture_cache, exist_texture,
    mesh, faces, verts_uvs,
    image_size, faces_per_pixel,
    init_image_dir, mask_image_dir, normal_map_dir, depth_map_dir, similarity_map_dir,
    device, save_intermediate=False, smooth_mask=False, view_threshold=0.01,
    textures_idx=None,
    hit=1
    ):
    
    # render the view
    (
        cameras, renderer,
        init_images_tensor, normal_maps_tensor, similarity_tensor, depth_maps_tensor, fragments
    ) = render_one_view(mesh,
        dist, elev, azim,
        image_size, faces_per_pixel,
        device
    )
    
    init_image = init_images_tensor[0].cpu()
    init_image = init_image.permute(2, 0, 1)
    init_image = transforms.ToPILImage()(init_image).convert("RGB")

    normal_map = normal_maps_tensor[0].cpu()
    normal_map = normal_map.permute(2, 0, 1)
    normal_map = transforms.ToPILImage()(normal_map).convert("RGB")

    depth_map = depth_maps_tensor[0].cpu().numpy()
    depth_map = Image.fromarray(depth_map).convert("L")

    similarity_map = similarity_tensor[0, :, :, 0].cpu()
    similarity_map = transforms.ToPILImage()(similarity_map).convert("L")


    flat_renderer = init_renderer(cameras,
        shader=init_flat_texel_shader(
            camera=cameras,
            device=device),
        image_size=image_size,
        faces_per_pixel=faces_per_pixel
    )
    new_mask_image, update_mask_image, old_mask_image, exist_mask_image = build_diffusion_mask(
        (mesh, faces, verts_uvs), 
        flat_renderer, exist_texture, similarity_texture_cache, selected_view_idx, device, image_size, 
        smooth_mask=smooth_mask, view_threshold=view_threshold, textures_idx=textures_idx
    )
    # NOTE the view idx is the absolute idx in the sample space (i.e. `selected_view_idx`)
    # it should match with `similarity_texture_cache`

    (
        old_mask_tensor, 
        update_mask_tensor, 
        new_mask_tensor, 
        all_mask_tensor, 
        quad_mask_tensor
    ) = compose_quad_mask(new_mask_image, update_mask_image, old_mask_image, device)

    view_heat = compute_view_heat(similarity_tensor, quad_mask_tensor)
    view_heat *= view_punishments[selected_view_idx]

    # save intermediate results
    if save_intermediate:
        init_image.save(os.path.join(init_image_dir, "{}_{}.png".format(view_idx, hit)))
        normal_map.save(os.path.join(normal_map_dir, "{}_{}.png".format(view_idx, hit)))
        depth_map.save(os.path.join(depth_map_dir, "{}_{}.png".format(view_idx, hit)))
        similarity_map.save(os.path.join(similarity_map_dir, "{}_{}.png".format(view_idx, hit)))

        new_mask_image.save(os.path.join(mask_image_dir, "{}_{}_new.png".format(view_idx, hit)))
        update_mask_image.save(os.path.join(mask_image_dir, "{}_{}_update.png".format(view_idx, hit)))
        old_mask_image.save(os.path.join(mask_image_dir, "{}_{}_old.png".format(view_idx, hit)))
        exist_mask_image.save(os.path.join(mask_image_dir, "{}_{}_exist.png".format(view_idx, hit)))

        visualize_quad_mask(mask_image_dir, quad_mask_tensor, view_idx, view_heat, device)

    return (
        view_heat,
        renderer, cameras, fragments,
        init_image, normal_map, depth_map, 
        init_images_tensor, normal_maps_tensor, depth_maps_tensor, similarity_tensor, 
        old_mask_image, update_mask_image, new_mask_image, 
        old_mask_tensor, update_mask_tensor, new_mask_tensor, all_mask_tensor, quad_mask_tensor
    )



@torch.no_grad()
def backproject_from_image(mesh, faces, verts_uvs, cameras, 
    reference_image, new_mask_image, update_mask_image, 
    init_texture, exist_texture,
    image_size, uv_size, faces_per_pixel,
    device,
    textures_idx=None
    ):

    # construct pixel UVs
    renderer_scaled = init_renderer(cameras,
        shader=init_soft_phong_shader(
            camera=cameras,
            blend_params=BlendParams(),
            device=device),
        image_size=image_size, 
        faces_per_pixel=faces_per_pixel
    )
    fragments_scaled = renderer_scaled.rasterizer(mesh)
    
    if textures_idx is None:
        textures_idx = faces.textures_idx

    # get UV coordinates for each pixel
    faces_verts_uvs = verts_uvs[textures_idx]

    pixel_uvs = interpolate_face_attributes(
        fragments_scaled.pix_to_face, fragments_scaled.bary_coords, faces_verts_uvs
    )  # NxHsxWsxKx2
    pixel_uvs = pixel_uvs.permute(0, 3, 1, 2, 4).reshape(pixel_uvs.shape[-2], pixel_uvs.shape[1], pixel_uvs.shape[2], 2)

    # the update mask has to be on top of the diffusion mask
    new_mask_image_tensor = transforms.ToTensor()(new_mask_image).to(device).unsqueeze(-1)
    update_mask_image_tensor = transforms.ToTensor()(update_mask_image).to(device).unsqueeze(-1)
    
    project_mask_image_tensor = torch.logical_or(update_mask_image_tensor, new_mask_image_tensor).float()
    project_mask_image = project_mask_image_tensor * 255.
    project_mask_image = Image.fromarray(project_mask_image[0, :, :, 0].cpu().numpy().astype(np.uint8))
    
    project_mask_image_scaled = project_mask_image.resize(
        (image_size, image_size),
        Image.Resampling.NEAREST
    )
    project_mask_image_tensor_scaled = transforms.ToTensor()(project_mask_image_scaled).to(device)

    pixel_uvs_masked = pixel_uvs[project_mask_image_tensor_scaled == 1]

    texture_locations_y, texture_locations_x = get_all_4_locations(
        (1 - pixel_uvs_masked[:, 1]).reshape(-1) * (uv_size - 1), 
        pixel_uvs_masked[:, 0].reshape(-1) * (uv_size - 1)
    )
    
    K = pixel_uvs.shape[0]
    project_mask_image_tensor_scaled = project_mask_image_tensor_scaled[:, None, :, :, None].repeat(1, 4, 1, 1, 3)

    texture_values = torch.from_numpy(np.array(reference_image.resize((image_size, image_size))))
    texture_values = texture_values.to(device).unsqueeze(0).expand([4, -1, -1, -1]).unsqueeze(0).expand([K, -1, -1, -1, -1])
    
    texture_values_masked = texture_values.reshape(-1, 3)[project_mask_image_tensor_scaled.reshape(-1, 3) == 1].reshape(-1, 3)

    # texture
    texture_tensor = torch.from_numpy(np.array(init_texture)).to(device)
    texture_tensor[texture_locations_y, texture_locations_x, :] = texture_values_masked
    
    init_texture = Image.fromarray(texture_tensor.cpu().numpy().astype(np.uint8))

    # update texture cache
    exist_texture[texture_locations_y, texture_locations_x] = 1

    return init_texture, project_mask_image, exist_texture

