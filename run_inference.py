import pandas as pd
import subprocess
import os
from tqdm import tqdm
import argparse

def init_args():
    print("=> initializing input arguments...")
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_dir", type=str, required=True)
    parser.add_argument("--description", type=str, required=True)
    parser.add_argument("--hits", type=int, required=False, default=2)
    
    args = parser.parse_args()
    return args
    
def main():
    args = init_args()
    mesh_folder_path = args.input_dir
    description = args.description
    max_hits = args.hits
    
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
    
    
    
if __name__ == "__main__":
    main()