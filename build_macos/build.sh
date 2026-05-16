#!/bin/bash
set -euo pipefail

echo "============================================"
echo "  LucaWriter v1.1.0 macOS Build Script"
echo "============================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ELECTRON_DIR="$ROOT_DIR/electron"
DIST_BACKEND="$ELECTRON_DIR/dist-backend"
DIST_BUILTIN="$ELECTRON_DIR/dist-builtin"
BUILD_TEMP="$ELECTRON_DIR/build-temp"
RELEASE_DIR="$ROOT_DIR/release/v1.1.0"
VENV_DIR="$SCRIPT_DIR/venv"

PYTHON_EXE="${PYTHON_EXE:-python3}"

echo "[1/9] Checking environment..."

if ! command -v "$PYTHON_EXE" &>/dev/null; then
    echo "[ERROR] Python not found. Please install Python 3.10+"
    exit 1
fi
echo "  Python: $($PYTHON_EXE --version)"

if ! command -v node &>/dev/null; then
    echo "[ERROR] Node.js not found. Please install Node.js 18+"
    exit 1
fi
echo "  Node: $(node --version)"

if ! command -v npm &>/dev/null; then
    echo "[ERROR] npm not found."
    exit 1
fi
echo "  npm: $(npm --version)"
echo ""

echo "[2/9] Setting up Python virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    $PYTHON_EXE -m venv "$VENV_DIR" || {
        echo "[ERROR] Failed to create virtual environment"
        exit 1
    }
    echo "  Virtual environment created."
else
    echo "  Virtual environment already exists."
fi
source "$VENV_DIR/bin/activate"
PYTHON_EXE="python"
echo ""

echo "[3/9] Cleaning old build files..."
rm -rf "$DIST_BACKEND"
rm -rf "$DIST_BUILTIN"
rm -rf "$BUILD_TEMP"
rm -rf "$RELEASE_DIR"
rm -rf "$ELECTRON_DIR/.pyinstaller_cache"
echo "  Clean done."
echo ""

echo "[4/9] Installing Python dependencies..."
pip install -r "$ROOT_DIR/requirements.txt" --quiet --no-cache-dir || {
    echo "[ERROR] Failed to install Python dependencies"
    exit 1
}
pip install pyinstaller Pillow --quiet --no-cache-dir || {
    echo "[ERROR] Failed to install PyInstaller/Pillow"
    exit 1
}
echo "  Python dependencies installed."
echo ""

echo "[5/9] Generating app icons..."
cd "$SCRIPT_DIR"
$PYTHON_EXE make_icns.py || {
    echo "[WARN] Icon generation failed, using default icon"
}
cd "$ELECTRON_DIR"
$PYTHON_EXE make_icon.py || {
    echo "[WARN] Electron icon generation failed"
}
echo "  Icons done."
echo ""

echo "[6/9] Building backend with PyInstaller..."
export PYINSTALLER_CONFIG_DIR="$ELECTRON_DIR/.pyinstaller_cache"
$PYTHON_EXE -m PyInstaller --onedir \
    --noconsole \
    --name LucaWriterBackend \
    --distpath "$DIST_BACKEND" \
    --workpath "$BUILD_TEMP" \
    --specpath "$BUILD_TEMP" \
    --noconfirm \
    --hidden-import docx \
    --hidden-import PyPDF2 \
    --hidden-import ebooklib \
    --hidden-import ebooklib.epub \
    --collect-all certifi \
    "$ROOT_DIR/backend/main.py" || {
    echo "[ERROR] PyInstaller build failed"
    exit 1
}
echo "  Backend build done."
echo ""

echo "[7/9] Copying builtin books..."
mkdir -p "$DIST_BUILTIN"
cp "$ROOT_DIR/builtin/LUCA_Legend.md" "$DIST_BUILTIN/" 2>/dev/null || {
    cp "$ROOT_DIR/LUCA_Legend.md" "$DIST_BUILTIN/" 2>/dev/null || {
        echo "[WARN] No builtin book found, skipping"
    }
}
echo "  Builtin books copied."
echo ""

echo "[8/9] Installing npm dependencies..."
cd "$ELECTRON_DIR"
export ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/"
export NODE_TLS_REJECT_UNAUTHORIZED=0
npm install || {
    echo "[ERROR] npm install failed"
    exit 1
}
echo "  npm dependencies installed."
echo ""

echo "[9/9] Packaging with electron-builder (macOS DMG)..."
npx electron-builder --mac || {
    echo "[ERROR] electron-builder failed"
    exit 1
}
echo "  Packaging done."
echo ""

echo "Cleaning temp files..."
rm -rf "$BUILD_TEMP"
rm -rf "$ELECTRON_DIR/.pyinstaller_cache"
echo ""

echo "============================================"
echo "  Build Success!"
echo "  Output: $RELEASE_DIR"
echo ""
echo "  DMG files:"
ls -lh "$RELEASE_DIR"/*.dmg 2>/dev/null || echo "  (no DMG found)"
echo "============================================"
