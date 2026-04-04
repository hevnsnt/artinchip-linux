#!/usr/bin/env python3
"""tinyscreen-evdi -- bridge EVDI virtual monitor to ArtInChip USB display.

Creates a virtual 1920x440 DRM output that appears as a real monitor.
Receives framebuffer updates from the desktop compositor, JPEG-encodes
them, and streams them to the USB display.

Usage:
    tinyscreen-evdi              # start (daemonizes)
    tinyscreen-evdi --fg         # foreground
    tinyscreen-evdi --status     # check if running
    tinyscreen-evdi --stop       # stop
"""

import argparse
import ctypes
import os
import select
import signal
import sys
import time

# Ensure tinyscreen modules are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
import evdi_wrapper as evdi
from tinyscreen import (find_device, setup_device, get_params, authenticate,
                        send_frame, image_to_jpeg, log)

EDID_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'edid_1920x440.bin')

# Separate PID/log from main tinyscreen daemon
PIDFILE = '/tmp/tinyscreen-evdi.pid'
LOGFILE = '/tmp/tinyscreen-evdi.log'

# Display dimensions
WIDTH = 1920
HEIGHT = 440
STRIDE = WIDTH * 4  # BGRA32 = 4 bytes per pixel
BUF_SIZE = WIDTH * HEIGHT * 4
MAX_FPS = 30
FRAME_INTERVAL = 1.0 / MAX_FPS
MAX_RECTS = 16


