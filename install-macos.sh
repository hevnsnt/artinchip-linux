#!/bin/bash
set -e

# tinyscreen macOS installer
# Installs the ArtInChip USB bar display driver on macOS (Apple Silicon or Intel).

INSTALL_DIR="$HOME/.tinyscreen"
BIN_LINK="/usr/local/bin/tinyscreen"
VENV="$INSTALL_DIR/venv"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[-]${NC} $1"; }

echo ""
echo "  tinyscreen macOS installer"
echo "  ArtInChip USB bar display driver (ZHAOCAILIN 11.3\" 1920x440)"
echo ""

# ── 1. Homebrew ─────────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
    error "Homebrew is required but not found."
    echo "  Install it first: https://brew.sh"
    echo "  Then re-run: ./install-macos.sh"
    exit 1
fi
info "Homebrew found"

# ── 2. System dependencies ──────────────────────────────────────────
info "Installing system dependencies (libusb, ffmpeg)..."
brew install libusb ffmpeg || warn "brew install failed; install libusb + ffmpeg manually"

# Optional extras
if ! command -v yt-dlp &>/dev/null; then
    info "Installing yt-dlp (YouTube support)..."
    brew install yt-dlp || warn "yt-dlp skipped; YouTube mode won't work"
fi
if ! command -v osx-cpu-temp &>/dev/null; then
    info "Installing osx-cpu-temp (CPU temp for sysmon)..."
    brew install osx-cpu-temp || warn "osx-cpu-temp skipped; sysmon temps show N/A"
fi
if ! command -v nmap &>/dev/null; then
    info "Installing nmap (lanmap mode)..."
    brew install nmap || warn "nmap skipped; lanmap mode won't scan"
fi

# ── 3. Python venv + packages ───────────────────────────────────────
info "Creating Python virtualenv at $VENV..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip >/dev/null
info "Installing Python packages (pyusb Pillow cryptography numpy psutil requests websocket-client pyyaml)..."
"$VENV/bin/pip" install pyusb Pillow cryptography numpy psutil requests websocket-client pyyaml

# Optional: audio visualizer support
if brew list --cask blackhole-2ch &>/dev/null 2>&1; then
    info "BlackHole found; installing sounddevice for the visualizer..."
    "$VENV/bin/pip" install sounddevice || warn "sounddevice skipped"
else
    warn "BlackHole not installed. The --visualizer mode needs it:"
    echo "       brew install --cask blackhole-2ch"
    echo "       then: $VENV/bin/pip install sounddevice"
fi

# ── 4. Install files ────────────────────────────────────────────────
info "Installing to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR/modes"
cp "$(dirname "$0")/tinyscreen.py" "$INSTALL_DIR/"
cp "$(dirname "$0")/tinyscreen" "$INSTALL_DIR/"
cp "$(dirname "$0")/sysmon.py" "$INSTALL_DIR/"
cp "$(dirname "$0")/uninstall.sh" "$INSTALL_DIR/" 2>/dev/null || true
cp "$(dirname "$0")"/modes/*.py "$INSTALL_DIR/modes/"
cp -r "$(dirname "$0")/modes/scenes" "$INSTALL_DIR/modes/" 2>/dev/null || true
[ ! -f "$INSTALL_DIR/config.yml" ] && cp "$(dirname "$0")/config.yml" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/tinyscreen" "$INSTALL_DIR/tinyscreen.py"

# ── 5. Symlink into PATH ────────────────────────────────────────────
info "Creating $BIN_LINK..."
if [ -d "/opt/homebrew/bin" ]; then
    BIN_LINK="/opt/homebrew/bin/tinyscreen"
fi
ln -sf "$INSTALL_DIR/tinyscreen" "$BIN_LINK"

# ── 6. Done ─────────────────────────────────────────────────────────
echo ""
info "Installation complete!"
echo ""
echo "  Quick start:"
echo "    tinyscreen --test                    # verify the display works"
echo "    tinyscreen --url https://example.com # show a website (headless Chrome)"
echo "    tinyscreen --image photo.jpg         # show an image"
echo "    tinyscreen --video clip.mp4          # play a video"
echo "    tinyscreen --sysmon                  # system monitor dashboard"
echo "    tinyscreen --clock                   # clock + weather"
echo "    tinyscreen --off                     # stop and blank the display"
echo ""
echo "  Notes:"
echo "    - Plug the display in, then run 'tinyscreen --test'."
echo "    - If the display is not detected, unplug/replug the USB cable."
echo "    - --monitor (EVDI virtual display) is Linux-only; on macOS use the"
echo "      content modes above instead."
echo ""
