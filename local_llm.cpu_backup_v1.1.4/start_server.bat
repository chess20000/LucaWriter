@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "MODEL=%~dp0models\NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf"
set "HOST=127.0.0.1"
set "PORT=8080"
set "CTX=131072"

if not exist "%MODEL%" (
    echo [错误] 找不到模型文件: %MODEL%
    echo 请检查模型文件是否已放入 models 文件夹。
    pause
    exit /b 1
)

echo ==========================================
echo  LucaWriter 本地 Llama.cpp 服务器
echo  模型: NVIDIA Nemotron 3 Nano 4B
echo  地址: http://%HOST%:%PORT%
echo  仅允许本机访问 (127.0.0.1)
echo ==========================================
echo  按 Ctrl+C 停止服务器
echo.

llama-server.exe -m "%MODEL%" --host %HOST% --port %PORT% -c %CTX% -np 1 --timeout 300
