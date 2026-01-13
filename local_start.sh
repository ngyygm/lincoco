#!/bin/bash

# ========================================
# Lincoco Voice Assistant - Quick Start
# ========================================

echo "========================================"
echo "Lincoco Voice Assistant"
echo "Quick Start Script"
echo "========================================"
echo ""

# Set virtual environment directory
VENV_DIR="venv"

# Check if virtual environment exists
echo "[1/2] Checking virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    echo "ERROR: Virtual environment \"$VENV_DIR\" not found!"
    echo ""
    echo "Please run setup_and_run.sh first to set up the environment."
    read -p "Press Enter to exit..."
    exit 1
fi
echo "Virtual environment \"$VENV_DIR\" found"
echo ""

# Activate virtual environment
echo "[2/2] Activating virtual environment..."
source "$VENV_DIR/bin/activate"
if [ $? -ne 0 ]; then
    echo "Failed to activate virtual environment"
    read -p "Press Enter to exit..."
    exit 1
fi
echo "Virtual environment activated"
echo ""

# Run main program
echo "========================================"
echo "Starting Lincoco Voice Assistant..."
echo "Press Ctrl+C to exit"
echo "========================================"
echo ""

python main.py

# If program exits, pause to see error messages
if [ $? -ne 0 ]; then
    echo ""
    echo "========================================"
    echo "Program exited abnormally"
    echo "========================================"
    read -p "Press Enter to exit..."
fi
