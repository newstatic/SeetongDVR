#!/bin/bash
set -e

REPO="newstatic/SeetongDVR"
INSTALL_DIR="$HOME/.local/bin"
BINARY_NAME="seetong-dvr"

# Detect architecture
ARCH=$(uname -m)
case $ARCH in
    arm64|aarch64)
        ASSET="seetong-dvr-darwin-arm64"
        ;;
    x86_64)
        ASSET="seetong-dvr-darwin-amd64"
        ;;
    *)
        echo "Unsupported architecture: $ARCH"
        exit 1
        ;;
esac

echo "Seetong DVR Web Player Installer"
echo "================================"
echo "Architecture: $ARCH"
echo "Downloading: $ASSET"
echo ""

# Create install directory
mkdir -p "$INSTALL_DIR"

# Download latest release
DOWNLOAD_URL="https://github.com/$REPO/releases/latest/download/$ASSET"
echo "Downloading from $DOWNLOAD_URL..."
curl -fsSL "$DOWNLOAD_URL" -o "$INSTALL_DIR/$BINARY_NAME"
chmod +x "$INSTALL_DIR/$BINARY_NAME"

echo ""
echo "Installed to: $INSTALL_DIR/$BINARY_NAME"
echo ""

# Add to PATH if needed
if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    echo "Add to your PATH:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
fi

# Run the player
echo "Starting Seetong DVR Player..."
"$INSTALL_DIR/$BINARY_NAME"
