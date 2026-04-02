#!/usr/bin/env python3
"""
tinyscreen - ArtInChip USB bar display driver for Linux.

Reverse-engineered protocol driver for ArtInChip (33c3:0e0x) USB bar monitors.
Performs RSA device authentication, then streams JPEG frames over USB bulk transfers.

Usage:
  tinyscreen --url URL         Show a website (live virtual display + browser)
  tinyscreen --video URL       Play a video file or YouTube URL (up to 4K source)
  tinyscreen --image FILE      Show a static image
  tinyscreen --test            Show test pattern
  tinyscreen --off             Stop the running tinyscreen instance
  tinyscreen --status          Check if tinyscreen is running
"""

import struct
import time
import sys
import io
import os
import re
import secrets
import resource
import subprocess
import signal
import shutil
import atexit
import json
import socket
import tempfile
from urllib.parse import urlparse

PIDFILE = '/tmp/tinyscreen.pid'
LOGFILE = '/tmp/tinyscreen.log'
STATEFILE = '/tmp/tinyscreen.state'

# ── PATH fixup (sudo drops user PATH) ──────────────────────────────
_sudo_user = os.environ.get('SUDO_USER', '')
if _sudo_user and not re.match(r'^[a-z_][a-z0-9_-]*$', _sudo_user):
    _sudo_user = ''  # reject suspicious values

for p in ['/home/linuxbrew/.linuxbrew/bin', '/usr/local/bin']:
    if p not in os.environ.get('PATH', ''):
        os.environ['PATH'] = p + ':' + os.environ.get('PATH', '')
if _sudo_user:
    os.environ['PATH'] = f'/home/{_sudo_user}/.local/bin:' + os.environ['PATH']

import usb.core
import usb.util
from PIL import Image, ImageDraw, ImageFont
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

# ── Protocol ────────────────────────────────────────────────────────
FRAME_START_MAGIC = 0xA1C62B01
AUTH_DEV_MAGIC    = 0xA1C62B10
AUTH_HOST_MAGIC   = 0xA1C62B11
VID, PID = 0x33C3, 0x0E02
EP_OUT, EP_IN = 0x01, 0x81
MAX_TRANSFER = 4096 * 64

