import pandas as pd
import subprocess
import os

from tqdm import tqdm
import configargparse

def parse_config():
    parser = configargparse.ArgumentParser(
                        prog='Multi-View Diffusion',
                        description='Generate texture given mesh and texture prompt',
                        epilog='Refer to https://arxiv.org/abs/2311.12891 for more details')
    # File Config
    parser.add_argument('--prompt', type=str, required=False)
    parser.add_argument('--test', action="store_true", required=False)
    options = parser.parse_args()

    return options


max_hits = [1, 2, 4]
style_prompt = None
style_prompts = [
    None,
    "orange"
]

category_prompts = {
    "car": [
        "Minecart style",
    ],
    "cup": [

    ],
    "hat": [

    ],
    "house": [ # also cabin

    ],
    "tent": [

    ],
    "ring": [

    ],
    "crown": [

    ]
}

run_multiple_style_prompts = True

objects_path = "Objaverse_Objects.csv"
objects_path = "Mini_Objects.csv"
meshes_path = "objaverse"

def test_run():
    command = (
        "python scripts/generate_texture.py "
        f"--input_dir data/backpack/ "
        f"--output_dir data/backpack/outputs "
        "--obj_name mesh "
        "--obj_file mesh.obj "
        f'--prompt "orange backpack" '
        "--add_view_to_prompt "
        "--ddim_steps 50 "
        "--new_strength 1 "
        "--update_strength 0.3 "
        "--view_threshold 0.1 "
        "--blend 0 "
        "--dist 1 "
        "--num_viewpoints 36 "
        "--viewpoint_mode predefined "
        "--use_principle "
        "--update_steps 20 "
        "--update_mode heuristic "
        "--seed 42 "
        "--post_process "
        '--device "2080" '
        "--use_objaverse "
        f"--hits {max_hits} "
    )
    
    # Run the command
    print(f"Running command: {command}")
    subprocess.run(command, shell=True)


def run_batch(uid_list, description_list, style_prompt=None, max_hits=2):
    for i, uid in tqdm(enumerate(uid_list)):
        config_path = f"{meshes_path}/{uid}/config.yaml"
        mesh_folder_path = f"{meshes_path}/{uid}"
        description = description_list[i]
        
        if not os.path.exists(config_path):
            print(f"Config file missing: {config_path}")
            continue
        
        if style_prompt is not None:
            description = f"{style_prompt} {description}"
        
        # Construct the command with max_hits
        command = (
            "python scripts/generate_texture.py "
            f"--input_dir {mesh_folder_path} "
            f"--output_dir {mesh_folder_path}/outputs "
            "--obj_name model "
            "--obj_file model.obj "
            f'--prompt "{description}" '
            "--add_view_to_prompt "
            "--ddim_steps 50 "
            "--new_strength 1 "
            "--update_strength 0.3 "
            "--view_threshold 0.1 "
            "--blend 0 "
            "--dist 1 "
            "--num_viewpoints 36 "
            "--viewpoint_mode predefined "
            "--use_principle "
            "--update_steps 20 "
            "--update_mode heuristic "
            "--seed 42 "
            "--post_process "
            '--device "2080" '
            "--use_objaverse "
            f"--hits {max_hits} "
        )
        
        # Run the command
        print(f"Running command: {command}")
        subprocess.run(command, shell=True)

def main():
    global style_prompt, style_prompts, max_hits, run_multiple_style_prompts
    opt = parse_config()
    if opt.test:
        test_run()
        return
    if opt.prompt is not None:
        style_prompt = opt.prompt
    # Read the CSV and extract the list of uids
    df = pd.read_csv(objects_path)
    uid_list = df['uid'].tolist()
    descriptions = df['description'].tolist()

    # Loop through the uid list and run the experiment for each uid
    if run_multiple_style_prompts:
        for max_hit in max_hits:
            for style_prompt in style_prompts:
                run_batch(uid_list, descriptions, style_prompt, max_hit)
    else:
        run_batch(uid_list, descriptions, style_prompt, max_hits)

if __name__ == '__main__':
    main()