class EvdiBridge:
    """Bridges an EVDI virtual display to the ArtInChip USB hardware."""

    def __init__(self):
        self.handle = None
        self.usb_dev = None
        self.usb_fmt = 0
        self.frame_id = 0
        self.running = False

        # Pixel buffer (shared with EVDI kernel module)
        self._buf = (ctypes.c_ubyte * BUF_SIZE)()
        self._buf_ptr = ctypes.cast(self._buf, ctypes.c_void_p)

        # Dirty rectangles (capacity = MAX_RECTS)
        self._rects = (evdi.EvdiRect * MAX_RECTS)()
        self._num_rects = ctypes.c_int(0)

        # Callback state
        self._update_ready = False
        self._reregister_buffer = False
        self._mode = None

        # Load EDID
        with open(EDID_PATH, 'rb') as f:
            self._edid = f.read()
        assert len(self._edid) == 128, f"Bad EDID: {len(self._edid)} bytes"

    # -- EVDI callbacks (must match CFUNCTYPE signatures) --

    def _on_update_ready(self, buf_id, user_data):
        self._update_ready = True

    def _on_mode_changed(self, mode, user_data):
        self._mode = (mode.width, mode.height, mode.refresh_rate, mode.bits_per_pixel)
        log(f"Mode changed: {mode.width}x{mode.height} @ {mode.refresh_rate}Hz, "
            f"{mode.bits_per_pixel}bpp")
        # Re-register buffer after mode change (old buffer is invalidated)
        self._reregister_buffer = True

    def _on_dpms(self, dpms_mode, user_data):
        log(f"DPMS: {dpms_mode}")

    def _on_crtc_state(self, state, user_data):
        log(f"CRTC state: {state}")

    # -- Setup --

    def setup_evdi(self):
        """Initialize EVDI device and connect with our EDID."""
        ver = evdi.get_lib_version()
        log(f"libevdi {ver[0]}.{ver[1]}.{ver[2]}")

        # Find or create an EVDI device
        device_idx = -1
        for i in range(20):
            if evdi.check_device(i) == evdi.AVAILABLE:
                device_idx = i
                break

        if device_idx < 0:
            log("No EVDI device available, adding one...")
            result = evdi.add_device()
            if result < 0:
                log("ERROR: Failed to add EVDI device. Is the module loaded?")
                log("  Try: sudo modprobe evdi initial_device_count=1")
                return False
            # Re-scan after adding
            for i in range(20):
                if evdi.check_device(i) == evdi.AVAILABLE:
                    device_idx = i
                    break

        if device_idx < 0:
            log("ERROR: No EVDI device found after adding")
            return False

        log(f"Opening EVDI device {device_idx}")
        self.handle = evdi.open_device(device_idx)
        if self.handle is None:
            log("ERROR: Failed to open EVDI device")
            return False

        # Connect with our custom EDID
        evdi.connect(self.handle, self._edid, WIDTH * HEIGHT)
        log(f"EVDI connected with {WIDTH}x{HEIGHT} EDID")

        # Register framebuffer -- rect_count is capacity, not initial count
        self._evdi_buf = evdi.register_buffer(
            self.handle, 0, self._buf_ptr,
            WIDTH, HEIGHT, STRIDE,
            self._rects, MAX_RECTS)
        log("Buffer registered")

        return True

    def setup_usb(self):
        """Find and authenticate with the USB display."""
        self.usb_dev = find_device()
        if not self.usb_dev:
            log("USB display not found, will retry...")
            return False
        try:
            setup_device(self.usb_dev)
            _, _, self.usb_fmt, _ = get_params(self.usb_dev)
            if not authenticate(self.usb_dev):
                log("USB authentication failed")
                self.usb_dev = None
                return False
        except OSError as e:
            log(f"USB setup error: {e}")
            self.usb_dev = None
            return False
        log("USB display authenticated")
        return True

    # -- Frame pipeline --

    def grab_and_send(self):
        """Grab pixels from EVDI, JPEG-encode, send to USB display."""
        self._num_rects.value = MAX_RECTS
        evdi.grab_pixels(self.handle, self._rects,
                         ctypes.byref(self._num_rects))

        if self._num_rects.value == 0:
            return

        # Convert BGRA buffer to RGB PIL Image (JPEG doesn't support alpha)
        img = Image.frombuffer('RGB', (WIDTH, HEIGHT),
                               bytes(self._buf), 'raw', 'BGRX', STRIDE, 1)

        jpeg = image_to_jpeg(img, 80)

        try:
            send_frame(self.usb_dev, jpeg, self.usb_fmt, self.frame_id)
            self.frame_id += 1
        except OSError as e:
            log(f"USB send error: {e}")
            self.usb_dev = None

    # -- Main loop --

    def run(self):
        """Main event loop."""
        if not self.setup_evdi():
            return 1
        # Try USB, but don't fail if not connected yet
        self.setup_usb()

        # Build event context -- store callback refs to prevent GC
        self._cb_update = evdi.UPDATE_READY_HANDLER(self._on_update_ready)
        self._cb_mode = evdi.MODE_CHANGED_HANDLER(self._on_mode_changed)
        self._cb_dpms = evdi.DPMS_HANDLER(self._on_dpms)
        self._cb_crtc = evdi.CRTC_STATE_HANDLER(self._on_crtc_state)
        self._cb_cursor_set = evdi.CURSOR_SET_HANDLER(lambda cs, ud: None)
        self._cb_cursor_move = evdi.CURSOR_MOVE_HANDLER(lambda cm, ud: None)
        self._cb_ddcci = evdi.DDCCI_HANDLER(lambda dd, ud: None)

        evt_ctx = evdi.EvdiEventContext()
        evt_ctx.dpms_handler = self._cb_dpms
        evt_ctx.mode_changed_handler = self._cb_mode
        evt_ctx.update_ready_handler = self._cb_update
        evt_ctx.crtc_state_handler = self._cb_crtc
        evt_ctx.cursor_set_handler = self._cb_cursor_set
        evt_ctx.cursor_move_handler = self._cb_cursor_move
        evt_ctx.ddcci_data_handler = self._cb_ddcci
        evt_ctx.user_data = None

        event_fd = evdi.get_event_fd(self.handle)
        log(f"Event loop starting (fd={event_fd}, max {MAX_FPS}fps)")

        self.running = True
        last_frame = 0.0
        frames = 0
        t_start = time.monotonic()

        while self.running:
            try:
                ready, _, _ = select.select([event_fd], [], [], 1.0)
            except (OSError, ValueError):
                break

            if not self.running:
                break

            if ready:
                evdi.handle_events(self.handle, evt_ctx)

            # Re-register buffer after mode change (old one is invalidated)
            if self._reregister_buffer:
                self._reregister_buffer = False
                log("Re-registering buffer after mode change")
                evdi.unregister_buffer(self.handle, 0)
                self._evdi_buf = evdi.register_buffer(
                    self.handle, 0, self._buf_ptr,
                    WIDTH, HEIGHT, STRIDE,
                    self._rects, MAX_RECTS)

            # Reconnect USB independently of frame updates
            now = time.monotonic()
            if self.usb_dev is None and (now - last_frame) >= 2.0:
                self.setup_usb()
                last_frame = now

            # Grab pixels on every frame interval (polling mode).
            # NVIDIA drivers don't signal dirty rects to EVDI sink outputs,
            # so we poll instead of waiting for update_ready events.
            if self.usb_dev is not None and (now - last_frame) >= FRAME_INTERVAL:
                self._update_ready = False
                evdi.request_update(self.handle, 0)
                self.grab_and_send()
                last_frame = now
                frames += 1

                if frames % 300 == 0:
                    elapsed = now - t_start
                    fps = frames / elapsed if elapsed > 0 else 0
                    log(f"{frames} frames, {fps:.1f} avg fps")

        return 0

    def shutdown(self):
        """Clean up EVDI and USB resources."""
        self.running = False
        if self.handle:
            evdi.unregister_buffer(self.handle, 0)
            evdi.disconnect(self.handle)
            evdi.close_device(self.handle)
            self.handle = None
            log("EVDI disconnected")


