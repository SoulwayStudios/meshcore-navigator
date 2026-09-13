#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
fi

export PYTHONPATH="$SCRIPT_DIR:$SCRIPT_DIR/vendor/pixoo/src:$SCRIPT_DIR/vendor/meshcore_py/src:$PYTHONPATH"
if [ -z "$QT_QPA_PLATFORM" ]; then
    export QT_QPA_PLATFORM=xcb
fi
if [ -z "$QTWEBENGINE_CHROMIUM_FLAGS" ]; then
    export QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox"
fi

exec python3 -m meshcore_tray.main "$@"

