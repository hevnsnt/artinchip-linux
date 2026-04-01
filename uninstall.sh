#!/bin/bash
set -e

# tinyscreen uninstaller

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $1"; }

if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}[-]${NC} Run as root: sudo /opt/tinyscreen/uninstall.sh"
    exit 1
fi

echo "Uninstalling tinyscreen..."

# Stop running instance
if [ -f /tmp/tinyscreen.pid ]; then
    PID=$(cat /tmp/tinyscreen.pid)
    if kill -0 "$PID" 2>/dev/null; then
        info "Stopping tinyscreen (PID $PID)..."
        kill "$PID" 2>/dev/null || true
        sleep 1
    fi
fi

# Disable systemd service
if systemctl is-enabled tinyscreen 2>/dev/null; then
    info "Disabling systemd service..."
    systemctl stop tinyscreen 2>/dev/null || true
    systemctl disable tinyscreen 2>/dev/null || true
fi

info "Removing files..."
rm -f /usr/local/bin/tinyscreen
rm -f /etc/systemd/system/tinyscreen.service
rm -f /etc/udev/rules.d/99-artinchip.rules
rm -f /etc/modprobe.d/blacklist-aic-tinyscreen.conf
rm -f /tmp/tinyscreen.pid /tmp/tinyscreen.log /tmp/tinyscreen.state
rm -rf /opt/tinyscreen

systemctl daemon-reload 2>/dev/null
udevadm control --reload-rules 2>/dev/null

info "tinyscreen uninstalled."
