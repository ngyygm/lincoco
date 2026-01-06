"""
Clean up 3D-Speaker, keep only core files needed by the project
"""

import os
import shutil
from pathlib import Path

# Project root
project_root = Path(__file__).parent
speaker3d_path = project_root / "3D-Speaker"

# Delete unnecessary directories
DELETE_DIRS = [
    "egs",
    "runtime",
    "docs",
    "speakerlab/bin",
    "speakerlab/dataset",
    "speakerlab/loss",
    "speakerlab/models/ecapa_tdnn",
    "speakerlab/models/rdino",
    "speakerlab/models/res2net",
    "speakerlab/models/resnet",
    "speakerlab/models/sdpn",
    "speakerlab/models/talknet",
    "speakerlab/models/xvector",
]

# Delete unnecessary files
DELETE_FILES = [
    "3D-Speaker-logo.png",
    ".gitignore",
    ".pre-commit-config.yaml",
    "requirements.txt",
]

def cleanup():
    """Clean up 3D-Speaker directory"""
    
    if not speaker3d_path.exists():
        print(f"Error: 3D-Speaker directory not found: {speaker3d_path}")
        return
    
    print(f"Starting cleanup of 3D-Speaker directory: {speaker3d_path}")
    
    # Delete unnecessary directories
    for dir_name in DELETE_DIRS:
        dir_path = speaker3d_path / dir_name
        if dir_path.exists():
            if dir_path.is_dir():
                print(f"Deleting directory: {dir_name}")
                shutil.rmtree(dir_path)
    
    # Delete unnecessary files
    for file_name in DELETE_FILES:
        file_path = speaker3d_path / file_name
        if file_path.exists():
            print(f"Deleting file: {file_name}")
            file_path.unlink()
    
    # Delete speakerlab/process files except processor.py
    process_dir = speaker3d_path / "speakerlab" / "process"
    if process_dir.exists():
        for item in process_dir.iterdir():
            if item.is_file() and item.name != "processor.py":
                print(f"Deleting process/{item.name}")
                item.unlink()
    
    # Delete speakerlab/utils files except builder.py, config.py, utils.py, checkpoint.py
    utils_dir = speaker3d_path / "speakerlab" / "utils"
    if utils_dir.exists():
        utils_keep = {"builder.py", "config.py", "utils.py", "checkpoint.py", "__init__.py"}
        for item in utils_dir.iterdir():
            if item.is_file() and item.name not in utils_keep:
                print(f"Deleting utils/{item.name}")
                item.unlink()
    
    # Delete speakerlab/models except campplus and eres2net
    models_dir = speaker3d_path / "speakerlab" / "models"
    if models_dir.exists():
        models_keep = {"campplus", "eres2net", "__init__.py"}
        for item in models_dir.iterdir():
            if item.name not in models_keep:
                print(f"Deleting models/{item.name}")
                shutil.rmtree(item) if item.is_dir() else item.unlink()
    
    # Add README
    readme_content = """# 3D-Speaker (Simplified Version)

This is a simplified version of 3D-Speaker, keeping only the core files used by the Lincoco voice chatbot project.

## Kept Files

- `speakerlab/models/campplus/` - CAMPPlus speaker recognition model
- `speakerlab/models/eres2net/` - ERes2Net/ERes2NetV2 speaker recognition models
- `speakerlab/process/processor.py` - Audio processor (FBank)
- `speakerlab/utils/builder.py` - Model dynamic import utility
- `speakerlab/utils/config.py` - Configuration utility
- `speakerlab/utils/utils.py` - Common utility functions
- `speakerlab/utils/checkpoint.py` - Model checkpoint loader utility

## Usage

This directory is integrated into the Lincoco project and used via `voice_chatbot/asr/speaker_verification.py`.

## Original Project

For the complete 3D-Speaker project, visit: https://github.com/alibaba-damo-academy/3D-Speaker
"""
    
    with open(speaker3d_path / "README.md", "w", encoding="utf-8") as f:
        f.write(readme_content)
    
    # Remove .git directory if it exists
    git_dir = speaker3d_path / ".git"
    if git_dir.exists():
        print(f"Deleting .git directory")
        shutil.rmtree(git_dir)
    
    print("\nCleanup completed!")
    
    # Calculate directory size
    total_size = sum(f.stat().st_size for f in speaker3d_path.rglob('*') if f.is_file())
    print(f"\n3D-Speaker directory size: {total_size / 1024 / 1024:.2f} MB")

if __name__ == "__main__":
    cleanup()
