@echo off

chcp 65001

SET CONDA_PATH=.\Miniconda3
set HF_ENDPOINT=https://hf-mirror.com
set HF_HOME=%CD%\hf_download


REM Activate voice environment
CALL %CONDA_PATH%\Scripts\activate.bat %CONDA_PATH%
call conda activate voice

start http://127.0.0.1:8102/docs

python api_onnx.py

cmd /k
