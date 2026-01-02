@echo off

REM 切换到脚本所在目录
cd /d "%~dp0"

REM 尝试多种方式激活 conda 环境
SET CONDA_PATH=.\Miniconda3
SET CONDA_ACTIVATED=0

REM 方式1: 检查本地 Miniconda3 是否存在
if exist "%CONDA_PATH%\Scripts\activate.bat" (
    echo 使用本地 Miniconda3: %CONDA_PATH%
    CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%"
    call conda activate voice
    SET CONDA_ACTIVATED=1
)

REM 方式2: 如果方式1失败，尝试使用系统 PATH 中的 conda
if %CONDA_ACTIVATED%==0 (
    where conda >nul 2>&1
    if %ERRORLEVEL%==0 (
        echo 使用系统 PATH 中的 conda
        call conda activate voice
        SET CONDA_ACTIVATED=1
    )
)

REM 方式3: 尝试使用 Anaconda3
if %CONDA_ACTIVATED%==0 (
    SET CONDA_PATH=.\Anaconda3
    if exist "%CONDA_PATH%\Scripts\activate.bat" (
        echo 使用本地 Anaconda3: %CONDA_PATH%
        CALL "%CONDA_PATH%\Scripts\activate.bat" "%CONDA_PATH%"
        call conda activate voice
        SET CONDA_ACTIVATED=1
    )
)

REM 如果所有方式都失败，提示用户
if %CONDA_ACTIVATED%==0 (
    echo.
    echo 警告: 无法找到 conda 环境
    echo 请确保:
    echo   1. Miniconda3 或 Anaconda3 已安装在当前目录，或
    echo   2. conda 已在系统 PATH 中
    echo.
    echo 尝试继续运行（如果 conda 环境已激活）...
    echo.
)

cmd /k