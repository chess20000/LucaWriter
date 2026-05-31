@echo off
chcp 936 >nul 2>&1
setlocal enabledelayedexpansion

echo ============================================
echo   LucaWriter v1.2.0c Community Build Script
echo ============================================
echo.

REM ---- Check Environment ----
set "PYTHON_EXE="
if exist "%USERPROFILE%\.pyenv\pyenv-win\versions\3.12.9\python.exe" (
    set "PYTHON_EXE=%USERPROFILE%\.pyenv\pyenv-win\versions\3.12.9\python.exe"
)
if "%PYTHON_EXE%"=="" (
    python --version >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=python"
)
if "%PYTHON_EXE%"=="" (
    echo [ERROR] Python not found.
    pause
    exit /b 1
)
echo Python: %PYTHON_EXE%

node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js not found.
    pause
    exit /b 1
)

REM ---- Set Paths ----
set "ROOT_DIR=%~dp0"
set "ELECTRON_DIR=%ROOT_DIR%electron"
set "DIST_BACKEND=%ELECTRON_DIR%\dist-backend"
set "DIST_BUILTIN=%ELECTRON_DIR%\dist-builtin"
set "BUILD_TEMP=%ELECTRON_DIR%\build-temp"

echo [1/5] Backing up package.json...
copy "%ELECTRON_DIR%\package.json" "%ELECTRON_DIR%\package.json.fullbak" >nul
copy "%ELECTRON_DIR%\package_c.json" "%ELECTRON_DIR%\package.json" >nul
echo Done.

echo [2/5] Checking backend build...
if not exist "%DIST_BACKEND%\LucaWriterBackend\LucaWriterBackend.exe" (
    echo Building backend with PyInstaller...
    %PYTHON_EXE% -m pip install pyinstaller Pillow --quiet --no-cache-dir
    %PYTHON_EXE% -m PyInstaller --onedir --noconsole ^
        --name LucaWriterBackend ^
        --distpath "%DIST_BACKEND%" ^
        --workpath "%BUILD_TEMP%" ^
        --specpath "%BUILD_TEMP%" ^
        --noconfirm ^
        --hidden-import docx ^
        --hidden-import PyPDF2 ^
        --hidden-import ebooklib ^
        --hidden-import ebooklib.epub ^
        --collect-all certifi ^
        --exclude-module torch ^
        --exclude-module torchvision ^
        --exclude-module torchaudio ^
        --exclude-module torchsde ^
        --exclude-module torchao ^
        --exclude-module safetensors ^
        --exclude-module tensorboard ^
        "%ROOT_DIR%backend\main.py"
    if errorlevel 1 (
        echo [ERROR] PyInstaller build failed
        pause
        exit /b 1
    )
) else (
    echo Backend already built, skipping PyInstaller.
)
echo Done.

echo [3/5] Copying builtin books...
mkdir "%DIST_BUILTIN%" 2>nul
copy "%ROOT_DIR%LUCA_Legend.md" "%DIST_BUILTIN%\" >nul
echo Done.

echo [4/5] Packaging with electron-builder...
cd /d "%ELECTRON_DIR%"
call npx electron-builder --win
if errorlevel 1 (
    echo [ERROR] electron-builder failed
    cd /d "%ROOT_DIR%"
    pause
    exit /b 1
)
cd /d "%ROOT_DIR%"
echo Packaging done.

echo [5/5] Restoring package.json...
copy "%ELECTRON_DIR%\package.json.fullbak" "%ELECTRON_DIR%\package.json" >nul
del "%ELECTRON_DIR%\package.json.fullbak"
echo Done.

echo ============================================
echo   Build Success!
echo   Output: release\v1.2.0c\
echo ============================================
pause
