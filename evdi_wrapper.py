"""Minimal ctypes wrapper for libevdi.so.1 -- the EVDI userspace library."""

import ctypes
import os


# ---------------------------------------------------------------------------
# Load library
# ---------------------------------------------------------------------------

def _find_libevdi():
    """Find libevdi.so.1 on the system."""
    for name in ['libevdi.so.1', 'libevdi.so']:
        try:
            return ctypes.cdll.LoadLibrary(name)
        except OSError:
            pass
    for path in ['/usr/lib/x86_64-linux-gnu/libevdi.so.1',
                 '/usr/local/lib/libevdi.so.1',
                 '/usr/lib/libevdi.so.1']:
        if os.path.exists(path):
            return ctypes.cdll.LoadLibrary(path)
    raise RuntimeError("libevdi.so.1 not found. Install: sudo apt install libevdi1")


_lib = _find_libevdi()


# ---------------------------------------------------------------------------
# C struct definitions (match evdi_lib.h exactly)
# ---------------------------------------------------------------------------

class EvdiRect(ctypes.Structure):
    _fields_ = [
        ('x1', ctypes.c_int), ('y1', ctypes.c_int),
        ('x2', ctypes.c_int), ('y2', ctypes.c_int),
    ]


class EvdiMode(ctypes.Structure):
    _fields_ = [
        ('width', ctypes.c_int),
        ('height', ctypes.c_int),
        ('refresh_rate', ctypes.c_int),
        ('bits_per_pixel', ctypes.c_int),
        ('pixel_format', ctypes.c_uint),
    ]


class EvdiBuffer(ctypes.Structure):
    _fields_ = [
        ('id', ctypes.c_int),
        ('buffer', ctypes.c_void_p),
        ('width', ctypes.c_int),
        ('height', ctypes.c_int),
        ('stride', ctypes.c_int),
        ('rects', ctypes.POINTER(EvdiRect)),
        ('rect_count', ctypes.c_int),
    ]


class EvdiCursorSet(ctypes.Structure):
    _fields_ = [
        ('hot_x', ctypes.c_int32), ('hot_y', ctypes.c_int32),
        ('width', ctypes.c_uint32), ('height', ctypes.c_uint32),
        ('enabled', ctypes.c_uint8),
        ('buffer_length', ctypes.c_uint32),
        ('buffer', ctypes.POINTER(ctypes.c_uint32)),
        ('pixel_format', ctypes.c_uint32), ('stride', ctypes.c_uint32),
    ]


class EvdiCursorMove(ctypes.Structure):
    _fields_ = [
        ('x', ctypes.c_int32), ('y', ctypes.c_int32),
    ]


class EvdiDdcciData(ctypes.Structure):
    _fields_ = [
        ('address', ctypes.c_uint16), ('flags', ctypes.c_uint16),
        ('buffer_length', ctypes.c_uint32),
        ('buffer', ctypes.POINTER(ctypes.c_uint8)),
    ]


class EvdiVersion(ctypes.Structure):
    # C fields: version_major, version_minor, version_patchlevel (evdi_lib_version)
    _fields_ = [
        ('version_major', ctypes.c_int),
        ('version_minor', ctypes.c_int),
        ('version_patchlevel', ctypes.c_int),
    ]


# Callback function types (match evdi_event_context field signatures)
DPMS_HANDLER = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p)
MODE_CHANGED_HANDLER = ctypes.CFUNCTYPE(None, EvdiMode, ctypes.c_void_p)
UPDATE_READY_HANDLER = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p)
CRTC_STATE_HANDLER = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_void_p)
CURSOR_SET_HANDLER = ctypes.CFUNCTYPE(None, EvdiCursorSet, ctypes.c_void_p)
CURSOR_MOVE_HANDLER = ctypes.CFUNCTYPE(None, EvdiCursorMove, ctypes.c_void_p)
DDCCI_HANDLER = ctypes.CFUNCTYPE(None, EvdiDdcciData, ctypes.c_void_p)


class EvdiEventContext(ctypes.Structure):
    _fields_ = [
        ('dpms_handler', DPMS_HANDLER),
        ('mode_changed_handler', MODE_CHANGED_HANDLER),
        ('update_ready_handler', UPDATE_READY_HANDLER),
        ('crtc_state_handler', CRTC_STATE_HANDLER),
        ('cursor_set_handler', CURSOR_SET_HANDLER),
        ('cursor_move_handler', CURSOR_MOVE_HANDLER),
        ('ddcci_data_handler', DDCCI_HANDLER),
        ('user_data', ctypes.c_void_p),
    ]


