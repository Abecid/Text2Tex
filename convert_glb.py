import os
import subprocess

base_path = 'outputs_server/backpack'

def save_mesh_as_glb(mesh_path, file_path):
    """
    Save the given mesh as a GLB file.
    """
    if not os.path.exists(file_path):
        # Load the mesh with textures and convert to GLB
        command = f"obj2gltf -i {mesh_path} -o {file_path}"
        subprocess.run(command, shell=True)
        print(f"Converted {mesh_path} to {file_path}")
    else:
        print(f"GLB already exists for {mesh_path}, skipping.")

def process_results(results_path):
    """
    Process all .obj files in the results folder, converting them to GLB.
    """
    for file_name in os.listdir(results_path):
        if file_name.endswith('.obj'):
            obj_file_path = os.path.join(results_path, file_name)
            glb_file_path = os.path.splitext(obj_file_path)[0] + '.glb'
            save_mesh_as_glb(obj_file_path, glb_file_path)

def main():
    for mesh_folder in os.listdir(base_path):
        mesh_folder_path = os.path.join(base_path, mesh_folder)
        generate_folder_path = os.path.join(mesh_folder_path, 'generate')
        update_folder_path = os.path.join(mesh_folder_path, 'update')
        
        generate_mesh_folder_path = os.path.join(generate_folder_path, 'mesh')
        update_mesh_folder_path = os.path.join(update_folder_path, 'mesh')
        
        if os.path.isdir(generate_mesh_folder_path):
            process_results(generate_mesh_folder_path)
        if os.path.isdir(update_mesh_folder_path):
            process_results(update_mesh_folder_path)

if __name__ == "__main__":
    main()