"""Hot weather scene -- scorching, intense, mesmerizing.

Blazing fireball sun with animated flame tendrils radiating outward, heat
shimmer distortion, rising ember particles, cracked parched ground, haze
bands, and pulsing red atmosphere. Designed for a 1920x440 bar LCD.
"""

import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from scenes.base import BaseScene
from scenes import engine


class HotScene(BaseScene):
    """Scorching heat scene with fireball sun and heat shimmer effects."""

    def __init__(self, w: int, h: int):
        super().__init__(w, h)

        # Deep burnt-red gradient background
        self._gradient = engine.gradient_fill(w, h, (55, 12, 5), (160, 40, 10))

        # Sun placement and sizing
        self._sun_cx = int(w * 0.65)
        self._sun_cy = int(h * 0.35)
        self._sun_r = int(min(w, h) * 0.22)

        # Pre-compute sun distance field arrays for the hot core
        cx, cy, r = self._sun_cx, self._sun_cy, self._sun_r
        ys = np.arange(h, dtype=np.float32)
        xs = np.arange(w, dtype=np.float32)
        yy, xx = np.meshgrid(ys, xs, indexing='ij')
        self._sun_dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        self._sun_norm = self._sun_dist / max(r, 1)

        # Ember particles -- 30 rising sparks
        self._embers: list[dict] = []
        for _ in range(30):
            self._embers.append(self._spawn_ember(w, h, initial=True))

        # Heat haze bands config -- 10 bands
        rng = random.Random(77)
        self._haze_bands = []
        for i in range(10):
            self._haze_bands.append({
                'base_y': h - 25 - i * 38,
                'speed': 12 + i * 4.5,
                'freq': 0.006 + rng.uniform(-0.002, 0.002),
                'amp': 6 + rng.uniform(0, 5),
                'phase': rng.uniform(0, math.tau),
                'alpha_base': 20 + rng.randint(0, 18),
            })

        # Cracked ground seed
        self._crack_seed = 42

        # Pre-render cracked ground layer (identical every frame)
        self._ground_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        ground_draw = ImageDraw.Draw(self._ground_layer)
        ground_top = h - 20
        ground_draw.rectangle([0, ground_top, w, h], fill=(40, 15, 5, 255))
        crack_rng = random.Random(self._crack_seed)
        crack_color = (80, 40, 15, 255)
        for _ in range(35):
            cx_start = crack_rng.randint(0, w)
            cy_start = crack_rng.randint(ground_top + 2, h - 2)
            segs = crack_rng.randint(3, 8)
            pts = [(cx_start, cy_start)]
            for _ in range(segs):
                dx = crack_rng.randint(-25, 25)
                dy = crack_rng.randint(-4, 4)
                nx_pt = max(0, min(w, pts[-1][0] + dx))
                ny_pt = max(ground_top, min(h, pts[-1][1] + dy))
                pts.append((nx_pt, ny_pt))
            if len(pts) >= 2:
                ground_draw.line(pts, fill=crack_color, width=1)

        # Pre-render ember glow sprites
        self._ember_sprites = [
            engine.glow_sprite(2, (255, 120, 20), alpha_peak=140),
            engine.glow_sprite(3, (255, 160, 40), alpha_peak=120),
            engine.glow_sprite(4, (255, 100, 10), alpha_peak=100),
        ]

        # Pre-render ground-level heat glow
        self._heat_glow = engine.glow_sprite(int(w // 3), (255, 80, 10), alpha_peak=25)

        # Pre-compute vignette mask (heavier, 0.20 strength)
        x_coords = np.linspace(-1, 1, w, dtype=np.float32)
        y_coords = np.linspace(-1, 1, h, dtype=np.float32)
        X, Y = np.meshgrid(x_coords, y_coords)
        self._vignette = np.clip(
            1.0 - 0.20 * (X ** 2 * 0.3 + Y ** 2 * 0.7), 0.55, 1.0)

    @staticmethod
    def _spawn_ember(w, h, initial=False):
        return {
            'x': random.uniform(0, w),
            'y': random.uniform(h * 0.3, h - 25) if initial else random.uniform(h * 0.7, h - 25),
            'vx': random.uniform(-0.35, 0.35),
            'vy': random.uniform(-2.2, -0.4),
            'size': random.randint(1, 4),
            'alpha': random.randint(120, 220),
            'phase': random.uniform(0, math.tau),
            'life': random.uniform(0.2, 1.0) if initial else 1.0,
        }

    # ------------------------------------------------------------------
    # Main render
    # ------------------------------------------------------------------

    def render(self, t: float, weather_data: dict) -> Image.Image:
        w, h = self.w, self.h
        cx, cy, r = self._sun_cx, self._sun_cy, self._sun_r

        # 1. Base gradient
        scene = self._gradient.copy()

        # 2. Pulsing red tint -- slow threatening pulse
        pulse = 0.5 + 0.5 * math.sin(t * 2.5)
        tint_alpha = int(45 * pulse)
        tint_overlay = Image.new('RGBA', (w, h), (200, 20, 0, tint_alpha))
        scene.alpha_composite(tint_overlay)

        # 3. Heat shimmer -- noise-field horizontal displacement
        noise = engine.noise_field(w, h, t, scale=0.02, octaves=2)
        scene_arr = np.array(scene, dtype=np.uint8)

        displacement = (noise * 2.0 - 1.0) * 3.0
        # Stronger shimmer at the bottom half
        vert_grad = np.linspace(0, 1, h, dtype=np.float32).reshape(-1, 1)
        displacement = displacement * vert_grad

        shifted = np.empty_like(scene_arr)
        col_indices = np.arange(w, dtype=np.float32)
        for row in range(h):
            src_cols = np.clip(col_indices - displacement[row], 0, w - 1).astype(np.int32)
            shifted[row] = scene_arr[row, src_cols]

        scene = Image.fromarray(shifted, 'RGBA')

        # 4. Fire sun
        # 4a. Pulsing radius
        pr = r + math.sin(t * 3) * 6
        ipr = int(pr)

        # 4b. Flame tendrils on a separate layer
        fire_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        fire_draw = ImageDraw.Draw(fire_layer)

        num_flames = 20
        for fi in range(num_flames):
            base_angle = fi * math.tau / num_flames
            # Independent multi-sine flicker per tendril
            flicker = (
                math.sin(t * 4.5 + fi * 2.3) * 0.35
                + math.sin(t * 7.2 + fi * 1.7) * 0.2
                + math.sin(t * 11.0 + fi * 3.1) * 0.1
            )
            # Flame length varies per tendril and over time
            flame_len = ipr * (0.35 + 0.55 * (0.5 + 0.5 * math.sin(t * 3.5 + fi * 1.9)))
            # Slight angular wobble
            angle = base_angle + math.sin(t * 2.0 + fi * 0.8) * 0.15

            # 8 overlapping circles from base to tip
            segments = 8
            for si in range(segments):
                frac = si / segments
                r_dist = ipr * 0.65 + flame_len * frac
                # Taper: wide at base, narrow at tip
                blob_r = int(ipr * 0.17 * (1.0 - frac * 0.85))
                if blob_r < 1:
                    continue

                fx = cx + int(math.cos(angle) * r_dist)
                fy = cy + int(math.sin(angle) * r_dist)

                # Color gradient: bright yellow base -> orange -> deep red tip
                cr = 255
                cg = int(220 * (1.0 - frac * 0.88))
                cb = int(90 * (1.0 - frac))
                ca = int((190 - 150 * frac) * max(0.0, min(1.0, 0.55 + 0.45 * (1 + flicker))))
                ca = max(0, min(255, ca))

                fire_draw.ellipse(
                    [fx - blob_r, fy - blob_r, fx + blob_r, fy + blob_r],
                    fill=(cr, cg, cb, ca),
                )

        # 4c. Bloom the flame layer for fiery glow
        fire_bloomed = engine.bloom(fire_layer, radius=10, intensity=1.2, downsample=4)
        scene = engine.additive_composite(scene, fire_bloomed)

        # 4d. Composite flame layer normally too (sharp detail on top of glow)
        scene.alpha_composite(fire_layer)

        # 4e. Hot core -- numpy radial gradient
        norm = self._sun_norm
        pulse_r = ipr
        # Recompute norm for pulsing radius
        p_norm = self._sun_dist / max(pulse_r, 1)
        cmask = p_norm < 1.0

        core_arr = np.zeros((h, w, 4), dtype=np.uint8)

        # White-hot center (255, 200, 120) -> orange (255, 80, 10) -> deep red (180, 15, 0)
        # Smooth continuous gradient: white-hot center -> orange -> deep red
        # Use linear interpolation across the full 0-1 range, no hard stops
        n = p_norm[cmask]
        r_v = np.full_like(p_norm, 0)
        g_v = np.full_like(p_norm, 0)
        b_v = np.full_like(p_norm, 0)
        r_v[cmask] = np.clip(255 - n * 75, 180, 255)
        g_v[cmask] = np.clip(200 - n * 260, 10, 200)
        b_v[cmask] = np.clip(120 - n * 170, 0, 120)

        # Smooth feathered alpha
        c_alpha = np.zeros_like(p_norm)
        c_alpha[cmask] = np.clip(np.maximum(0, (1.0 - n) / 0.3) ** 1.5 * 255, 0, 255)

        core_arr[cmask, 0] = r_v[cmask].astype(np.uint8)
        core_arr[cmask, 1] = g_v[cmask].astype(np.uint8)
        core_arr[cmask, 2] = b_v[cmask].astype(np.uint8)
        core_arr[cmask, 3] = c_alpha[cmask].astype(np.uint8)

        core_layer = Image.fromarray(core_arr, 'RGBA')
        scene.alpha_composite(core_layer)

        # 5. Heat haze bands -- 10 wavy horizontal lines rising upward
        haze_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        haze_draw = ImageDraw.Draw(haze_layer)
        for band in self._haze_bands:
            band_y = (band['base_y'] - t * band['speed']) % (h + 80) - 40
            band_alpha = int(band['alpha_base']
                             + 12 * math.sin(t * 1.3 + band['phase']))
            band_alpha = max(4, min(50, band_alpha))
            pts = []
            for bx in range(0, w, 6):
                wave = math.sin(bx * band['freq'] + t * 2.0 + band['phase']) * band['amp']
                pts.append((bx, int(band_y + wave)))
            if len(pts) >= 2:
                haze_draw.line(pts, fill=(255, 150, 50, band_alpha), width=2)
        scene.alpha_composite(haze_layer)

        # 6. Cracked ground -- pre-rendered at init
        scene.alpha_composite(self._ground_layer)

        # 7. Ember particles -- rising glow sprites with horizontal wobble
        ember_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))

        for i, ember in enumerate(self._embers):
            # Update position
            wobble = math.sin(t * 1.5 + ember['phase']) * 0.4
            ember['x'] += ember['vx'] + wobble
            ember['y'] += ember['vy']
            ember['life'] -= 0.004

            # Respawn at bottom when off-screen or dead
            if ember['y'] < -10 or ember['life'] <= 0:
                new = self._spawn_ember(w, h, initial=False)
                ember.update(new)

            ex, ey = int(ember['x']), int(ember['y'])

            if 0 <= ex < w and 0 <= ey < h:
                sprite = self._ember_sprites[i % len(self._ember_sprites)]
                engine.stamp_glow(ember_layer, ex, ey, sprite)
        scene.alpha_composite(ember_layer)

        # 8. Bottom atmosphere -- warm dark gradient fade
        bottom_h = 55
        bottom_arr = np.zeros((h, w, 4), dtype=np.uint8)
        for y in range(h - bottom_h, h):
            frac = (y - (h - bottom_h)) / bottom_h
            bottom_arr[y, :, 0] = 60
            bottom_arr[y, :, 1] = 15
            bottom_arr[y, :, 2] = 5
            bottom_arr[y, :, 3] = int(frac ** 1.5 * 50)
        scene.alpha_composite(Image.fromarray(bottom_arr, 'RGBA'))

        # 9. Ground-level heat glow (centered above bottom edge)
        engine.stamp_glow(scene, w // 2, h - 30, self._heat_glow)

        # 10. Vignette -- heavier 0.20 strength (pre-computed)
        arr = np.array(scene, dtype=np.float32)
        arr[..., :3] *= self._vignette[..., np.newaxis]
        scene = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGBA')

        return scene


# --------------------------------------------------------------------------
# Standalone test
# --------------------------------------------------------------------------
if __name__ == '__main__':
    import sys
    import time

    scene = HotScene(1920, 440)
    if '--anim' in sys.argv:
        t0 = time.time()
        for i in range(30):
            img = scene.render(i * 0.067, {})
            img.convert('RGB').save(f'/tmp/scene_hot_{i:03d}.png')
        elapsed = time.time() - t0
        print(f"Saved 30 frames to /tmp/scene_hot_*.png  ({elapsed:.1f}s)")
    else:
        img = scene.render(1.0, {})
        img.convert('RGB').save('/tmp/scene_hot.png')
        print("Saved /tmp/scene_hot.png")
