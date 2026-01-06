"""
Extract minimal 3D-Speaker files from git
"""

import subprocess
import shutil
from pathlib import Path

# Project paths
project_root = Path(__file__).parent
target_dir = project_root / "3D-Speaker-minimal"

# Files and directories to extract from git
FILES_TO_EXTRACT = [
    "3D-Speaker/speakerlab/models/campplus",
    "3D-Speaker/speakerlab/models/eres2net",
    "3D-Speaker/speakerlab/process/processor.py",
    "3D-Speaker/speakerlab/utils/builder.py",
    "3D-Speaker/speakerlab/utils/config.py",
    "3D-Speaker/speakerlab/utils/utils.py",
    "3D-Speaker/speakerlab/utils/checkpoint.py",
]

def extract_from_git():
    """Extract files from git"""
    
    print(f"Creating minimal 3D-Speaker at: {target_dir}")
    
    # Create target directory
    target_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy files from git
    for file_path in FILES_TO_EXTRACT:
        src = project_root / file_path
        
        # Get relative path for target
        rel_path = file_path.replace("3D-Speaker/", "")
        dest = target_dir / rel_path
        
        # Create parent directories
        dest.parent.mkdir(parents=True, exist_ok=True)
        
        # Use git checkout to extract file
        print(f"Extracting: {rel_path}")
        
        try:
            # Create directory structure
            if src.is_dir():
                # For directories, use git to get all files in the directory
                result = subprocess.run(
                    ["git", "checkout", "--", file_path],
                    cwd=project_root,
                    capture_output=True,
                    text=True
                )
                
                # Copy directory to target
                if src.exists():
                    shutil.copytree(src, dest)
            else:
                # For files, use git to get the file
                result = subprocess.run(
                    ["git", "checkout", "--", file_path],
                    cwd=project_root,
                    capture_output=True,
                    text=True
                )
                
                # Copy file to target
                if src.exists():
                    shutil.copy2(src, dest)
                    
        except Exception as e:
            print(f"  Error extracting {rel_path}: {e}")
    
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

This directory is integrated into Lincoco project and used via `voice_chatbot/asr/speaker_verification.py`.

## Original Project

For complete 3D-Speaker project, visit: https://github.com/alibaba-damo-academy/3D-Speaker
"""
    
    with open(target_dir / "README.md", "w", encoding="utf-8") as f:
        f.write(readme_content)
    
    # Create __init__.py files
    (target_dir / "speakerlab").mkdir(parents=True, exist_ok=True)
    (target_dir / "speakerlab" / "__init__.py").touch()
    (target_dir / "speakerlab" / "models").mkdir(parents=True, exist_ok=True)
    (target_dir / "speakerlab" / "models" / "__init__.py").touch()
    (target_dir / "speakerlab" / "process").mkdir(parents=True, exist_ok=True)
    (target_dir / "speakerlab" / "process" / "__init__.py").touch()
    (target_dir / "speakerlab" / "utils").mkdir(parents=True, exist_ok=True)
    (target_dir / "speakerlab" / "utils" / "__init__.py").touch()
    
    print("\nMinimal 3D-Speaker created successfully!")
    
    # Calculate directory size
    total_size = sum(f.stat().st_size for f in target_dir.rglob('*') if f.is_file())
    print(f"\n3D-Speaker-minimal directory size: {total_size / 1024 / 1024:.2f} MB")

if __name__ == "__main__":
    extract_from_git()

