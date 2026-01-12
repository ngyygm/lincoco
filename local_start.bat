@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

:: ========================================
:: Lincoco Voice Assistant - Quick Start
:: ========================================

echo ========================================
echo Lincoco Voice Assistant
echo Quick Start Script
echo ========================================
echo.

:: Set virtual environment directory
set VENV_DIR=venv

:: Check if virtual environment exists
echo [1/2] Checking virtual environment...
if not exist "%VENV_DIR%" (
    echo ERROR: Virtual environment "%VENV_DIR%" not found!
    echo.
    echo Please run setup_and_run.bat first to set up the environment.
    pause
    exit /b 1
)
echo Virtual environment "%VENV_DIR%" found
echo.

:: Activate virtual environment
echo [2/2] Activating virtual environment...
call "%VENV_DIR%\Scripts\activate.bat"
if %errorlevel% neq 0 (
    echo Failed to activate virtual environment
    pause
    exit /b 1
)
echo Virtual environment activated
echo.

:: Run main program
echo ========================================
echo Starting Lincoco Voice Assistant...
echo Press Ctrl+C to exit
echo ========================================
echo.

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
