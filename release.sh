#!/bin/bash
set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}Seetong DVR Release Script${NC}"
echo -e "${GREEN}============================================================${NC}"

# Check dependencies
command -v gh >/dev/null 2>&1 || { echo -e "${RED}Error: gh (GitHub CLI) is not installed${NC}"; exit 1; }
command -v npm >/dev/null 2>&1 || { echo -e "${RED}Error: npm is not installed${NC}"; exit 1; }
command -v go >/dev/null 2>&1 || { echo -e "${RED}Error: go is not installed${NC}"; exit 1; }

# Check GitHub auth
gh auth status >/dev/null 2>&1 || { echo -e "${RED}Error: Not logged in to GitHub. Run: gh auth login${NC}"; exit 1; }

# Get current version
CURRENT_TAG=$(gh release list --limit 1 --json tagName -q '.[0].tagName' 2>/dev/null || echo "v0.0.0")
if [ -z "$CURRENT_TAG" ] || [ "$CURRENT_TAG" = "null" ]; then
    CURRENT_TAG="v0.0.0"
fi

echo -e "Current version: ${YELLOW}$CURRENT_TAG${NC}"

# Parse version
VERSION=${CURRENT_TAG#v}
MAJOR=$(echo $VERSION | cut -d. -f1)
MINOR=$(echo $VERSION | cut -d. -f2)
PATCH=$(echo $VERSION | cut -d. -f3)

# Determine next version
echo ""
echo "Select version bump type:"
echo "  1) patch ($MAJOR.$MINOR.$((PATCH+1)))"
echo "  2) minor ($MAJOR.$((MINOR+1)).0)"
echo "  3) major ($((MAJOR+1)).0.0)"
echo "  4) custom"
read -p "Choice [1]: " CHOICE

case ${CHOICE:-1} in
    1) NEW_VERSION="$MAJOR.$MINOR.$((PATCH+1))" ;;
    2) NEW_VERSION="$MAJOR.$((MINOR+1)).0" ;;
    3) NEW_VERSION="$((MAJOR+1)).0.0" ;;
    4) read -p "Enter version (e.g. 1.2.3): " NEW_VERSION ;;
    *) NEW_VERSION="$MAJOR.$MINOR.$((PATCH+1))" ;;
esac

NEW_TAG="v$NEW_VERSION"
echo -e "\nNew version: ${GREEN}$NEW_TAG${NC}"
read -p "Continue? [Y/n]: " CONFIRM
if [[ "${CONFIRM:-Y}" =~ ^[Nn] ]]; then
    echo "Aborted."
    exit 0
fi

# Step 1: Build frontend
echo -e "\n${YELLOW}[1/5] Building frontend...${NC}"
cd "$PROJECT_DIR/web"
npm run build
cd "$PROJECT_DIR"

# Step 2: Copy static files for embedding
echo -e "${YELLOW}[2/5] Copying static files...${NC}"
rm -rf cmd/server/static
mkdir -p cmd/server/static
cp -r web/dist/* cmd/server/static/

# Step 3: Build binaries
echo -e "${YELLOW}[3/5] Building binaries...${NC}"
GOOS=darwin GOARCH=arm64 go build -ldflags="-s -w" -o "seetong-dvr-darwin-arm64" ./cmd/server
GOOS=darwin GOARCH=amd64 go build -ldflags="-s -w" -o "seetong-dvr-darwin-amd64" ./cmd/server

echo "  - seetong-dvr-darwin-arm64: $(du -h seetong-dvr-darwin-arm64 | cut -f1)"
echo "  - seetong-dvr-darwin-amd64: $(du -h seetong-dvr-darwin-amd64 | cut -f1)"

# Step 4: Commit and push
echo -e "${YELLOW}[4/5] Committing changes...${NC}"
git add -A
if ! git diff --cached --quiet; then
    git commit -m "Release $NEW_TAG

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>"
    git push origin main
else
    echo "  No changes to commit"
fi

# Step 5: Create release
echo -e "${YELLOW}[5/5] Creating GitHub release...${NC}"

RELEASE_NOTES=$(cat <<EOF
## Seetong DVR Web Player $NEW_TAG

Web-based player for Seetong (天视通) DVR/NVR recordings.

### Quick Install (macOS)

\`\`\`bash
curl -fsSL https://github.com/newstatic/SeetongDVR/releases/latest/download/install.sh | bash
\`\`\`

### Changes
- See commit history for details

### Downloads
| Platform | File |
|----------|------|
| macOS (Apple Silicon) | seetong-dvr-darwin-arm64 |
| macOS (Intel) | seetong-dvr-darwin-amd64 |
EOF
)

gh release create "$NEW_TAG" \
    --title "Seetong DVR Web Player $NEW_TAG" \
    --notes "$RELEASE_NOTES" \
    seetong-dvr-darwin-arm64 \
    seetong-dvr-darwin-amd64 \
    install.sh

# Cleanup
rm -f seetong-dvr-darwin-arm64 seetong-dvr-darwin-amd64

echo -e "\n${GREEN}============================================================${NC}"
echo -e "${GREEN}Release $NEW_TAG created successfully!${NC}"
echo -e "${GREEN}https://github.com/newstatic/SeetongDVR/releases/tag/$NEW_TAG${NC}"
echo -e "${GREEN}============================================================${NC}"