# ---------------------------------------------------------------------------
# C function signatures
# ---------------------------------------------------------------------------

_lib.evdi_check_device.argtypes = [ctypes.c_int]
_lib.evdi_check_device.restype = ctypes.c_int

_lib.evdi_add_device.argtypes = []
_lib.evdi_add_device.restype = ctypes.c_int

_lib.evdi_open.argtypes = [ctypes.c_int]
_lib.evdi_open.restype = ctypes.c_void_p

_lib.evdi_close.argtypes = [ctypes.c_void_p]
_lib.evdi_close.restype = None

_lib.evdi_connect.argtypes = [
    ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint32]
_lib.evdi_connect.restype = None

_lib.evdi_disconnect.argtypes = [ctypes.c_void_p]
_lib.evdi_disconnect.restype = None

_lib.evdi_register_buffer.argtypes = [ctypes.c_void_p, EvdiBuffer]
_lib.evdi_register_buffer.restype = None

_lib.evdi_unregister_buffer.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.evdi_unregister_buffer.restype = None

_lib.evdi_request_update.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.evdi_request_update.restype = ctypes.c_bool

_lib.evdi_grab_pixels.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(EvdiRect), ctypes.POINTER(ctypes.c_int)]
_lib.evdi_grab_pixels.restype = None

_lib.evdi_handle_events.argtypes = [ctypes.c_void_p, ctypes.POINTER(EvdiEventContext)]
_lib.evdi_handle_events.restype = None

_lib.evdi_get_event_ready.argtypes = [ctypes.c_void_p]
_lib.evdi_get_event_ready.restype = ctypes.c_int

_lib.evdi_get_lib_version.argtypes = [ctypes.POINTER(EvdiVersion)]
_lib.evdi_get_lib_version.restype = None


# ---------------------------------------------------------------------------
# Python API
# ---------------------------------------------------------------------------

# Device status enum (matches evdi_device_status)
AVAILABLE = 0
UNRECOGNIZED = 1
NOT_PRESENT = 2


def get_lib_version():
    """Returns (major, minor, patch) tuple."""
    v = EvdiVersion()
    _lib.evdi_get_lib_version(ctypes.byref(v))
    return (v.version_major, v.version_minor, v.version_patchlevel)


def check_device(device_index):
    """Check if EVDI device slot exists. Returns AVAILABLE/UNRECOGNIZED/NOT_PRESENT."""
    return _lib.evdi_check_device(device_index)


def add_device():
    """Ask kernel module to add a new EVDI device. Returns device index or negative on error."""
    return _lib.evdi_add_device()


def open_device(device_index):
    """Open an EVDI device. Returns handle (opaque pointer) or None."""
    handle = _lib.evdi_open(device_index)
    if handle is None or handle == 0:
        return None
    return handle


def close_device(handle):
    _lib.evdi_close(handle)


def connect(handle, edid_bytes, pixel_area_limit=0):
    """Connect with EDID. pixel_area_limit=0 means no limit."""
    _lib.evdi_connect(handle, edid_bytes, len(edid_bytes), pixel_area_limit)


def disconnect(handle):
    _lib.evdi_disconnect(handle)


def register_buffer(handle, buf_id, buffer, width, height, stride,
                    rects_array, rect_count):
    """Register a pixel buffer. rect_count is the capacity of rects_array."""
    buf = EvdiBuffer()
    buf.id = buf_id
    buf.buffer = buffer
    buf.width = width
    buf.height = height
    buf.stride = stride
    buf.rects = rects_array
    buf.rect_count = rect_count
    _lib.evdi_register_buffer(handle, buf)
    return buf


def unregister_buffer(handle, buf_id):
    _lib.evdi_unregister_buffer(handle, buf_id)


def request_update(handle, buf_id):
    """Request a buffer update. Returns True if update available immediately."""
    return _lib.evdi_request_update(handle, buf_id)


def grab_pixels(handle, rects_array, num_rects_ptr):
    """Grab pixels into the registered buffer. num_rects_ptr is updated with dirty rect count."""
    _lib.evdi_grab_pixels(handle, rects_array, num_rects_ptr)


def get_event_fd(handle):
    """Get the selectable file descriptor for event readiness."""
    return _lib.evdi_get_event_ready(handle)


def handle_events(handle, event_ctx):
    """Process pending events, invoking callbacks in event_ctx."""
    _lib.evdi_handle_events(handle, ctypes.byref(event_ctx))