def main():
    parser = argparse.ArgumentParser(
        prog='tinyscreen-evdi',
        description='Bridge EVDI virtual display to ArtInChip USB bar display')
    parser.add_argument('--fg', action='store_true', help='Run in foreground')
    parser.add_argument('--stop', action='store_true', help='Stop running instance')
    parser.add_argument('--status', action='store_true', help='Show status')
    parser.add_argument('--max-fps', type=int, default=30, help='Max frame rate')
    args = parser.parse_args()

    if args.stop:
        pid = None
        try:
            with open(PIDFILE) as f:
                pid = int(f.read().strip())
        except (FileNotFoundError, ValueError):
            pass
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                print(f"Stopped tinyscreen-evdi (PID {pid})")
            except OSError:
                print("Process not running")
        else:
            print("Not running")
        return

    if args.status:
        try:
            with open(PIDFILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            print(f"tinyscreen-evdi running (PID {pid})")
        except (FileNotFoundError, ValueError, OSError):
            print("tinyscreen-evdi not running")
        return

    global MAX_FPS, FRAME_INTERVAL
    MAX_FPS = args.max_fps
    FRAME_INTERVAL = 1.0 / MAX_FPS

    bridge = EvdiBridge()

    def handle_sigterm(signum, frame):
        log("SIGTERM received")
        bridge.running = False

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    if not args.fg:
        # Daemonize (double-fork)
        if os.fork() > 0:
            sys.exit(0)
        os.setsid()
        if os.fork() > 0:
            sys.exit(0)
        # Redirect log() output to our own log file
        import tinyscreen
        tinyscreen._log_fh = open(LOGFILE, 'a')
        sys.stdout = tinyscreen._log_fh
        sys.stderr = tinyscreen._log_fh

    with open(PIDFILE, 'w') as f:
        f.write(str(os.getpid()))

    log(f"tinyscreen-evdi starting (max {MAX_FPS}fps)")
    try:
        sys.exit(bridge.run())
    finally:
        bridge.shutdown()
        try:
            os.unlink(PIDFILE)
        except FileNotFoundError:
            pass
        log("tinyscreen-evdi stopped")


if __name__ == '__main__':
    main()
