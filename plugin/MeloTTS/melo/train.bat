@echo off

chcp 65001

REM 设置变量
set CONFIG=%1
set GPUS=%2
for %%i in ("%CONFIG%") do set MODEL_NAME=%%~ni

REM 设置分布式训练环境变量
REM 在Windows上，Gloo后端可能不可用，因此禁用分布式训练
REM 如果确实需要分布式训练，请设置 USE_DISTRIBUTED=1
set USE_DISTRIBUTED=0
set MASTER_ADDR=127.0.0.1
set MASTER_PORT=29500
set WORLD_SIZE=1
set RANK=0
set LOCAL_RANK=0
set PORT=10902
REM 禁用 libuv（Windows上Gloo后端需要）
set GLOO_USE_LIBUV=0 
 

:loop
REM 运行训练脚本，修改了原始的分布式训练脚本，单卡训练
python train.py --c %CONFIG% --m %MODEL_NAME%

REM 检查是否有残余进程并杀死它们
for /f "tokens=2" %%a in ('tasklist ^| findstr /C:"python" ^| findstr /C:"%CONFIG%"') do (
    echo Killing process %%a
    taskkill /F /PID %%a
)

REM 等待一段时间后重试
timeout /t 30 >nul
goto loop