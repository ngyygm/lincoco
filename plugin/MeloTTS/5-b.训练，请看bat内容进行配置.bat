@echo off

chcp 65001

cd /d "%~dp0"

set HF_ENDPOINT=https://hf-mirror.com
set HF_HOME=%CD%\hf_download

SET CONDA_PATH=.\Miniconda3
SET CONDA_ACTIVATED=0

if exist "%CONDA_PATH%\Scripts\activate.bat" (
    echo Using local Miniconda3: %CONDA_PATH%
    CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%"
    call conda activate voice
    SET CONDA_ACTIVATED=1
)

if %CONDA_ACTIVATED%==0 (
    where conda >nul 2>&1
    if %ERRORLEVEL%==0 (
        echo Using conda from system PATH
        call conda activate voice
        SET CONDA_ACTIVATED=1
    )
)

if %CONDA_ACTIVATED%==0 (
    SET CONDA_PATH=.\Anaconda3
    if exist "%CONDA_PATH%\Scripts\activate.bat" (
        echo Using local Anaconda3: %CONDA_PATH%
        CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%"
        call conda activate voice
        SET CONDA_ACTIVATED=1
    )
)

if %CONDA_ACTIVATED%==0 (
    echo.
    echo WARNING: Cannot find conda environment
    echo Please ensure:
    echo   1. Miniconda3 or Anaconda3 is installed in current directory, or
    echo   2. conda is in system PATH
    echo.
    echo Attempting to continue...
    echo.
)

REM Save parent directory path before entering melo folder
set PARENT_DIR=%CD%
echo Enter melo folder
cd melo

set DATABASE_NAME=屠夫

echo ========================================
echo Dataset folder name: %DATABASE_NAME%
echo Config file path: data/%DATABASE_NAME%/config.json
echo ========================================
echo.
echo Usage instructions:
echo   1. Make sure dataset is prepared in data/%DATABASE_NAME%/ directory
echo   2. Config file is located at data/%DATABASE_NAME%/config.json
echo   3. Training parameters can be modified in config file
echo   4. Default save every 1000 steps
echo   5. Default training 10000 Epochs
echo.

REM Check if config file exists
if not exist "data\%DATABASE_NAME%\config.json" (
    echo.
    echo ERROR: Cannot find config file data\%DATABASE_NAME%\config.json
    echo Please ensure:
    echo   1. Dataset name %DATABASE_NAME% is correct
    echo   2. Dataset folder is created in data\ directory
    echo   3. config.json file exists in dataset folder
    echo.
    pause
    exit /b 1
)

echo Config file check passed, starting training...
echo.
REM Set PYTHONPATH to parent directory to enable absolute imports like melo.text
set PYTHONPATH=%PARENT_DIR%;%PYTHONPATH%
call train.bat "data/%DATABASE_NAME%/config.json" 1

echo Training completed, model saved in data/%DATABASE_NAME%/
cmd /k
