"""
Touch input handler for ArtInChip USB bar displays.

Reads raw input events from /dev/input/eventN for the ArtInChip HID touchscreen.
Provides a simple callback-based API for tap, swipe, and hold gestures.

Usage:
    touch = TouchInput()
    touch.on_tap = lambda x, y: print(f"Tap at {x}, {y}")
    touch.on_swipe_left = lambda: print("Swipe left")
    touch.start()  # spawns background thread
    ...
    touch.stop()
"""

import os
import struct
import threading
import time
import glob


# Linux input event format: struct input_event { timeval time; u16 type; u16 code; s32 value; }
# On 64-bit: 8 (tv_sec) + 8 (tv_usec) + 2 (type) + 2 (code) + 4 (value) = 24 bytes
EVENT_SIZE = 24
EVENT_FMT = 'llHHi'

# Event types
EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03

# ABS codes
ABS_X = 0x00
ABS_Y = 0x01
ABS_MT_SLOT = 0x2F
ABS_MT_TOUCH_MAJOR = 0x30
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TRACKING_ID = 0x39

# BTN codes
BTN_TOUCH = 0x14A

# Gesture thresholds
SWIPE_MIN_DISTANCE = 100   # pixels
SWIPE_MAX_TIME = 0.5       # seconds
TAP_MAX_DISTANCE = 30      # pixels
TAP_MAX_TIME = 0.3         # seconds
HOLD_MIN_TIME = 0.8        # seconds


def find_touch_device():
    """Find the ArtInChip touchscreen event device."""
    for event_dir in sorted(glob.glob('/sys/class/input/event*')):
        device_dir = os.path.join(event_dir, 'device')
        # Check if this device is our ArtInChip display
        try:
            name_path = os.path.join(device_dir, 'name')
            if os.path.exists(name_path):
                with open(name_path) as f:
                    name = f.read().strip()
                if 'ArtInChip' in name:
                    event_name = os.path.basename(event_dir)
                    return f'/dev/input/{event_name}'
        except Exception:
            pass
    return None


