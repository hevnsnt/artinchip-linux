#!/bin/bash
set -e

# tinyscreen installer — ArtInChip USB bar display driver for Linux

INSTALL_DIR="/opt/tinyscreen"
BIN_LINK="/usr/local/bin/tinyscreen"
UDEV_RULE="/etc/udev/rules.d/99-artinchip.rules"
SERVICE_FILE="/etc/systemd/system/tinyscreen.service"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[-]${NC} $1"; }

if [ "$(id -u)" -ne 0 ]; then
    error "Run as root: sudo ./install.sh"
    exit 1
fi

echo ""
echo "  ┌─────────────────────────────────────────┐"
echo "  │  tinyscreen installer                    │"
echo "  │  ArtInChip USB bar display driver        │"
echo "  └─────────────────────────────────────────┘"
echo ""

# ── Dependencies ────────────────────────────────────────────────────
info "Checking dependencies..."

install_pkg() {
    if command -v apt-get &>/dev/null; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
    elif command -v dnf &>/dev/null; then
        dnf install -y "$@"
    elif command -v pacman &>/dev/null; then
        pacman -S --noconfirm "$@"
    else
        warn "Unknown package manager — install manually: $*"
        return 1
    fi
}

# Python 3
if ! command -v python3 &>/dev/null; then
    info "Installing Python 3..."
    install_pkg python3 python3-pip
fi

# pip packages
info "Installing Python packages..."
python3 -m pip install --break-system-packages pyusb Pillow cryptography 2>&1 | tail -1 \
    || python3 -m pip install pyusb Pillow cryptography 2>&1 | tail -1 \
    || warn "pip install failed — install manually: pip install pyusb Pillow cryptography"

# ffmpeg
if ! command -v ffmpeg &>/dev/null; then
    info "Installing ffmpeg..."
    install_pkg ffmpeg || warn "Install ffmpeg manually"
fi

# Xvfb
if ! command -v Xvfb &>/dev/null; then
    info "Installing Xvfb..."
    install_pkg xvfb || install_pkg xorg-server-xvfb || warn "Install Xvfb manually"
fi

# Browser check
if ! command -v chromium &>/dev/null && ! command -v chromium-browser &>/dev/null && ! command -v google-chrome &>/dev/null; then
    warn "No browser found (chromium/google-chrome). URL mode won't work without one."
    warn "Install with: sudo apt install chromium"
fi

# yt-dlp (optional)
if ! command -v yt-dlp &>/dev/null; then
    info "Installing yt-dlp (optional, for YouTube support)..."
    python3 -m pip install --break-system-packages yt-dlp 2>&1 | tail -1 \
        || python3 -m pip install yt-dlp 2>&1 | tail -1 \
        || warn "yt-dlp not installed — YouTube mode won't work. Install with: pip install yt-dlp"
fi

# ── Install files ───────────────────────────────────────────────────
info "Installing to ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR/modes"
cp "$SCRIPT_DIR/tinyscreen.py" "$INSTALL_DIR/tinyscreen.py"
cp "$SCRIPT_DIR/tinyscreen" "$INSTALL_DIR/tinyscreen"
cp "$SCRIPT_DIR/sysmon.py" "$INSTALL_DIR/sysmon.py"
cp "$SCRIPT_DIR/uninstall.sh" "$INSTALL_DIR/uninstall.sh"
cp "$SCRIPT_DIR"/modes/*.py "$INSTALL_DIR/modes/"
# Only copy config if not already present (preserve user edits)
[ ! -f "$INSTALL_DIR/config.yml" ] && cp "$SCRIPT_DIR/config.yml" "$INSTALL_DIR/config.yml"
chmod +x "$INSTALL_DIR/tinyscreen" "$INSTALL_DIR/tinyscreen.py" "$INSTALL_DIR/uninstall.sh"

# Symlink
info "Creating ${BIN_LINK}..."
ln -sf "$INSTALL_DIR/tinyscreen" "$BIN_LINK"

# ── udev rule ───────────────────────────────────────────────────────
info "Installing udev rule..."
cat > "$UDEV_RULE" << 'EOF'
# ArtInChip USB Display devices — allow any user access (no sudo needed)
SUBSYSTEM=="usb", ATTR{idVendor}=="33c3", ATTR{idProduct}=="0e01", MODE="0666"
SUBSYSTEM=="usb", ATTR{idVendor}=="33c3", ATTR{idProduct}=="0e02", MODE="0666"
SUBSYSTEM=="usb", ATTR{idVendor}=="33c3", ATTR{idProduct}=="0e04", MODE="0666"
SUBSYSTEM=="usb", ATTR{idVendor}=="33c3", ATTR{idProduct}=="0e05", MODE="0666"
EOF
udevadm control --reload-rules 2>/dev/null
udevadm trigger 2>/dev/null
info "udev rules installed (replug display or reboot to apply)"

# ── Blacklist kernel module ─────────────────────────────────────────
if lsmod 2>/dev/null | grep -q aic_drm_ud; then
    warn "aic_drm_ud kernel module is loaded — it conflicts with tinyscreen"
    warn "Blacklisting it..."
fi
cat > /etc/modprobe.d/blacklist-aic-tinyscreen.conf << 'EOF'
# Blacklist ArtInChip DRM driver — tinyscreen talks to the device directly via USB
blacklist aic_drm_ud
EOF
info "Kernel module aic_drm_ud blacklisted"

# ── systemd service ─────────────────────────────────────────────────
info "Installing systemd service..."

# Detect PATH for ffmpeg, yt-dlp, browsers
FFMPEG_DIR="$(dirname "$(command -v ffmpeg 2>/dev/null || echo /usr/bin/ffmpeg)")"
EXTRA_PATHS="/usr/local/bin:/usr/bin:/bin"
[ -d "/home/linuxbrew/.linuxbrew/bin" ] && EXTRA_PATHS="/home/linuxbrew/.linuxbrew/bin:${EXTRA_PATHS}"
# Include invoking user's local bin for yt-dlp
SUDO_USER="${SUDO_USER:-}"
[ -n "$SUDO_USER" ] && [ -d "/home/${SUDO_USER}/.local/bin" ] && EXTRA_PATHS="/home/${SUDO_USER}/.local/bin:${EXTRA_PATHS}"

cat > "$SERVICE_FILE" << SERVICEEOF
[Unit]
Description=tinyscreen - ArtInChip USB bar display
After=network-online.target graphical.target
Wants=network-online.target

[Service]
Type=simple
# >>> EDIT THIS: Change the URL to your dashboard <<<
ExecStart=/usr/bin/python3 /opt/tinyscreen/tinyscreen.py --url http://YOUR-DASHBOARD-URL:PORT/ --fg
Restart=on-failure
RestartSec=5
Environment=PATH=${FFMPEG_DIR}:${EXTRA_PATHS}
Environment=HOME=/root

[Install]
WantedBy=multi-user.target
SERVICEEOF

systemctl daemon-reload 2>/dev/null

# ── Done ────────────────────────────────────────────────────────────
echo ""
info "Installation complete!"
echo ""
echo "  Quick start:"
echo "    tinyscreen --test                    # verify display works"
echo "    tinyscreen --url https://example.com # show a website"
echo "    tinyscreen --video video.mp4         # play a video"
echo "    tinyscreen --off                     # stop"
echo ""
echo "  Auto-start on boot:"
echo "    1. Edit the URL: sudo nano ${SERVICE_FILE}"
echo "    2. sudo systemctl enable tinyscreen"
echo "    3. sudo systemctl start tinyscreen"
echo ""
echo "  If the display isn't detected, replug the USB cable."
echo ""
