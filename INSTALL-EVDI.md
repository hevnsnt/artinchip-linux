# EVDI Virtual Display Setup

Make the tinyscreen act as a real Linux monitor via EVDI. Required for `--monitor` and low-CPU `--url` modes.

## Quick Setup

```bash
# 1. Install EVDI
sudo apt install -y evdi-dkms libevdi1 xdotool

# 2. Load at boot
echo "evdi" | sudo tee /etc/modules-load.d/evdi.conf
echo "options evdi initial_device_count=1" | sudo tee /etc/modprobe.d/evdi.conf

# 3. Load now + restart display manager so X11 detects the EVDI card
sudo modprobe evdi initial_device_count=1
sudo systemctl restart lightdm   # or gdm3 for GNOME

# 4. Test (after logging back into your desktop)
sudo tinyscreen --url http://your-dashboard:8420/
```

## Platform Notes

### Kali Linux

```bash
sudo apt install -y evdi-dkms libevdi1 xdotool
```

### Ubuntu 22.04+

```bash
sudo apt install -y evdi-dkms libevdi1 xdotool
```

If the package isn't available:

```bash
sudo add-apt-repository ppa:displaylink/release
sudo apt update
sudo apt install -y evdi-dkms libevdi1
```

### Build from Source (kernel too new for packaged EVDI)

```bash
sudo apt install -y dkms linux-headers-$(uname -r) build-essential libdrm-dev
cd /tmp && git clone https://github.com/DisplayLink/evdi.git
cd evdi/module && sudo make install_dkms
cd ../library && make && sudo make install && sudo ldconfig
```

## Usage

```bash
# Show a website (low CPU, uses EVDI + real Chromium)
sudo tinyscreen --url http://192.168.1.178:8420/

# Virtual monitor mode (tinyscreen becomes a desktop display)
sudo tinyscreen --monitor

# Stop and blank the screen
sudo tinyscreen --off

# Standalone launcher (alternative, doesn't need tinyscreen daemon)
sudo ./launch-dashboard.sh http://192.168.1.178:8420/
sudo ./launch-dashboard.sh --stop
```

## Auto-Start on Boot

### 1. Enable autologin (EVDI needs a desktop session)

**LightDM (Kali):**
```bash
sudo mkdir -p /etc/lightdm/lightdm.conf.d
cat << EOF | sudo tee /etc/lightdm/lightdm.conf.d/autologin.conf
[Seat:*]
autologin-user=$USER
autologin-session=xfce
EOF
```

**GDM (Ubuntu/GNOME):**
```bash
cat << EOF | sudo tee /etc/gdm3/custom.conf
[daemon]
AutomaticLoginEnable=true
AutomaticLogin=$USER
EOF
```

### 2. Autostart the dashboard

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/tinyscreen-dashboard.desktop << EOF
[Desktop Entry]
Type=Application
Name=tinyscreen Dashboard
Exec=sudo /opt/tinyscreen/launch-dashboard.sh
X-GNOME-Autostart-enabled=true
EOF

# Allow passwordless sudo for the launcher
echo "$USER ALL=(ALL) NOPASSWD: /opt/tinyscreen/launch-dashboard.sh" | sudo tee /etc/sudoers.d/tinyscreen
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `libevdi.so.1 not found` | `sudo apt install libevdi1` or `sudo ldconfig` |
| DKMS build fails | `sudo apt install linux-headers-$(uname -r)` then `sudo dkms install evdi/<version>` |
| xrandr shows `DVI-I-1-1 disconnected` | Bridge must be running first: `sudo tinyscreen --monitor --fg` |
| xrandr hangs | EVDI module must be loaded before X starts — reboot after setup |
| `RGBA as JPEG` error | Update to latest tinyscreen-evdi.py (fixed: uses BGRX→RGB conversion) |
| No frames / `request_update` always False | NVIDIA driver quirk — fixed with polling mode in latest code |
| Browser not on tinyscreen | Needs a window manager (log into desktop session, not just TTY) |
| XFCE panel on tinyscreen | `--url` mode auto-moves the panel; or manually: `xfconf-query -c xfce4-panel -p /panels/panel-1/output-name -s DP-0` |
