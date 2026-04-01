"""
Audio spectrum visualizer mode for tinyscreen bar display.
Captures system audio via PulseAudio monitor source and renders
a full-width equalizer with reflection effect.
"""

import struct
import subprocess
import time
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# --- Visual style (dark theme) ---
BG = (10, 12, 18)
PANEL_BG = (18, 22, 32)
BORDER = (35, 42, 58)
ACCENT = (0, 170, 255)
TEXT = (200, 210, 225)
TEXT_DIM = (100, 110, 130)
GREEN = (0, 220, 100)
RED = (255, 60, 60)
CYAN = (0, 220, 220)
PURPLE = (160, 100, 255)

# --- Font cache ---
_fonts = {}
def font(size):
    if size not in _fonts:
        for path in ['/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
                     '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
            try:
                _fonts[size] = ImageFont.truetype(path, size)
                return _fonts[size]
            except:
                continue
        _fonts[size] = ImageFont.load_default()
    return _fonts[size]

# --- Audio capture state ---
_parec_proc = None
_sample_rate = 44100
_chunk_samples = 2048
_chunk_bytes = _chunk_samples * 2  # s16le = 2 bytes per sample
_monitor_device = 'alsa_output.pci-0000_00_1f.3.analog-stereo.monitor'

# Smoothing state for bars
_prev_magnitudes = None


def _detect_monitor_source():
    """Try to find the default PulseAudio monitor source."""
    global _monitor_device
    try:
        result = subprocess.run(
            ['pactl', 'get-default-sink'],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0 and result.stdout.strip():
            _monitor_device = result.stdout.strip() + '.monitor'
            return
    except Exception:
        pass
    # Fallback: try to list sinks and pick the first monitor
    try:
        result = subprocess.run(
            ['pactl', 'list', 'short', 'sources'],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = line.split('\t')
                if len(parts) >= 2 and 'monitor' in parts[1].lower():
                    _monitor_device = parts[1]
                    return
    except Exception:
        pass


def init():
    """Start the parec subprocess to capture audio."""
    global _parec_proc
    _detect_monitor_source()
    try:
        _parec_proc = subprocess.Popen(
            [
                'parec',
                '--format=s16le',
                '--channels=1',
                '--rate={}'.format(_sample_rate),
                '--device={}'.format(_monitor_device),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        _parec_proc = None
    except Exception:
        _parec_proc = None


def cleanup():
    """Stop the parec subprocess."""
    global _parec_proc
    if _parec_proc is not None:
        try:
            _parec_proc.terminate()
            _parec_proc.wait(timeout=2)
        except Exception:
            try:
                _parec_proc.kill()
            except Exception:
                pass
        _parec_proc = None


def _read_audio_chunk():
    """Read one chunk of raw PCM samples from parec. Returns numpy array or None."""
    if _parec_proc is None or _parec_proc.poll() is not None:
        return None
    try:
        raw = _parec_proc.stdout.read(_chunk_bytes)
        if raw is None or len(raw) < _chunk_bytes:
            return None
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
        return samples
    except Exception:
        return None


def _lerp_color(c1, c2, t):
    """Linearly interpolate between two RGB tuples."""
    t = max(0.0, min(1.0, t))
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def render_frame(w=1920, h=440):
    """Render one frame of the audio spectrum visualizer."""
    global _prev_magnitudes

    img = Image.new('RGB', (w, h), BG)
    draw = ImageDraw.Draw(img)

    samples = _read_audio_chunk()

    if samples is None:
        # No audio available -- draw flat line and message
        mid_y = h // 2
        draw.line([(0, mid_y), (w, mid_y)], fill=BORDER, width=2)
        msg = "No Audio"
        f = font(36)
        bbox = draw.textbbox((0, 0), msg, font=f)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((w - tw) // 2, (h - th) // 2 - 30), msg, fill=TEXT_DIM, font=f)

        sub = "Waiting for PulseAudio stream..."
        f2 = font(18)
        bbox2 = draw.textbbox((0, 0), sub, font=f2)
        tw2 = bbox2[2] - bbox2[0]
        draw.text(((w - tw2) // 2, (h + th) // 2), sub, fill=BORDER, font=f2)
        return img

    # Apply a Hann window to reduce spectral leakage
    window = np.hanning(len(samples))
    windowed = samples * window

    # FFT
    fft_data = np.fft.rfft(windowed)
    magnitudes = np.abs(fft_data)

    # We only care about the first half (positive frequencies), skip DC
    magnitudes = magnitudes[1:]

    # Number of bars to display
    num_bars = min(128, w // 12)

    # Bin the FFT data into num_bars groups (logarithmic spacing)
    freq_bins = np.logspace(0, np.log10(len(magnitudes)), num=num_bars + 1, dtype=int)
    freq_bins = np.clip(freq_bins, 1, len(magnitudes))
    bar_values = np.zeros(num_bars)
    for i in range(num_bars):
        lo = freq_bins[i]
        hi = max(freq_bins[i + 1], lo + 1)
        if hi > len(magnitudes):
            hi = len(magnitudes)
        if lo >= hi:
            bar_values[i] = 0
        else:
            bar_values[i] = np.mean(magnitudes[lo:hi])

    # Normalize to 0..1
    peak = np.max(bar_values) if np.max(bar_values) > 0 else 1.0
    bar_values = bar_values / peak

    # Smooth with previous frame
    if _prev_magnitudes is not None and len(_prev_magnitudes) == num_bars:
        smoothing = 0.3
        bar_values = smoothing * _prev_magnitudes + (1.0 - smoothing) * bar_values
    _prev_magnitudes = bar_values.copy()

    # Drawing geometry
    bar_area_top = 20
    bar_area_bottom = h * 0.55  # bars occupy top ~55%
    reflection_top = bar_area_bottom + 4
    reflection_bottom = h - 10
    max_bar_height = bar_area_bottom - bar_area_top

    bar_total_width = w / num_bars
    bar_gap = max(1, int(bar_total_width * 0.2))
    bar_w = int(bar_total_width - bar_gap)

    for i in range(num_bars):
        t = i / max(num_bars - 1, 1)  # 0..1 across frequency range
        color = _lerp_color(CYAN, PURPLE, t)

        bar_h = int(bar_values[i] * max_bar_height)
        if bar_h < 2:
            bar_h = 2

        x = int(i * bar_total_width + bar_gap / 2)
        y_top = int(bar_area_bottom - bar_h)
        y_bot = int(bar_area_bottom)

        # Main bar
        draw.rectangle([x, y_top, x + bar_w, y_bot], fill=color)

        # Bright cap on top of bar
        cap_h = max(2, bar_h // 20)
        bright = _lerp_color(color, (255, 255, 255), 0.5)
        draw.rectangle([x, y_top, x + bar_w, y_top + cap_h], fill=bright)

        # Reflection (dimmed, inverted)
        refl_max_h = reflection_bottom - reflection_top
        refl_h = min(int(bar_h * 0.5), int(refl_max_h))
        if refl_h > 1:
            dim = (color[0] // 4, color[1] // 4, color[2] // 4)
            draw.rectangle(
                [x, int(reflection_top), x + bar_w, int(reflection_top) + refl_h],
                fill=dim,
            )

    # Thin separator line between bars and reflection
    draw.line(
        [(0, int(bar_area_bottom) + 1), (w, int(bar_area_bottom) + 1)],
        fill=BORDER, width=1,
    )

    return img