# RSA public key extracted from aic-render binary.
# Used for device authentication — see README for protocol details.
RSA_PUB_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAybdtvB1uNA4XICh+xJi1
KJWO0GYal4lNiW69zSMIJFGzb2wkiFBX2txFaH5ZYh0TYdwmjzBqinzTsWhIasW3
rl9QN5cv73zFalO3J4hADXz1g7hlHVB0BKDD280NUKUGAbwDv+KMHTprs+B/T4QU
a0s4RBNnN4fMPk2H0UAWU1jKAvMYjh/YR+MLYbl04ZCLlOfX9zQjRBVan7aLARQg
v5QRahAlAoBsYK864VrBKq91lRCXt4XP5d/sDtZM7kGcpLi2i4xHtRct37M+bkZv
Lf/3aVpAVsqZy5P2NXEe6HMv4Q+YP6QKz2wuk3xWYHWFn+88ydjv394tN28rjl56
hwIDAQAB
-----END PUBLIC KEY-----"""

# ── Logging ─────────────────────────────────────────────────────────
_log_fh = None

def log(msg):
    ts = time.strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    if _log_fh:
        _log_fh.write(line + '\n')
        _log_fh.flush()
    else:
        print(line, flush=True)

# ── Crypto ──────────────────────────────────────────────────────────
def load_rsa_key():
    return serialization.load_pem_public_key(RSA_PUB_PEM)

def rsa_public_decrypt(pub_key, ct):
    """RSA signature recovery: compute m = ct^e mod n, strip PKCS#1 v1.5 type-1 padding."""
    n = pub_key.public_numbers().n
    e = pub_key.public_numbers().e
    m = pow(int.from_bytes(ct, 'big'), e, n)
    m_bytes = m.to_bytes((n.bit_length() + 7) // 8, 'big')
    # PKCS#1 v1.5 type 1: 0x00 0x01 [0xFF padding] 0x00 [data]
    if m_bytes[0] != 0 or m_bytes[1] != 1:
        raise ValueError("Bad PKCS#1 padding")
    idx = 2
    while idx < len(m_bytes) and m_bytes[idx] == 0xFF:
        idx += 1
    if idx >= len(m_bytes) or m_bytes[idx] != 0:
        raise ValueError("Bad PKCS#1 separator")
    return m_bytes[idx + 1:]

# ── USB + Auth ──────────────────────────────────────────────────────
def find_device():
    return usb.core.find(idVendor=VID, idProduct=PID)

def setup_device(dev):
    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except usb.core.USBError:
        pass
    usb.util.claim_interface(dev, 0)

def get_params(dev):
    data = dev.ctrl_transfer(
        usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
        0, 0, 0, 256, timeout=5000)
    ver, chip, fmt, bus, modes, w, h, fps = struct.unpack_from('<8H', data, 0)
    return w, h, fmt, fps

def bulk_out(dev, data, timeout=5000):
    dev.write(EP_OUT, data, timeout=timeout)

def bulk_in(dev, size=256, timeout=5000):
    return bytes(dev.read(EP_IN, size, timeout=timeout))

def authenticate(dev):
    """Two-phase RSA authentication handshake.

    Phase 1 (auth_dev): Host verifies device holds the private key.
    Phase 2 (auth_host): Device verifies host can perform RSA public-key operations.
    The embedded public key is shared — phase 2 is not a strong host identity proof,
    but it is required by the device firmware before it will accept frame data.
    """
    pk = load_rsa_key()
    # Phase 1: encrypt random challenge, device must decrypt it
    challenge = os.urandom(secrets.randbelow(244) + 1)
    encrypted = pk.encrypt(challenge, asym_padding.PKCS1v15())
    bulk_out(dev, struct.pack('<IIHHII', AUTH_DEV_MAGIC, 0x100, 0, 0, 0, AUTH_DEV_MAGIC))
    bulk_out(dev, encrypted)
    response = bulk_in(dev, 256, timeout=3000)
    if len(response) < len(challenge) or response[:len(challenge)] != challenge:
        return False
    # Phase 2: device sends signed blob, host recovers plaintext and returns it
    bulk_out(dev, struct.pack('<IIHHII', AUTH_HOST_MAGIC, 0x100, 0, 0, 0, AUTH_HOST_MAGIC))
    signed = bulk_in(dev, 256, timeout=3000)
    plaintext = rsa_public_decrypt(pk, signed)
    bulk_out(dev, plaintext)
    return True

def send_frame(dev, jpeg_data, media_format, frame_id):
    bulk_out(dev, struct.pack('<IIHHII',
                              FRAME_START_MAGIC, len(jpeg_data),
                              frame_id & 0xFFFF, media_format, 0,
                              FRAME_START_MAGIC))
    for pos in range(0, len(jpeg_data), MAX_TRANSFER):
        bulk_out(dev, bytes(jpeg_data[pos:pos + MAX_TRANSFER]), timeout=10000)

# ── Display connection with auto-reconnect ──────────────────────────
class Display:
    """Manages USB device lifecycle with auto-reconnect."""

    def __init__(self, rotate=0):
        self.dev = None
        self.w = self.h = self.fmt = self.fps = 0
        self.frame_id = 0
        self.rotate = rotate % 360  # 0, 90, 180, 270

    def connect(self):
        self.dev = find_device()
        if not self.dev:
            return False
        try:
            setup_device(self.dev)
            self.w, self.h, self.fmt, self.fps = get_params(self.dev)
            if not authenticate(self.dev):
                log("Authentication failed")
                return False
            log(f"Connected: {self.w}x{self.h} @ {self.fps}fps")
            self.frame_id = 0
            return True
        except Exception as e:
            log(f"Connect error: {e}")
            self.dev = None
            return False

    def wait_for_device(self):
        """Block until device is found and authenticated, with periodic logging."""
        t0 = time.monotonic()
        attempts = 0
        while True:
            if self.connect():
                return
            attempts += 1
            if attempts % 5 == 1:
                elapsed = int(time.monotonic() - t0)
                log(f"Waiting for display... ({elapsed}s elapsed)")
            time.sleep(2)

    @property
    def render_w(self):
        """Width that modes should render at (swapped if rotated 90/270)."""
        return self.h if self.rotate in (90, 270) else self.w

    @property
    def render_h(self):
        """Height that modes should render at (swapped if rotated 90/270)."""
        return self.w if self.rotate in (90, 270) else self.h

    def send(self, jpeg_data):
        """Send a JPEG frame, applying rotation if set. Returns False on USB error."""
        if not self.dev:
            return False
        # Apply rotation if needed
        if self.rotate:
            img = Image.open(io.BytesIO(jpeg_data))
            # PIL rotates counter-clockwise, so negate for clockwise
            img = img.rotate(-self.rotate, expand=True)
            jpeg_data = image_to_jpeg(img, 85)
        try:
            send_frame(self.dev, jpeg_data, self.fmt, self.frame_id)
            self.frame_id += 1
            return True
        except usb.core.USBError:
            try:
                self.dev.clear_halt(EP_OUT)
                send_frame(self.dev, jpeg_data, self.fmt, self.frame_id)
                self.frame_id += 1
                return True
            except Exception:
                log("USB error, will reconnect...")
                self.dev = None
                return False

    def release(self):
        if self.dev:
            try:
                usb.util.release_interface(self.dev, 0)
            except Exception:
                pass

# ── Image helpers ───────────────────────────────────────────────────
def image_to_jpeg(img, quality=80):
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=quality)
    return buf.getvalue()