class TouchInput:
    """Reads touch events and detects gestures."""

    def __init__(self, device_path=None, screen_w=1920, screen_h=440):
        self.device_path = device_path or find_touch_device()
        self.screen_w = screen_w
        self.screen_h = screen_h
        self._thread = None
        self._running = False
        self._fd = None

        # Touch state
        self._touch_down = False
        self._start_x = self._start_y = 0
        self._cur_x = self._cur_y = 0
        self._start_time = 0

        # Axis ranges (will be read from device)
        self._abs_x_max = 1920
        self._abs_y_max = 440

        # Gesture callbacks
        self.on_tap = None          # (x, y) in screen coords
        self.on_swipe_left = None   # ()
        self.on_swipe_right = None  # ()
        self.on_swipe_up = None     # ()
        self.on_swipe_down = None   # ()
        self.on_hold = None         # (x, y) in screen coords
        self.on_touch_down = None   # (x, y)
        self.on_touch_up = None     # (x, y)

    def _read_axis_range(self, axis):
        """Read axis min/max via EVIOCGABS ioctl."""
        import fcntl
        import array
        EVIOCGABS = lambda a: 0x80184540 + a
        buf = array.array('i', [0, 0, 0, 0, 0, 0])
        try:
            fcntl.ioctl(self._fd, EVIOCGABS(axis), buf)
            return buf[1], buf[2]  # min, max
        except Exception:
            return 0, 1920

    def start(self):
        """Start reading touch events in a background thread."""
        if not self.device_path:
            return False
        try:
            self._fd = os.open(self.device_path, os.O_RDONLY)
        except (PermissionError, FileNotFoundError) as e:
            return False

        # Read axis ranges
        _, self._abs_x_max = self._read_axis_range(ABS_MT_POSITION_X)
        if self._abs_x_max == 0:
            _, self._abs_x_max = self._read_axis_range(ABS_X)
        _, self._abs_y_max = self._read_axis_range(ABS_MT_POSITION_Y)
        if self._abs_y_max == 0:
            _, self._abs_y_max = self._read_axis_range(ABS_Y)
        if self._abs_x_max == 0:
            self._abs_x_max = 1920
        if self._abs_y_max == 0:
            self._abs_y_max = 440

        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        if self._fd is not None:
            try:
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None
        if self._thread:
            self._thread.join(timeout=2)

    def _scale_x(self, raw):
        """Scale raw touch X to screen coordinates."""
        return int(raw * self.screen_w / max(self._abs_x_max, 1))

    def _scale_y(self, raw):
        """Scale raw touch Y to screen coordinates."""
        return int(raw * self.screen_h / max(self._abs_y_max, 1))

    def _read_loop(self):
        while self._running:
            try:
                data = os.read(self._fd, EVENT_SIZE)
                if len(data) < EVENT_SIZE:
                    continue
                tv_sec, tv_usec, ev_type, code, value = struct.unpack(EVENT_FMT, data)

                if ev_type == EV_ABS:
                    if code in (ABS_X, ABS_MT_POSITION_X):
                        self._cur_x = value
                    elif code in (ABS_Y, ABS_MT_POSITION_Y):
                        self._cur_y = value
                    elif code == ABS_MT_TRACKING_ID:
                        if value >= 0 and not self._touch_down:
                            # Finger down
                            self._touch_down = True
                            self._start_x = self._cur_x
                            self._start_y = self._cur_y
                            self._start_time = time.monotonic()
                            sx = self._scale_x(self._cur_x)
                            sy = self._scale_y(self._cur_y)
                            if self.on_touch_down:
                                self.on_touch_down(sx, sy)
                        elif value == -1 and self._touch_down:
                            # Finger up
                            self._touch_down = False
                            self._handle_gesture()
                            sx = self._scale_x(self._cur_x)
                            sy = self._scale_y(self._cur_y)
                            if self.on_touch_up:
                                self.on_touch_up(sx, sy)

                elif ev_type == EV_KEY and code == BTN_TOUCH:
                    if value == 1 and not self._touch_down:
                        self._touch_down = True
                        self._start_x = self._cur_x
                        self._start_y = self._cur_y
                        self._start_time = time.monotonic()
                        if self.on_touch_down:
                            self.on_touch_down(self._scale_x(self._cur_x),
                                               self._scale_y(self._cur_y))
                    elif value == 0 and self._touch_down:
                        self._touch_down = False
                        self._handle_gesture()
                        if self.on_touch_up:
                            self.on_touch_up(self._scale_x(self._cur_x),
                                             self._scale_y(self._cur_y))

            except OSError:
                if self._running:
                    time.sleep(0.1)
                break

    def _handle_gesture(self):
        dt = time.monotonic() - self._start_time
        dx = self._cur_x - self._start_x
        dy = self._cur_y - self._start_y
        dist = (dx * dx + dy * dy) ** 0.5
        sx = self._scale_x(self._cur_x)
        sy = self._scale_y(self._cur_y)

        # Scale distance to screen coords for threshold comparison
        sdx = self._scale_x(abs(dx))
        sdy = self._scale_y(abs(dy))

        if dt >= HOLD_MIN_TIME and dist < TAP_MAX_DISTANCE * (self._abs_x_max / self.screen_w):
            if self.on_hold:
                self.on_hold(sx, sy)
        elif dt < SWIPE_MAX_TIME and sdx > SWIPE_MIN_DISTANCE and sdx > sdy * 2:
            if dx > 0 and self.on_swipe_right:
                self.on_swipe_right()
            elif dx < 0 and self.on_swipe_left:
                self.on_swipe_left()
        elif dt < SWIPE_MAX_TIME and sdy > SWIPE_MIN_DISTANCE and sdy > sdx * 2:
            if dy > 0 and self.on_swipe_down:
                self.on_swipe_down()
            elif dy < 0 and self.on_swipe_up:
                self.on_swipe_up()
        elif dt < TAP_MAX_TIME and dist < TAP_MAX_DISTANCE * (self._abs_x_max / self.screen_w):
            if self.on_tap:
                self.on_tap(sx, sy)
