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

REM Define dataset folder name variable, please modify to your own path
set DATABASE_NAME=屠夫
echo Dataset folder name: %DATABASE_NAME%
echo.
echo Configuration instructions:
echo   1. Configure the text to be synthesized
echo   2. Configure model path (trained model in logs/%DATABASE%/)
echo   3. Configure output directory
echo.
echo Example command:
echo   python infer.py -t "this is the text to be synthesized" -l EN -m logs/%DATABASE%/G_1000.pth -o out
echo.
echo Running inference with example text...
echo.

REM Check if model file exists
set MODEL_PATH=logs\%DATABASE_NAME%\G_1000.pth
if not exist "%MODEL_PATH%" (
    echo WARNING: Model file not found: %MODEL_PATH%
    echo The script will try to run anyway, but may fail.
    echo Please check your training output and adjust the model path if needed.
    echo.
)

REM Set PYTHONPATH to parent directory to enable absolute imports like melo.text
set PYTHONPATH=%PARENT_DIR%;%PYTHONPATH%
python infer.py -t "this is the text to be synthesized" -l EN -m "logs/%DATABASE_NAME%/G_1000.pth" -o out

echo.
echo Inference completed. Check the 'out' directory for output files.
cmd /k
