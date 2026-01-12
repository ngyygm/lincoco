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

REM Define dataset folder name variable, please modify to your own path
set DATABASE_NAME=屠夫
echo Dataset folder name: %DATABASE_NAME%
echo A config file will be generated at melo/data/%DATABASE_NAME%/config.json
echo You can freely edit hyperparameters in this config file
echo For example, reduce batch_size if you encounter CUDA out of memory
echo.

REM Check if metadata file exists
if not exist "melo\data\%DATABASE_NAME%\metadata.list" (
    echo.
    echo ERROR: Cannot find metadata file melo\data\%DATABASE_NAME%\metadata.list
    echo Please ensure:
    echo   1. Dataset name %DATABASE_NAME% is correct
    echo   2. Dataset folder is created in melo\data\ directory
    echo   3. metadata.list file exists in dataset folder
    echo.
    pause
    exit /b 1
)

echo Preprocessing started...
REM Save parent directory path before entering melo folder
set PARENT_DIR=%CD%
cd melo
REM Set PYTHONPATH to parent directory to enable absolute imports like melo.text
set PYTHONPATH=%PARENT_DIR%;%PYTHONPATH%
python preprocess_text.py --metadata "data/%DATABASE_NAME%/metadata.list"
cd ..

echo.
echo Preprocessing completed. The processed dataset is saved in melo/data/%DATABASE_NAME%/
cmd /k
