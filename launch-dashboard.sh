#!/bin/bash
# Launch a website on the tinyscreen via EVDI virtual display.
#
# Usage:
#   sudo ./launch-dashboard.sh [URL]
#   sudo ./launch-dashboard.sh                              # uses default URL
#   sudo ./launch-dashboard.sh http://192.168.1.178:8420/   # custom URL
#   sudo ./launch-dashboard.sh --stop                       # stop everything

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
URL="${1:-http://192.168.1.178:8420/}"

# ── X11 environment ──────────────────────────────────────────────
export DISPLAY="${DISPLAY:-:0}"
if [ -z "$XAUTHORITY" ]; then
    for auth in "/var/run/lightdm/root/:0" \
                "/home/${SUDO_USER:-$USER}/.Xauthority" \
                "$HOME/.Xauthority"; do
        [ -f "$auth" ] && export XAUTHORITY="$auth" && break
    done
fi

# ── Helpers ──────────────────────────────────────────────────────
die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "[*] $*"; }
warn() { echo "[!] $*"; }

wait_for_death() {
    # Wait up to 5s for a process pattern to die
    local pattern="$1" i=0
    while pgrep -f "$pattern" > /dev/null 2>&1 && [ $i -lt 10 ]; do
        sleep 0.5; i=$((i+1))
    done
}

kill_clean() {
    # Kill by pattern, wait for it to actually die
    local pattern="$1"
    if pgrep -f "$pattern" > /dev/null 2>&1; then
        pkill -f "$pattern" 2>/dev/null || true
        wait_for_death "$pattern"
        # Force kill if still alive
        if pgrep -f "$pattern" > /dev/null 2>&1; then
            pkill -9 -f "$pattern" 2>/dev/null || true
            sleep 1
        fi
    fi
}

verify_x11() {
    if ! xdpyinfo > /dev/null 2>&1; then
        die "Cannot connect to X display $DISPLAY (is XAUTHORITY correct?)"
    fi
}

# ── Stop mode ────────────────────────────────────────────────────
if [ "$1" = "--stop" ]; then
    info "Stopping tinyscreen dashboard..."
    kill_clean "tinyscreen-evdi"
    kill_clean "chromium.*tinyscreen-display"
    rm -f /tmp/tinyscreen-evdi.pid /tmp/tinyscreen-evdi.log 2>/dev/null
    info "Stopped."
    exit 0
fi

# ── Preflight checks ────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || die "Must run as root (sudo)"

echo "=== tinyscreen EVDI dashboard ==="
echo "URL: $URL"
echo ""

verify_x11

# Check xdotool is available
command -v xdotool > /dev/null 2>&1 || die "xdotool not found. Install: sudo apt install xdotool"

# ── 1. EVDI module ──────────────────────────────────────────────
if ! lsmod | grep -q evdi; then
    info "Loading EVDI kernel module..."
    modprobe evdi initial_device_count=1 || die "Failed to load EVDI. Install: sudo apt install evdi-dkms"
    sleep 2
fi
info "EVDI module loaded"

# ── 2. Kill existing processes ───────────────────────────────────
info "Cleaning up old processes..."
kill_clean "tinyscreen-evdi"
kill_clean "chromium.*tinyscreen-display"
"$SCRIPT_DIR/tinyscreen" --off > /dev/null 2>&1 || true
sleep 1

# Clean stale files
rm -f /tmp/tinyscreen-evdi.pid /tmp/tinyscreen-evdi.log \
      /tmp/tinyscreen.pid /tmp/tinyscreen.log 2>/dev/null

# ── 3. Start EVDI bridge ────────────────────────────────────────
info "Starting EVDI bridge..."
python3 "$SCRIPT_DIR/tinyscreen-evdi.py" --fg > /tmp/tinyscreen-evdi.log 2>&1 &
BRIDGE_PID=$!
sleep 4

if ! kill -0 "$BRIDGE_PID" 2>/dev/null; then
    echo ""
    echo "Bridge log:"
    cat /tmp/tinyscreen-evdi.log 2>/dev/null
    die "Bridge failed to start"
fi
info "Bridge running (PID $BRIDGE_PID)"

# ── 4. Find displays ────────────────────────────────────────────
info "Configuring display..."

# Wait for EVDI output to appear (retry up to 10s)
EVDI_OUTPUT=""
for i in $(seq 1 10); do
    EVDI_OUTPUT=$(xrandr 2>/dev/null | grep " connected" | grep -v "eDP\|HDMI\|DP-" | head -1 | awk '{print $1}')
    [ -n "$EVDI_OUTPUT" ] && break
    sleep 1
