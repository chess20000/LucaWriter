@echo off
chcp 936 >nul 2>&1
setlocal enabledelayedexpansion

echo ============================================
echo   LucaWriter v1.1.5 Build Script
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
    echo [ERROR] Python not found. Please install Python 3.8+
    pause
    exit /b 1
)
echo Python: %PYTHON_EXE%

node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js not found. Please install Node.js 18+
    pause
    exit /b 1
)

REM ---- Set Paths ----
set "ROOT_DIR=%~dp0"
set "ELECTRON_DIR=%ROOT_DIR%electron"
set "DIST_BACKEND=%ELECTRON_DIR%\dist-backend"
set "DIST_BUILTIN=%ELECTRON_DIR%\dist-builtin"
set "BUILD_TEMP=%ELECTRON_DIR%\build-temp"

echo [1/8] Cleaning old build files...
if exist "%DIST_BACKEND%" rmdir /s /q "%DIST_BACKEND%"
if exist "%DIST_BUILTIN%" rmdir /s /q "%DIST_BUILTIN%"
if exist "%BUILD_TEMP%" rmdir /s /q "%BUILD_TEMP%"
if exist "%ROOT_DIR%release\v1.1.5" rmdir /s /q "%ROOT_DIR%release\v1.1.5"
echo Clean done.
echo.

echo [2/8] Cleaning pip cache and installing Python dependencies...
%PYTHON_EXE% -m pip cache purge >nul 2>&1
%PYTHON_EXE% -m pip install -r "%ROOT_DIR%requirements.txt" --quiet --no-cache-dir
if errorlevel 1 (
    echo [ERROR] Failed to install Python dependencies
    pause
    exit /b 1
)
%PYTHON_EXE% -m pip install pyinstaller Pillow --quiet --no-cache-dir
if errorlevel 1 (
    echo [ERROR] Failed to install PyInstaller/Pillow
    pause
    exit /b 1
)
echo Python dependencies installed.
echo.

echo [3/8] Generating app icon...
cd /d "%ROOT_DIR%"
%PYTHON_EXE% electron\make_icon.py
if errorlevel 1 (
    echo [WARN] Icon generation failed, using default icon
)
echo Icon done.
echo.

echo [4/8] Building backend with PyInstaller...
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
    "%ROOT_DIR%backend\main.py"
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed
    pause
    exit /b 1
)
echo Backend build done.
echo.

echo [5/8] Copying builtin books...
mkdir "%DIST_BUILTIN%" 2>nul
copy "%ROOT_DIR%LUCA_Legend.md" "%DIST_BUILTIN%\" >nul
if errorlevel 1 (
    echo [ERROR] Failed to copy builtin book
    pause
    exit /b 1
)
echo Builtin books copied.
echo.

echo [6/8] Installing npm dependencies...
cd /d "%ELECTRON_DIR%"
set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
call npm install
if errorlevel 1 (
    echo [ERROR] npm install failed
    cd /d "%ROOT_DIR%"
    pause
    exit /b 1
)
echo npm dependencies installed.
echo.

echo [7/8] Packaging with electron-builder...
call npx electron-builder --win
if errorlevel 1 (
    echo [ERROR] electron-builder failed
    cd /d "%ROOT_DIR%"
    pause
    exit /b 1
)
cd /d "%ROOT_DIR%"
echo Packaging done.
echo.

echo [8/8] Cleaning temp files...
if exist "%BUILD_TEMP%" rmdir /s /q "%BUILD_TEMP%"
echo Temp files cleaned.
echo.

echo ============================================
echo   Build Success!
echo   Output: release\v1.1.5\
echo ============================================
pause
