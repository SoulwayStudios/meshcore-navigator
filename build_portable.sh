#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VERSION=$(python3 -c "import meshcore_tray; print(meshcore_tray.__version__)" 2>/dev/null || echo "0.0.2")
DIST_NAME="meshcore-navigator-v${VERSION}-linux"
BUILD_DIR="$SCRIPT_DIR/build/$DIST_NAME"

echo "Building portable Linux distribution package for $DIST_NAME..."
rm -rf "$SCRIPT_DIR/build/$DIST_NAME" "$SCRIPT_DIR/dist/${DIST_NAME}.tar.gz"
mkdir -p "$BUILD_DIR" "$SCRIPT_DIR/dist"

# Copy essential files
cp -r meshcore_tray "$BUILD_DIR/"
cp -r vendor "$BUILD_DIR/"
cp pyproject.toml README.md LICENSE CHANGELOG.md run.sh install.sh meshcore-navigator.desktop "$BUILD_DIR/"

# Remove any __pycache__ or temporary caches
find "$BUILD_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true

# Create tarball
cd "$SCRIPT_DIR/build"
tar -czf "$SCRIPT_DIR/dist/${DIST_NAME}.tar.gz" "$DIST_NAME"
cd "$SCRIPT_DIR"

echo "✓ Created release package: $SCRIPT_DIR/dist/${DIST_NAME}.tar.gz"
ls -lh "$SCRIPT_DIR/dist/${DIST_NAME}.tar.gz"
