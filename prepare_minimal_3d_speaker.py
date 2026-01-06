"""
Prepare minimal 3D-Speaker with only necessary files
"""

import os
import shutil
from pathlib import Path

# Project paths
project_root = Path(__file__).parent
source_3d = project_root / "3D-Speaker"
target_3d = project_root / "3D-Speaker-minimal"

# Files and directories to copy
FILES_TO_COPY = [
    "speakerlab/models/campplus",
    "speakerlab/models/eres2net",
    "speakerlab/process/processor.py",
    "speakerlab/utils/builder.py",
    "speakerlab/utils/config.py",
    "speakerlab/utils/utils.py",
    "speakerlab/utils/checkpoint.py",
    "speakerlab/models/__init__.py",
    "speakerlab/process/__init__.py",
    "speakerlab/utils/__init__.py",
]

def copy_minimal():
    """Copy only necessary files to minimal directory"""
    
    if not source_3d.exists():
        print(f"Error: Source 3D-Speaker not found: {source_3d}")
        return
    
    print(f"Creating minimal 3D-Speaker at: {target_3d}")
    
    # Create target directories
    for item in FILES_TO_COPY:
        src_path = source_3d / item
        if not src_path.exists():
            print(f"Warning: {item} not found in source")
            continue
        
        dest_path = target_3d / item
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        if src_path.is_dir():
            print(f"Copying directory: {item}")
            shutil.copytree(src_path, dest_path)
        else:
            print(f"Copying file: {item}")
            shutil.copy2(src_path, dest_path)
    
    # Add README
    readme_content = """# 3D-Speaker (Minimal Version)

This is a minimal version of 3D-Speaker, keeping only the core files used by Lincoco voice chatbot.

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
    
    with open(target_3d / "README.md", "w", encoding="utf-8") as f:
        f.write(readme_content)
    
    print("\nMinimal 3D-Speaker created successfully!")
    
    # Calculate directory size
    total_size = sum(f.stat().st_size for f in target_3d.rglob('*') if f.is_file())
    print(f"\n3D-Speaker-minimal directory size: {total_size / 1024 / 1024:.2f} MB")

if __name__ == "__main__":
    copy_minimal()

