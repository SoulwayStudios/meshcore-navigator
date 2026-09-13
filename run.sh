#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
fi

export PYTHONPATH="$SCRIPT_DIR:$SCRIPT_DIR/vendor/pixoo/src:$SCRIPT_DIR/vendor/meshcore_py/src:$PYTHONPATH"
if [ -z "$QT_QPA_PLATFORM" ]; then
    export QT_QPA_PLATFORM="wayland;xcb"
fi
if [ -z "$QTWEBENGINE_CHROMIUM_FLAGS" ]; then
    export QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox"
fi

# If NVIDIA driver/library mismatch is detected after a package upgrade, route GLX to mesa so GPU acceleration remains active without crashing
if [ -z "$__GLX_VENDOR_LIBRARY_NAME" ]; then
    if command -v nvidia-smi &>/dev/null && nvidia-smi 2>&1 | grep -qi "mismatch"; then
        export __GLX_VENDOR_LIBRARY_NAME=mesa
    fi
fi

exec python3 -m meshcore_tray.main "$@"

