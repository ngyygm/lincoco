#!/bin/bash

# ========================================
# Lincoco Voice Assistant - One-click setup and run
# ========================================

echo "========================================"
echo "Lincoco Voice Assistant"
echo "One-click Setup and Run Script"
echo "========================================"
echo ""

# Set virtual environment directory
VENV_DIR="venv"



run_program() {
    echo "========================================"
    echo "All packages are ready!"
    echo "========================================"
    echo ""
    echo "Virtual environment location: $(pwd)/$VENV_DIR"
    echo "Python version: 3.10"
    echo ""
    echo "[5/5] Starting main program..."
    echo ""
    echo "Press Ctrl+C to exit"
    echo "========================================"
    echo ""

    # Run main program
    python main.py

    # If program exits, pause to see error messages
    if [ $? -ne 0 ]; then
        echo ""
        echo "========================================"
        echo "Program exited abnormally"
        echo "========================================"
        read -p "Press Enter to exit..."
    fi
}


activate_and_install() {
    # Activate virtual environment
    echo "[3/5] Activating virtual environment..."
    source "$VENV_DIR/bin/activate"
    if [ $? -ne 0 ]; then
        echo "Failed to activate virtual environment"
        read -p "Press Enter to exit..."
        exit 1
    fi
    echo "Virtual environment activated"
    echo ""

    # Check if key packages are already installed
    echo "Checking installed packages..."
    MISSING_PACKAGES=0

    # Check critical packages
    for package in openai funasr edge-tts gradio fastapi torch; do
        # Convert package name to import name
        import_name=$(echo "$package" | sed 's/-/_/g')
        python -c "import importlib; importlib.import_module('$import_name')" 2>/dev/null
        if [ $? -ne 0 ]; then
            MISSING_PACKAGES=1
            echo "[MISSING] $package"
        else
            echo "[OK] $package"
        fi
    done

    # Check extra packages
    echo ""
    echo "Checking extra packages (vllm, transformers)..."
    python -c "import vllm" 2>/dev/null
    if [ $? -ne 0 ]; then
        MISSING_PACKAGES=1
        echo "[MISSING] vllm"
    else
        echo "[OK] vllm"
    fi

    python -c "import transformers" 2>/dev/null
    if [ $? -ne 0 ]; then
        MISSING_PACKAGES=1
        echo "[MISSING] transformers"
    else
        echo "[OK] transformers"
    fi

    echo ""

    # Decide whether to install or skip
    if [ $MISSING_PACKAGES -eq 0 ]; then
        echo "========================================"
        echo "All required packages are already installed!"
        echo "========================================"
        read -p "Do you want to reinstall/upgrade packages? This may take several minutes (y/N): " REINSTALL
        if [ "$REINSTALL" != "y" ] && [ "$REINSTALL" != "Y" ]; then
            echo "Skipping installation, starting program directly..."
            run_program
        fi
        echo "Proceeding with installation..."
    else
        echo "========================================"
        echo "Some packages are missing or not installed correctly."
        echo "Proceeding with installation..."
        echo "========================================"
    fi
    echo ""

    # Upgrade pip to latest version
    echo "Upgrading pip to latest version..."
    python -m pip install --upgrade pip
    if [ $? -ne 0 ]; then
        echo "Failed to upgrade pip"
        read -p "Press Enter to exit..."
        exit 1
    fi
    echo "pip upgraded successfully"
    echo ""

    # Configure pip to use Tsinghua mirror
    echo "Configuring pip to use Tsinghua mirror..."
    pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
    pip config set install.trusted-host pypi.tuna.tsinghua.edu.cn
    echo "Pip mirror configured successfully"
    echo ""

    # Install requirements.txt
    echo "Installing project dependencies (requirements.txt)..."
    pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "Failed to install requirements.txt"
        read -p "Press Enter to exit..."
        exit 1
    fi
    echo "Project dependencies installed successfully"
    echo ""

    # Install extra packages using Aliyun mirror
    echo "[4/5] Installing extra dependencies..."
    echo "Installing vllm and transformers (using Aliyun mirror)..."
    pip install vllm==v0.11.0 transformers==4.57.1 numpy==1.26.4 -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host=mirrors.aliyun.com
    if [ $? -ne 0 ]; then
        echo "Failed to install extra dependencies"
        read -p "Press Enter to exit..."
        exit 1
    fi
    echo "Extra dependencies installed successfully"
    echo ""

    run_program
}




# Check if virtual environment already exists
echo "[1/5] Checking virtual environment..."
if [ -d "$VENV_DIR" ]; then
    echo "Virtual environment \"$VENV_DIR\" already exists"
    echo "Using existing virtual environment, skipping creation step"
    activate_and_install
else
    # Create virtual environment
    echo "[2/5] Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then
        echo "Failed to create virtual environment"
        echo "Please ensure Python 3.10 is installed"
        read -p "Press Enter to exit..."
        exit 1
    fi
    echo "Virtual environment created successfully"
    echo ""
    activate_and_install
fi