# Font cache — avoid re-parsing from disk on every status screen
_font_cache = {}

def _load_font(size=36):
    if size in _font_cache:
        return _font_cache[size]
    for path in ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                 '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                 '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf']:
        try:
            font = ImageFont.truetype(path, size)
            _font_cache[size] = font
            return font
        except Exception:
            pass
    font = ImageFont.load_default()
    _font_cache[size] = font
    return font

def make_status_screen(w, h, line1, line2="", line3=""):
    img = Image.new('RGB', (w, h), (15, 15, 25))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, w, 3], fill=(0, 150, 255))
    y = h // 2 - 50
    draw.text((40, y), line1, fill=(220, 220, 240), font=_load_font(36))
    if line2:
        draw.text((40, y + 46), line2, fill=(140, 140, 170), font=_load_font(22))
    if line3:
        draw.text((40, y + 76), line3, fill=(100, 100, 130), font=_load_font(22))
    draw.text((w - 180, h - 30), "tinyscreen", fill=(60, 60, 80), font=_load_font(22))
    return img

def make_test_pattern(w, h):
    img = Image.new('RGB', (w, h), (20, 20, 40))
    draw = ImageDraw.Draw(img)
    colors = [(255, 0, 0), (255, 165, 0), (255, 255, 0),
              (0, 255, 0), (0, 128, 255), (128, 0, 255), (255, 255, 255)]
    bw = w // len(colors)
    for i, c in enumerate(colors):
        draw.rectangle([i * bw, 40, (i + 1) * bw, h - 40], fill=c)
    draw.text((w // 2 - 200, h // 2 - 30), "tinyscreen", fill=(255, 255, 255), font=_load_font(48))
    return img

# ── Network helpers ─────────────────────────────────────────────────
def check_url_reachable(url, timeout=3):
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except (socket.timeout, socket.error, OSError):
        return False

def wait_for_url(disp, url, quality=75):
    """Show waiting screen on display until URL becomes reachable."""
    parsed = urlparse(url)
    host_display = parsed.hostname
    if parsed.port:
        host_display += f":{parsed.port}"

    log(f"Waiting for {host_display}...")
    dots = 0
    while not check_url_reachable(url):
        screen = make_status_screen(
            disp.w, disp.h,
            f"Waiting for {host_display}{'.' * (dots % 4)}",
            "Will connect automatically when available",
            time.strftime("%H:%M:%S"))
        jpeg = image_to_jpeg(screen, quality)
        if not disp.send(jpeg):
            disp.wait_for_device()
        dots += 1
        time.sleep(3)
    log(f"{host_display} is reachable")

# ── Daemon management ───────────────────────────────────────────────
def read_pid():
    try:
        with open(PIDFILE) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None

def is_running(pid):
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def _find_other_pids():
    """Find tinyscreen.py PIDs other than our own process tree."""
    my_pid = os.getpid()
    my_ppid = os.getppid()
    pids = []
    try:
        result = subprocess.run(['pgrep', '-f', 'tinyscreen.py'],
                                capture_output=True, text=True)
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            pid = int(line)
            if pid != my_pid and pid != my_ppid:
                pids.append(pid)
    except Exception:
        pass
    return pids

def _stop_others():
    """Kill other tinyscreen.py processes."""
    pids = _find_other_pids()
    if not pids:
        print("tinyscreen is not running.")
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(0.3)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    for f in [PIDFILE, STATEFILE]:
        try:
            os.unlink(f)
        except (FileNotFoundError, PermissionError):
            pass
    print(f"tinyscreen stopped ({len(pids)} process{'es' if len(pids) != 1 else ''}).")

def stop_existing():
    """Kill other tinyscreen.py processes (silent version for mode switching)."""
    pids = _find_other_pids()
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if pids:
        time.sleep(0.3)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    for f in [PIDFILE, STATEFILE]:
        try:
            os.unlink(f)
        except (FileNotFoundError, PermissionError):
            pass

def write_pid():
    with open(PIDFILE, 'w') as f:
        f.write(str(os.getpid()))

def write_state(mode, target):
    with open(STATEFILE, 'w') as f:
        json.dump({'mode': mode, 'target': target, 'pid': os.getpid(),
                   'started': time.strftime('%Y-%m-%d %H:%M:%S')}, f)

def daemonize():
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
    # Close inherited file descriptors
    maxfd = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    if maxfd == resource.RLIM_INFINITY:
        maxfd = 1024
    os.closerange(3, maxfd)
    sys.stdin = open(os.devnull)
    global _log_fh
    _log_fh = open(LOGFILE, 'a')
    sys.stdout = _log_fh
    sys.stderr = _log_fh

# ── yt-dlp ──────────────────────────────────────────────────────────
def is_youtube_url(url):
    return any(x in url for x in ['youtube.com', 'youtu.be', 'youtube-nocookie.com'])

def find_yt_dlp():
    for path in [shutil.which('yt-dlp'),
                 os.path.expanduser('~/.local/bin/yt-dlp'),
                 '/usr/local/bin/yt-dlp', '/usr/bin/yt-dlp']:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    if _sudo_user:
        path = f'/home/{_sudo_user}/.local/bin/yt-dlp'
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None

def get_stream_urls(video_url):
    """Resolve video URL via yt-dlp. Returns list of URLs (video, possibly audio)."""
    yt_dlp = find_yt_dlp()
    if not yt_dlp:
        log("ERROR: yt-dlp not found")
        return None
    cmd = [yt_dlp, '-f',
           'bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/'
           'bestvideo[height<=2160]+bestaudio/'
           'best[height<=2160]/best',
           '--get-url', video_url]
    if _sudo_user and os.getuid() == 0:
        cmd = ['sudo', '-u', _sudo_user] + cmd
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            urls = [u for u in result.stdout.strip().split('\n') if u]
            return urls if urls else None
        log(f"yt-dlp: {result.stderr.strip()[:200]}")
    except subprocess.TimeoutExpired:
        log("yt-dlp timed out")
    return None

# ── ffmpeg streaming core ───────────────────────────────────────────
def stream_ffmpeg(disp, ffmpeg_cmd, quality, target_fps, loop=False):
    """Read raw RGB frames from ffmpeg stdout, JPEG-encode, send to display."""
    frame_size = disp.w * disp.h * 3
    while True:
        log("ffmpeg starting...")
        # Redirect stderr to tempfile to avoid pipe deadlock
        stderr_file = tempfile.TemporaryFile()
        proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=stderr_file)
        frame_id = 0
        interval = 1.0 / target_fps
        t0 = time.monotonic()
        dropped = 0
        try:
            while True:
                raw = proc.stdout.read(frame_size)
                if len(raw) < frame_size:
                    break
                img = Image.frombytes('RGB', (disp.w, disp.h), raw)
                jpeg = image_to_jpeg(img, quality)
                expected = t0 + frame_id * interval
                now = time.monotonic()
                if now < expected:
                    time.sleep(expected - now)
                elif now - expected > interval * 2:
                    dropped += 1
                    frame_id += 1
                    continue

                if not disp.send(jpeg):
                    log("Display lost during stream, reconnecting...")
                    disp.wait_for_device()

                frame_id += 1
                if frame_id % (target_fps * 10) == 0:
                    elapsed = time.monotonic() - t0
                    fps_actual = frame_id / elapsed if elapsed > 0 else 0
                    log(f"Frame {frame_id}, {fps_actual:.1f}fps, "
                        f"{len(jpeg)//1024}KB/fr, {dropped} dropped")
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
                proc.wait()

        # Read stderr safely after process has exited
        stderr_file.seek(0)
        stderr = stderr_file.read().decode(errors='replace').strip()
        stderr_file.close()
        if stderr and frame_id == 0:
            log(f"ffmpeg: {stderr[:300]}")
            return
        if frame_id > 0:
            elapsed = time.monotonic() - t0
            log(f"Stream done: {frame_id} frames, {elapsed:.0f}s")
        if not loop:
            break
        log("Looping...")

# ── Mode: video ─────────────────────────────────────────────────────
def mode_video(disp, source, quality, fps, loop):
    if is_youtube_url(source):
        log("Resolving YouTube URL...")
        urls = get_stream_urls(source)
        if not urls:
            log("ERROR: Could not resolve video URL")
            return
        log(f"Got {len(urls)} stream URL(s), scaling to {disp.w}x{disp.h}")
    else:
        urls = [source]

    # Build ffmpeg command with separate -i for each URL (video + audio)
    ffmpeg_cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error']
    for u in urls:
        ffmpeg_cmd += ['-i', u]
    ffmpeg_cmd += [
        '-vf', f'scale={disp.w}:{disp.h}:force_original_aspect_ratio=increase,'
               f'crop={disp.w}:{disp.h}',
        '-r', str(fps), '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-an', '-'
    ]
    stream_ffmpeg(disp, ffmpeg_cmd, quality, fps, loop)

# ── Mode: URL (virtual display + browser) ───────────────────────────
_child_procs = []

def cleanup_children():
    for p in _child_procs:
        try:
            p.terminate()
            p.wait(timeout=3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    _child_procs.clear()

def mode_url(disp, url, quality, fps):
    """Run a live virtual display with a browser pointed at url."""
    atexit.register(cleanup_children)
    display = ':98'

    wait_for_url(disp, url, quality)

    # Start Xvfb
    xvfb = subprocess.Popen(
        ['Xvfb', display, '-screen', '0', f'{disp.w}x{disp.h}x24', '-ac', '-nolisten', 'tcp'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _child_procs.append(xvfb)
    time.sleep(1)
    if xvfb.poll() is not None:
        log("ERROR: Xvfb failed to start")
        return

    env = os.environ.copy()
    env['DISPLAY'] = display

    browser = None
    for b in ['chromium', 'chromium-browser', 'google-chrome']:
        if shutil.which(b):
            browser = b
            break
    if not browser:
        log("ERROR: No browser found (chromium/google-chrome)")
        cleanup_children()
        return

    # Run browser as the invoking user if we're root, for sandbox safety
    browser_cmd = [browser, '--disable-gpu',
                   '--disable-software-rasterizer', '--disable-dev-shm-usage',
                   '--disable-background-timer-throttling',
                   '--disable-renderer-backgrounding',
                   '--disable-backgrounding-occluded-windows',
                   f'--window-size={disp.w},{disp.h}', '--kiosk', '--hide-scrollbars',
                   '--autoplay-policy=no-user-gesture-required',
                   '--no-first-run', '--disable-translate', url]

    if _sudo_user and os.getuid() == 0:
        # Drop to unprivileged user for browser — avoids running Chrome as root
        log(f"Launching {browser} as {_sudo_user} -> {url}")
        browser_cmd = ['sudo', '-u', _sudo_user, f'DISPLAY={display}'] + browser_cmd
        bp = subprocess.Popen(browser_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        log(f"Launching {browser} -> {url}")
        bp = subprocess.Popen(browser_cmd, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _child_procs.append(bp)
    time.sleep(3)

    log(f"Streaming {display} at {fps}fps")
    ffmpeg_cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-f', 'x11grab', '-framerate', str(fps),
        '-video_size', f'{disp.w}x{disp.h}', '-i', display,
        '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'
    ]
    try:
        stream_ffmpeg(disp, ffmpeg_cmd, quality, fps, loop=True)
    finally:
        cleanup_children()

# ── Mode: image ─────────────────────────────────────────────────────
def mode_image(disp, path, quality):
    if not os.path.isfile(path):
        log(f"ERROR: File not found: {path}")
        return
    img = Image.open(path).resize((disp.w, disp.h), Image.LANCZOS)
    jpeg = image_to_jpeg(img, quality)
    log(f"Image: {path} ({len(jpeg)//1024}KB)")
    while True:
        if not disp.send(jpeg):
            disp.wait_for_device()
        time.sleep(5)

# ── Mode: generic module runner ─────────────────────────────────────
MODES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'modes')
ALL_MODES = ['sysmon', 'ticker', 'clock', 'matrix', 'visualizer',
             'nowplaying', 'docker', 'netmon', 'news', 'pomodoro']

def _load_mode(name):
    """Import a mode module by name. Returns module or None."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, MODES_DIR)
    module_map = {
        'sysmon': 'sysmon',
        'ticker': 'ticker',
        'clock': 'clock',
        'matrix': 'matrix',
        'visualizer': 'visualizer',
        'nowplaying': 'nowplaying',
        'docker': 'docker_mon',
        'netmon': 'netmon',
        'news': 'newscrawl',
        'pomodoro': 'pomodoro',
    }
    mod_name = module_map.get(name, name)
    try:
        import importlib
        return importlib.import_module(mod_name)
    except Exception as e:
        log(f"Failed to load mode '{name}': {e}")
        return None

def _init_mode(mod):
    """Call init() on a mode module if it has one."""
    if hasattr(mod, 'init'):
        try:
            mod.init()
        except Exception as e:
            log(f"Mode init error: {e}")
    # Warm up sysmon differential readings
    if hasattr(mod, 'read_cpu'):
        mod.read_cpu()
    if hasattr(mod, 'read_net'):
        mod.read_net()
        time.sleep(0.5)

def _cleanup_mode(mod):
    """Call cleanup() on a mode module if it has one."""
    if hasattr(mod, 'cleanup'):
        try:
            mod.cleanup()
        except Exception:
            pass

def _mode_fps(name):
    """Return the ideal sleep interval for a mode.
    Display is 60Hz and USB bandwidth is not a bottleneck (~5MB/s at q85
    vs ~35MB/s available), so the limit is host-side frame generation speed.
    """
    return {
        'visualizer': 1/60,  # 60fps — audio needs smooth updates
        'matrix':     1/30,  # 30fps — good balance for rain effect
        'news':       1/30,  # 30fps — smooth text scrolling
        'ticker':     1/30,  # 30fps — smooth scroll
        'nowplaying': 1/10,  # 10fps — progress bar updates
        'sysmon':     1/2,   # 2fps  — sensor data doesn't change faster
        'clock':      1/15,  # 15fps — animated weather icon
        'docker':     1/2,   # 2fps  — container stats refresh
        'netmon':     1/2,   # 2fps  — connection updates
        'pomodoro':   1,     # 1fps  — countdown seconds
    }.get(name, 1.0)

def mode_single(disp, name, quality):
    """Run a single display mode in a loop."""
    mod = _load_mode(name)
    if not mod:
        return
    _init_mode(mod)
    interval = _mode_fps(name)
    rw, rh = disp.render_w, disp.render_h
    log(f"Mode '{name}' running ({1/interval:.0f}fps, quality={quality}, {rw}x{rh})")
    try:
        while True:
            img = mod.render_frame(rw, rh)
            jpeg = image_to_jpeg(img, quality)
            if not disp.send(jpeg):
                disp.wait_for_device()
            time.sleep(interval)
    finally:
        _cleanup_mode(mod)

def mode_rotate(disp, mode_names, delay, quality):
    """Rotate through multiple display modes, switching every `delay` seconds."""
    rw, rh = disp.render_w, disp.render_h
    log(f"Rotating {len(mode_names)} modes every {delay}s: {', '.join(mode_names)}")
    while True:
        for name in mode_names:
            mod = _load_mode(name)
            if not mod:
                continue
            _init_mode(mod)
            interval = _mode_fps(name)
            log(f"Showing '{name}' for {delay}s")
            t_end = time.monotonic() + delay
            try:
                while time.monotonic() < t_end:
                    img = mod.render_frame(rw, rh)
                    jpeg = image_to_jpeg(img, quality)
                    if not disp.send(jpeg):
                        disp.wait_for_device()
                    time.sleep(interval)
            finally:
                _cleanup_mode(mod)

# ── Mode: test ──────────────────────────────────────────────────────
def mode_test(disp, quality):
    img = make_test_pattern(disp.w, disp.h)
    jpeg = image_to_jpeg(img, quality)
    log(f"Test pattern ({len(jpeg)//1024}KB)")
    while True:
        if not disp.send(jpeg):
            disp.wait_for_device()
            img = make_test_pattern(disp.w, disp.h)
            jpeg = image_to_jpeg(img, quality)
        time.sleep(5)

# ── Signal handler ──────────────────────────────────────────────────
def handle_sigterm(signum, frame):
    log("SIGTERM, shutting down...")
    cleanup_children()
    for f in [PIDFILE, STATEFILE]:
        try:
            os.unlink(f)
        except (FileNotFoundError, PermissionError):
            pass
    sys.exit(0)

# ── Config file ─────────────────────────────────────────────────────
CONFIGFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yml')

def load_config():
    """Load config.yml and return (mode, target, quality, fps, delay, loop, show_modes)."""
    try:
        import yaml
    except ImportError:
        return None
    if not os.path.exists(CONFIGFILE):
        return None
    try:
        with open(CONFIGFILE) as f:
            cfg = yaml.safe_load(f)
    except Exception as e:
        log(f"Config error: {e}")
        return None

    mode = cfg.get('mode', 'sysmon')
    quality = cfg.get('quality', 0)
    fps = cfg.get('fps', 24)
    rotate = cfg.get('rotate_display', 0)
    loop = False
    delay = 30
    show_modes = None
    target = mode

    if mode == 'rotate':
        rot = cfg.get('rotate', {})
        modes_list = rot.get('modes', ['sysmon'])
        if 'all' in modes_list:
            show_modes = ALL_MODES
        else:
            show_modes = [m for m in modes_list if m in ALL_MODES]
        delay = rot.get('delay', 30)
        target = f"{', '.join(show_modes)} ({delay}s each)"
    elif mode == 'url':
        target = cfg.get('url', 'http://localhost/')
    elif mode == 'video':
        vcfg = cfg.get('video', {})
        target = vcfg.get('source', '')
        loop = vcfg.get('loop', False)
    elif mode == 'image':
        target = cfg.get('image', '')
    elif mode in ALL_MODES:
        target = mode

    return mode, target, quality, fps, delay, loop, show_modes, rotate

# ── Main ────────────────────────────────────────────────────────────
def main():
    import argparse

    modes_list = ' '.join(ALL_MODES)
    parser = argparse.ArgumentParser(
        prog='tinyscreen',
        description='ArtInChip USB bar display driver',
        epilog='Examples:\n'
               '  tinyscreen                                       # run from config.yml\n'
               '  tinyscreen --sysmon                              # system monitor\n'
               '  tinyscreen --matrix                              # matrix rain\n'
               '  tinyscreen --ticker                              # crypto prices\n'
               '  tinyscreen --show all --delay 30                 # rotate all modes\n'
               '  tinyscreen --show sysmon matrix ticker --delay 20\n'
               '  tinyscreen --url https://example.com\n'
               '  tinyscreen --video "https://youtu.be/..."        # YouTube 4K\n'
               '  tinyscreen --off\n'
               f'\nAvailable modes: {modes_list}\n'
               f'Config file: {CONFIGFILE}\n',
        formatter_class=argparse.RawDescriptionHelpFormatter)

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--url', help='Display a website (live virtual display + browser)')
    group.add_argument('--video', help='Play a video or YouTube URL (fetches up to 4K)')
    group.add_argument('--image', help='Display a static image')
    group.add_argument('--sysmon', action='store_true', help='System monitor dashboard')
    group.add_argument('--ticker', action='store_true', help='Crypto price ticker')
    group.add_argument('--clock', action='store_true', help='Clock + weather + system info')
    group.add_argument('--matrix', action='store_true', help='Matrix digital rain')
    group.add_argument('--visualizer', action='store_true', help='Audio spectrum visualizer')
    group.add_argument('--nowplaying', action='store_true', help='Now playing (MPRIS)')
    group.add_argument('--docker', action='store_true', help='Docker container monitor')
    group.add_argument('--netmon', action='store_true', help='Network connections monitor')
    group.add_argument('--news', action='store_true', help='RSS news crawl')
    group.add_argument('--pomodoro', action='store_true', help='Pomodoro focus timer')
    group.add_argument('--show', nargs='+', metavar='MODE',
                       help='Rotate through modes (use "all" for all, or list names)')
    group.add_argument('--test', action='store_true', help='Show test pattern')
    group.add_argument('--off', action='store_true', help='Stop running instance')
    group.add_argument('--status', action='store_true', help='Show status')

    parser.add_argument('--delay', type=int, default=30,
                        help='Seconds per mode when using --show (default: 30)')
    parser.add_argument('--rotate', type=int, default=0, choices=[0, 90, 180, 270],
                        help='Rotate output (0, 90, 180, 270 degrees)')
    parser.add_argument('--fps', type=int, default=24, help='Framerate (default: 24)')
    parser.add_argument('-q', '--quality', type=int, default=0,
                        help='JPEG quality 1-100 (default: auto)')
    parser.add_argument('--loop', action='store_true', help='Loop video')
    parser.add_argument('--fg', action='store_true', help='Run in foreground')

    args = parser.parse_args()

    # --off
    if args.off:
        _stop_others()
        return

    # --status
    if args.status:
        pid = read_pid()
        if not is_running(pid):
            print("tinyscreen is not running.")
        else:
            try:
                with open(STATEFILE) as f:
                    st = json.load(f)
                print(f"tinyscreen running (PID {pid})")
                print(f"  Mode:    {st.get('mode', '?')}")
                print(f"  Target:  {st.get('target', '?')}")
                print(f"  Started: {st.get('started', '?')}")
            except Exception:
                print(f"tinyscreen running (PID {pid})")
        print(f"  Log:     {LOGFILE}")
        return

    # Stop any existing instance
    stop_existing()

    # Determine mode — check CLI flags first, fall back to config.yml
    show_modes = None
    mode = target = None

    if args.url:
        mode, target = 'url', args.url
    elif args.video:
        mode, target = 'video', args.video
    elif args.image:
        mode, target = 'image', args.image
    elif args.show:
        show_modes = ALL_MODES if 'all' in args.show else [m for m in args.show if m in ALL_MODES]
        if not show_modes:
            print(f"No valid modes. Available: {', '.join(ALL_MODES)}")
            return
        mode, target = 'rotate', f"{', '.join(show_modes)} ({args.delay}s each)"
    elif args.test:
        mode, target = 'test', 'test pattern'
    else:
        # Check single mode flags
        for m in ALL_MODES:
            if getattr(args, m, False):
                mode, target = m, m
                break

    # No CLI mode specified — load from config.yml
    if mode is None:
        cfg = load_config()
        if cfg:
            mode, target, cfg_quality, cfg_fps, cfg_delay, cfg_loop, cfg_show, cfg_rotate = cfg
            if not args.quality:
                args.quality = cfg_quality
            if args.fps == 24:
                args.fps = cfg_fps
            if args.delay == 30:
                args.delay = cfg_delay
            if not args.rotate:
                args.rotate = cfg_rotate
            if cfg_loop:
                args.loop = True
            if cfg_show:
                show_modes = cfg_show
            print(f"Loaded config from {CONFIGFILE}")
        else:
            mode, target = 'sysmon', 'sysmon'
            print("No mode specified and no config.yml found, defaulting to --sysmon")

    if not args.fg:
        print(f"tinyscreen: {mode} -> {target}")
        print(f"  Log:  tail -f {LOGFILE}")
        print(f"  Stop: tinyscreen --off")
        daemonize()

    signal.signal(signal.SIGTERM, handle_sigterm)
    write_pid()
    log(f"tinyscreen starting ({mode}: {target})")

    disp = Display(rotate=args.rotate)
    disp.wait_for_device()
    if args.rotate:
        log(f"Output rotated {args.rotate}°, render size {disp.render_w}x{disp.render_h}")
    write_state(mode, target)

    try:
        quality = args.quality or 80
        if args.video:
            mode_video(disp, args.video, args.quality or 70, args.fps, args.loop)
        elif args.url:
            mode_url(disp, args.url, args.quality or 75, args.fps)
        elif args.image:
            mode_image(disp, args.image, args.quality or 85)
        elif mode == 'rotate':
            mode_rotate(disp, show_modes, args.delay, quality)
        elif mode == 'test':
            mode_test(disp, quality)
        elif mode in ALL_MODES:
            mode_single(disp, mode, quality)
    except KeyboardInterrupt:
        log("Interrupted.")
    except Exception as e:
        log(f"ERROR: {e}")
        import traceback
        traceback.print_exc(file=sys.stderr)
    finally:
        disp.release()
        cleanup_children()
        for f in [PIDFILE, STATEFILE]:
            try:
                os.unlink(f)
            except (FileNotFoundError, PermissionError):
                pass
        log("tinyscreen stopped.")


if __name__ == '__main__':
    main()