done
[ -n "$EVDI_OUTPUT" ] || die "No EVDI output found after 10s. Check /tmp/tinyscreen-evdi.log"
info "EVDI output: $EVDI_OUTPUT"

# Find primary display — try multiple parsing methods
PRIMARY=""
PRIMARY_HEIGHT=1080

# Method 1: grep for "primary" keyword
PRIMARY=$(xrandr 2>/dev/null | grep " connected primary" | awk '{print $1}')

# Method 2: if no primary, use first connected non-EVDI output
if [ -z "$PRIMARY" ]; then
    PRIMARY=$(xrandr 2>/dev/null | grep " connected" | grep -v "$EVDI_OUTPUT" | head -1 | awk '{print $1}')
fi

if [ -n "$PRIMARY" ]; then
    # Extract height from resolution (e.g., "3840x1080+0+0" → 1080)
    PRIMARY_HEIGHT=$(xrandr 2>/dev/null | grep "^${PRIMARY} " | grep -oE '[0-9]+x[0-9]+\+' | head -1 | sed 's/+$//' | cut -dx -f2)
    PRIMARY_HEIGHT="${PRIMARY_HEIGHT:-1080}"
    info "Primary: $PRIMARY (height: ${PRIMARY_HEIGHT}px)"
else
    warn "No primary display found, assuming height=1080"
fi

# Configure EVDI display position
if [ -n "$PRIMARY" ]; then
    xrandr --output "$EVDI_OUTPUT" --mode 1920x440 --rotate inverted --below "$PRIMARY" 2>/dev/null
else
    xrandr --output "$EVDI_OUTPUT" --mode 1920x440 --rotate inverted --pos 0x${PRIMARY_HEIGHT} 2>/dev/null
fi
sleep 2
info "Display configured"

# ── 5. Ensure a window manager is running ────────────────────────
if ! pgrep -x "xfwm4\|openbox\|mutter\|kwin\|marco\|metacity\|i3\|sway" > /dev/null 2>&1; then
    warn "No window manager detected"
    # Try to start one
    for wm in xfwm4 openbox metacity marco; do
        if command -v "$wm" > /dev/null 2>&1; then
            info "Starting $wm..."
            if [ "$wm" = "xfwm4" ]; then
                "$wm" --compositor=off > /dev/null 2>&1 &
            else
                "$wm" > /dev/null 2>&1 &
            fi
            sleep 2
            if pgrep -x "$wm" > /dev/null 2>&1; then
                info "$wm running"
                break
            fi
        fi
    done
fi

# ── 6. Launch browser ───────────────────────────────────────────
info "Launching browser..."
chromium --no-first-run --no-sandbox --test-type \
    --disable-session-crashed-bubble --noerrdialogs \
    --disable-infobars \
    --class=tinyscreen-display \
    --window-size=1920,440 \
    --window-position=0,"$PRIMARY_HEIGHT" \
    --app="$URL" > /dev/null 2>&1 &
BROWSER_PID=$!

# Wait for the window to appear (retry up to 15s)
WID=""
for i in $(seq 1 15); do
    WID=$(xdotool search --class tinyscreen-display 2>/dev/null | tail -1)
    [ -n "$WID" ] && break
    sleep 1
done

if [ -z "$WID" ]; then
    warn "Browser window not found — trying by PID"
    WID=$(xdotool search --pid "$BROWSER_PID" 2>/dev/null | tail -1)
fi

if [ -n "$WID" ]; then
    # Move and resize to EVDI display
    xdotool windowmove "$WID" 0 "$PRIMARY_HEIGHT" 2>/dev/null
    xdotool windowsize "$WID" 1920 440 2>/dev/null
    xdotool windowactivate "$WID" 2>/dev/null
    sleep 1
    # Remove decorations via fullscreen
    xdotool key F11 2>/dev/null
    info "Browser positioned on tinyscreen"
else
    warn "Could not find browser window — it may need manual positioning"
    warn "Try: xdotool search --class Chromium windowmove %1 0 $PRIMARY_HEIGHT windowsize %1 1920 440"
fi

# ── Done ─────────────────────────────────────────────────────────
echo ""
echo "=== Dashboard running ==="
echo "  Bridge PID: $BRIDGE_PID"
echo "  Browser PID: $BROWSER_PID"
echo "  Log: tail -f /tmp/tinyscreen-evdi.log"
echo "  Stop: sudo $0 --stop"
