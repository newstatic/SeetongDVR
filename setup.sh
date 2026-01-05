#!/bin/bash
#
# Seetong DVR Web Viewer - One-click Setup Script
# https://github.com/newstatic/SeetongDVR
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║          Seetong DVR Web Viewer Setup                    ║"
echo "║          天视通 DVR 网页播放器部署脚本                     ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check OS
OS="$(uname -s)"
case "${OS}" in
    Linux*)     PLATFORM=Linux;;
    Darwin*)    PLATFORM=Mac;;
    *)          PLATFORM="UNKNOWN"
esac

echo -e "${YELLOW}Detected platform: ${PLATFORM}${NC}"

# ============================================================
# Step 1: Check Python
# ============================================================
echo -e "\n${BLUE}[1/5] Checking Python...${NC}"

if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    echo -e "${GREEN}✓ Python ${PYTHON_VERSION} found${NC}"
else
    echo -e "${RED}✗ Python 3 not found${NC}"
    echo "Please install Python 3.9 or later:"
    if [ "$PLATFORM" = "Mac" ]; then
        echo "  brew install python3"
    else
        echo "  sudo apt install python3 python3-pip python3-venv"
    fi
    exit 1
fi

# ============================================================
# Step 2: Check Node.js
# ============================================================
echo -e "\n${BLUE}[2/5] Checking Node.js...${NC}"

if command -v node &> /dev/null; then
    NODE_VERSION=$(node --version)
    echo -e "${GREEN}✓ Node.js ${NODE_VERSION} found${NC}"
else
    echo -e "${RED}✗ Node.js not found${NC}"
    echo "Please install Node.js 18 or later:"
    if [ "$PLATFORM" = "Mac" ]; then
        echo "  brew install node"
    else
        echo "  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -"
        echo "  sudo apt install nodejs"
    fi
    exit 1
fi

# ============================================================
# Step 3: Setup Python virtual environment
# ============================================================
echo -e "\n${BLUE}[3/5] Setting up Python environment...${NC}"

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

echo "Activating virtual environment..."
source .venv/bin/activate

echo "Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo -e "${GREEN}✓ Python environment ready${NC}"

# ============================================================
# Step 4: Build frontend
# ============================================================
echo -e "\n${BLUE}[4/5] Building frontend...${NC}"

cd web

if [ ! -d "node_modules" ]; then
    echo "Installing npm dependencies..."
    npm install --silent
fi

echo "Building production bundle..."
npm run build --silent

cd ..

echo -e "${GREEN}✓ Frontend built${NC}"

# ============================================================
# Step 5: Extract OCR training data (optional)
# ============================================================
echo -e "\n${BLUE}[5/5] Setting up OCR data...${NC}"

if [ -f "tesseract_train/ground-truth.7z" ] && [ ! -d "tesseract_train/ground-truth" ]; then
    if command -v 7z &> /dev/null; then
        echo "Extracting OCR training data..."
        cd tesseract_train
        7z x ground-truth.7z -oground-truth -y > /dev/null
        cd ..
        echo -e "${GREEN}✓ OCR data extracted${NC}"
    else
        echo -e "${YELLOW}⚠ 7z not found, skipping OCR data extraction${NC}"
        echo "  Install with: brew install p7zip (Mac) or apt install p7zip-full (Linux)"
    fi
else
    echo -e "${GREEN}✓ OCR data already set up${NC}"
fi

# ============================================================
# Done!
# ============================================================
echo -e "\n${GREEN}"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║                    Setup Complete!                       ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

echo "To start the server:"
echo -e "  ${YELLOW}source .venv/bin/activate${NC}"
echo -e "  ${YELLOW}python server.py /path/to/dvr/storage${NC}"
echo ""
echo "Example:"
echo -e "  ${YELLOW}python server.py /Volumes/DVR_USB${NC}"
echo ""
echo "Then open in browser:"
echo -e "  ${BLUE}http://localhost:8080${NC}"
echo ""
