#!/usr/bin/env bash
set -e

echo "=========================================================="
echo "⚡ MESHCORE NAVIGATOR - Linux Installer"
echo "   App by Nicky Proniewicz - M7NCY"
echo "=========================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 1. Check Python version
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
else
    echo "❌ Error: Python 3 was not found on this system. Please install Python 3.10+."
    exit 1
fi

PY_VER=$($PYTHON_CMD -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✓ Found Python $PY_VER"

# 2. Set up virtual environment
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    echo "Creating virtual environment in $SCRIPT_DIR/venv..."
    $PYTHON_CMD -m venv --system-site-packages "$SCRIPT_DIR/venv" 2>/dev/null || $PYTHON_CMD -m venv "$SCRIPT_DIR/venv"
fi

source "$SCRIPT_DIR/venv/bin/activate"

# 3. Upgrade pip and install package
echo "Installing dependencies..."
pip install --upgrade pip
# Install local vendored packages first to avoid upstream PyPI packages forcing obsolete builds
pip install -e "$SCRIPT_DIR/vendor/pixoo"
pip install -e "$SCRIPT_DIR/vendor/meshcore_py"
pip install -e .

# 4. Install Desktop Launcher binary to ~/.local/bin
mkdir -p "$HOME/.local/bin"
cat << EOF > "$HOME/.local/bin/meshcore-navigator"
#!/usr/bin/env bash
export QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --ozone-platform-hint=auto --enable-features=UseOzonePlatform,WaylandWindowDecorations --disable-gpu-watchdog"
export PYTHONPATH="$SCRIPT_DIR:$SCRIPT_DIR/vendor/pixoo/src:$SCRIPT_DIR/vendor/meshcore_py/src:\$PYTHONPATH"
exec "$SCRIPT_DIR/venv/bin/python" -m meshcore_tray.main "\$@"
EOF
chmod +x "$HOME/.local/bin/meshcore-navigator"
echo "✓ Installed launcher command: $HOME/.local/bin/meshcore-navigator"

# 5. Install Desktop Icon
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
mkdir -p "$ICON_DIR"
if [ -f "$SCRIPT_DIR/meshcore_tray/ui/static/icon.png" ]; then
    cp "$SCRIPT_DIR/meshcore_tray/ui/static/icon.png" "$ICON_DIR/meshcore-navigator.png"
    echo "✓ Installed application icon to $ICON_DIR/meshcore-navigator.png"
fi

# 6. Install Desktop Menu Entry
APP_DIR="$HOME/.local/share/applications"
mkdir -p "$APP_DIR"
cat << EOF > "$APP_DIR/meshcore-navigator.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=MESHCORE NAVIGATOR
GenericName=MeshCore LoRa Station & Live Map
Comment=MeshCore LoRa Station, Real-Time Interactive Mesh Map & Divoom Pixoo Matrix Integration
Icon=meshcore-navigator
Exec=$HOME/.local/bin/meshcore-navigator %u
Terminal=false
Categories=Network;HamRadio;Utility;
Keywords=meshcore;lora;heltec;radio;map;pixoo;m7ncy;
StartupNotify=true
StartupWMClass=MESHCORE NAVIGATOR
EOF
chmod +x "$APP_DIR/meshcore-navigator.desktop"
echo "✓ Installed desktop entry to $APP_DIR/meshcore-navigator.desktop"

# Update desktop database if tool exists
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
fi

echo ""
echo "🎉 Installation Complete!"
echo "You can launch MESHCORE NAVIGATOR from your application menu,"
echo "or run 'meshcore-navigator' in your terminal (ensure ~/.local/bin is in PATH)."
echo "App by Nicky Proniewicz - M7NCY"
