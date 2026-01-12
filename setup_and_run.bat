@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

:: ========================================
:: Lincoco Voice Assistant - One-click setup and run
:: ========================================

echo ========================================
echo Lincoco Voice Assistant
echo One-click Setup and Run Script
echo ========================================
echo.

:: Set virtual environment directory
set VENV_DIR=venv

:: Check if virtual environment already exists
echo [1/5] Checking virtual environment...
if exist "%VENV_DIR%" (
    echo Virtual environment "%VENV_DIR%" already exists
    echo Using existing virtual environment, skipping creation step
    goto :activate_and_install
)

:: Create virtual environment
echo [2/5] Creating virtual environment...
python -m venv "%VENV_DIR%"
if %errorlevel% neq 0 (
    echo Failed to create virtual environment
    echo Please ensure Python 3.10 is installed
    pause
    exit /b 1
)
echo Virtual environment created successfully
echo.

:activate_and_install
:: Activate virtual environment
echo [3/5] Activating virtual environment...
call "%VENV_DIR%\Scripts\activate.bat"
if %errorlevel% neq 0 (
    echo Failed to activate virtual environment
    pause
    exit /b 1
)
echo Virtual environment activated
echo.

:: Check if key packages are already installed
echo Checking installed packages...
set MISSING_PACKAGES=0

:: Check critical packages
for %%P in (openai funasr edge-tts gradio fastapi torch) do (
    python -c "import importlib; importlib.import_module('%%P.replace('-', '_')')" >nul 2>&1
    if %errorlevel% neq 0 (
        set MISSING_PACKAGES=1
        echo [MISSING] %%P
    ) else (
        echo [OK] %%P
    )
)

:: Check extra packages
echo.
echo Checking extra packages (vllm, transformers)...
python -c "import vllm" >nul 2>&1
if %errorlevel% neq 0 (
    set MISSING_PACKAGES=1
    echo [MISSING] vllm
) else (
    echo [OK] vllm
)

python -c "import transformers" >nul 2>&1
if %errorlevel% neq 0 (
    set MISSING_PACKAGES=1
    echo [MISSING] transformers
) else (
    echo [OK] transformers
)

echo.

:: Decide whether to install or skip
if %MISSING_PACKAGES%==0 (
    echo ========================================
    echo All required packages are already installed!
    echo ========================================
    set /p REINSTALL="Do you want to reinstall/upgrade packages? This may take several minutes (y/N): "
    if /i not "!REINSTALL!"=="y" (
        echo Skipping installation, starting program directly...
        goto :run_program
    )
    echo Proceeding with installation...
) else (
    echo ========================================
    echo Some packages are missing or not installed correctly.
    echo Proceeding with installation...
    echo ========================================
)
echo.

:: Upgrade pip to latest version
echo Upgrading pip to latest version...
python -m pip install --upgrade pip
if %errorlevel% neq 0 (
    echo Failed to upgrade pip
    pause
    exit /b 1
)
echo pip upgraded successfully
echo.

:: Configure pip to use Tsinghua mirror
echo Configuring pip to use Tsinghua mirror...
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
pip config set install.trusted-host pypi.tuna.tsinghua.edu.cn
echo Pip mirror configured successfully
echo.

:: Install requirements.txt
echo Installing project dependencies (requirements.txt)...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo Failed to install requirements.txt
    pause
    exit /b 1
)
echo Project dependencies installed successfully
echo.

:: Install extra packages using Aliyun mirror
echo [4/5] Installing extra dependencies...
echo Installing vllm and transformers (using Aliyun mirror)...
pip install vllm==v0.11.0 transformers==4.57.1 numpy==1.26.4 -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host=mirrors.aliyun.com
if %errorlevel% neq 0 (
    echo Failed to install extra dependencies
    pause
    exit /b 1
)
echo Extra dependencies installed successfully
echo.

:run_program
echo ========================================
echo All packages are ready!
echo ========================================
echo.
echo Virtual environment location: %CD%\%VENV_DIR%
echo Python version: 3.10
echo.
echo [5/5] Starting main program...
echo.
echo Press Ctrl+C to exit
echo ========================================
echo.

:: Run main program
python main.py

:: If program exits, pause to see error messages
if %errorlevel% neq 0 (
    echo.
    echo ========================================
    echo Program exited abnormally, error code: %errorlevel%
    echo ========================================
    pause
)

endlocal
