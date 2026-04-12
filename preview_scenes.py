#!/usr/bin/env python3
"""Preview all weather scenes on the tinyscreen display, 20 seconds each."""

import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'modes'))

from PIL import Image, ImageDraw, ImageFont
from tinyscreen import Display, image_to_jpeg
from scenes import SCENE_MAP
from scenes.engine import font, draw_text_shadow

SCENE_ORDER = [
    'rain', 'thunder', 'snow', 'overcast', 'fog',
    'sunny', 'hot', 'partly_cloudy', 'night', 'sunset',
]

SCENE_LABELS = {
    'rain': 'Rain Storm',
    'thunder': 'Thunderstorm',
    'snow': 'Snowfall',
    'overcast': 'Overcast',
    'fog': 'Dense Fog',
    'sunny': 'Sunny Day',
    'hot': 'Extreme Heat',
    'partly_cloudy': 'Partly Cloudy',
    'night': 'Night Sky',
    'sunset': 'Sunset',
}

DURATION = 20  # seconds per scene


def show_title(disp, name, index, total, w, h):
    """Show a title card for 2 seconds."""
    label = SCENE_LABELS.get(name, name)
    img = Image.new('RGB', (w, h), (5, 7, 12))
    draw = ImageDraw.Draw(img)

    title_f = font(60, bold=True)
    sub_f = font(24)
    count_f = font(18)

    # Center the title
    bbox = draw.textbbox((0, 0), label, font=title_f)
    tw = bbox[2] - bbox[0]
    tx = (w - tw) // 2
    ty = h // 2 - 50
    draw.text((tx, ty), label, fill=(0, 210, 255), font=title_f)

    # Scene counter
    counter = f"Scene {index + 1} of {total}"
    bbox2 = draw.textbbox((0, 0), counter, font=sub_f)
    cw = bbox2[2] - bbox2[0]
    draw.text(((w - cw) // 2, ty + 80), counter, fill=(100, 110, 130), font=sub_f)

    # Duration note
    note = f"{DURATION}s preview"
    bbox3 = draw.textbbox((0, 0), note, font=count_f)
    nw = bbox3[2] - bbox3[0]
    draw.text(((w - nw) // 2, ty + 115), note, fill=(65, 75, 100), font=count_f)

    jpeg = image_to_jpeg(img, 85)
    disp.send(jpeg)
    time.sleep(2)


def preview_scene(disp, name, w, h):
    """Render a scene for DURATION seconds."""
    cls = SCENE_MAP.get(name)
    if not cls:
        print(f"  Unknown scene: {name}")
        return

    scene = cls(w, h)
    t_start = time.monotonic()
    frame_count = 0

    while True:
        t = time.monotonic() - t_start
        if t >= DURATION:
            break

        t0 = time.monotonic()
        scene_img = scene.render(t, {})

        # Convert to RGB
        img = Image.new('RGB', (w, h), (10, 12, 18))
        img.paste(scene_img, (0, 0), scene_img)

        # Add scene label overlay
        draw = ImageDraw.Draw(img)
        label = SCENE_LABELS.get(name, name)
        remaining = int(DURATION - t)
        draw_text_shadow(draw, 20, h - 35, f"{label}  ({remaining}s)",
                         (200, 210, 225), font(20), shadow_offset=2)

        jpeg = image_to_jpeg(img, 80)
        disp.send(jpeg)
        frame_count += 1

        # Adaptive sleep targeting ~25fps
        elapsed = time.monotonic() - t0
        sleep_time = (1 / 25) - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

    elapsed = time.monotonic() - t_start
    fps = frame_count / elapsed
    print(f"  {name:16s}: {frame_count} frames in {elapsed:.1f}s = {fps:.1f}fps")
    scene.cleanup()


def main():
    disp = Display(rotate=180)
    print("Connecting to display...")
    disp.wait_for_device()
    w, h = disp.render_w, disp.render_h
    print(f"Display ready: {w}x{h}")

    total = len(SCENE_ORDER)
    print(f"\nPreviewing {total} scenes, {DURATION}s each ({total * (DURATION + 2) // 60}min total)\n")

    for i, name in enumerate(SCENE_ORDER):
        print(f"[{i+1}/{total}] {SCENE_LABELS.get(name, name)}")
        show_title(disp, name, i, total, w, h)
        preview_scene(disp, name, w, h)

    # Done card
    img = Image.new('RGB', (w, h), (5, 7, 12))
    draw = ImageDraw.Draw(img)
    done_f = font(48, bold=True)
    bbox = draw.textbbox((0, 0), "Preview Complete", font=done_f)
    tw = bbox[2] - bbox[0]
    draw.text(((w - tw) // 2, h // 2 - 30), "Preview Complete",
              fill=(0, 220, 100), font=done_f)
    disp.send(image_to_jpeg(img, 85))

    print("\nDone! All scenes previewed.")
    disp.release()


if __name__ == '__main__':
    main()
