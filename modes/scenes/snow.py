"""Snow weather scene -- magical winter snowfall for a 1920x440 bar LCD.

Deep parallax cloud layers, 200 glow-sprite snowflakes with sine-wave drift,
random sparkle flashes, growing wavy accumulation band, cold ground mist,
cool desaturation, and a gentle vignette. Designed to feel hushed and magical.
"""

import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scenes.base import BaseScene
from scenes import engine


class SnowScene(BaseScene):

    def __init__(self, w: int, h: int):
        super().__init__(w, h)

        rng = random.Random(77)

        # --- bright wintry sky -- soft white-blue, cozy and luminous ---
        self._bg = engine.gradient_fill(w, h, (60, 70, 110), (120, 130, 155))

        # --- 2 PARALLAX CLOUD LAYERS (volumetric sprites) ---
        self._cloud_layers = []
        layer_cfgs = [
            # Far: soft, bright, blurred for depth
            {'count': 5, 'speed': (5, 12), 'y': (-10, 90),
             'cw': (420, 700), 'ch': (110, 190),
             'color': (160, 165, 180), 'alpha': 120, 'blur': 2},
            # Near: slightly denser, still bright
            {'count': 5, 'speed': (14, 30), 'y': (30, 170),
             'cw': (350, 600), 'ch': (90, 160),
             'color': (140, 145, 165), 'alpha': 140, 'blur': 0},
        ]
        for cfg in layer_cfgs:
            clouds = []
            for _ in range(cfg['count']):
                c = {
                    'x': rng.uniform(-400, w + 400),
                    'y': rng.uniform(*cfg['y']),
                    'cw': rng.uniform(*cfg['cw']),
                    'ch': rng.uniform(*cfg['ch']),
                    'speed': rng.uniform(*cfg['speed']),
                    'seed': rng.randint(0, 99999),
                    'phase': rng.uniform(0, math.tau),
                }
                clouds.append(c)
                # Pre-render sprite into cache
                engine.render_cloud_sprite(
                    int(c['cw']), int(c['ch']),
                    cfg['color'], cfg['alpha'], c['seed'])
            self._cloud_layers.append((cfg, clouds))

        # --- SNOWFLAKE PARTICLE POOL (200) ---
        self._pool = engine.ParticlePool(200)
        p = self._pool
        for i in range(p.max):
            p.x[i] = rng.uniform(0, w)
            p.y[i] = rng.uniform(-h, h)
            p.vx[i] = rng.uniform(-0.8, 0.8)
            p.vy[i] = rng.uniform(0.5, 2.5)
            p.depth[i] = rng.uniform(0, 1)
            p.size[i] = rng.uniform(1.0, 5.5)
            p.phase[i] = rng.uniform(0, math.tau)

        # --- 4 PRE-RENDERED GLOW SPRITES (cool-white) ---
        snow_color = (220, 230, 255)
        self._sprites = [
            engine.glow_sprite(radius=2, color=snow_color, alpha_peak=140),   # tiny
            engine.glow_sprite(radius=4, color=snow_color, alpha_peak=165),   # small
            engine.glow_sprite(radius=6, color=snow_color, alpha_peak=185),   # medium
            engine.glow_sprite(radius=9, color=snow_color, alpha_peak=210),   # large
        ]
        # Sparkle flash sprite (bright white)
        self._sparkle_sprite = engine.glow_sprite(
            radius=5, color=(255, 255, 255), alpha_peak=250)

        # --- GROUND MIST (8 cold-toned puffs, breathing) ---
        self._mist_puffs = []
        for _ in range(8):
            self._mist_puffs.append({
                'x': rng.uniform(-200, w + 200),
                'y': rng.uniform(h * 0.78, h * 0.96),
                'rx': rng.uniform(140, 380),
                'ry': rng.uniform(18, 45),
                'speed': rng.uniform(5, 18),
                'alpha': rng.randint(18, 40),
                'phase': rng.uniform(0, math.tau),
            })

        # --- ACCUMULATION state ---
        self._accumulation_height = 0.0

        # --- SPARKLE state: dict[particle_index] -> frames_remaining ---
        self._sparkle: dict[int, int] = {}

        # --- PRE-COMPUTE VIGNETTE (0.12 strength) ---
        x_c = np.linspace(-1, 1, w, dtype=np.float32)
        y_c = np.linspace(-1, 1, h, dtype=np.float32)
        X, Y = np.meshgrid(x_c, y_c)
        self._vignette = np.clip(
            1.0 - 0.12 * (X ** 2 * 0.3 + Y ** 2 * 0.7), 0.6, 1.0)

    # ------------------------------------------------------------------

    def render(self, t: float, weather_data: dict) -> Image.Image:
        w, h = self.w, self.h
        p = self._pool

        # 1. Copy gradient
        scene = self._bg.copy()

        # 2. Ambient light shift -- very gentle, cool-toned pulsing
        amb = 0.025 * math.sin(t * 0.12) + 0.012 * math.sin(t * 0.07)
        if abs(amb) > 0.003:
            arr = np.array(scene, dtype=np.float32)
            # Slightly stronger shift on blue channel for cold feel
            arr[..., 0] = np.clip(arr[..., 0] * (1.0 + amb * 0.8), 0, 255)
            arr[..., 1] = np.clip(arr[..., 1] * (1.0 + amb * 0.9), 0, 255)
            arr[..., 2] = np.clip(arr[..., 2] * (1.0 + amb), 0, 255)
            scene = Image.fromarray(arr.astype(np.uint8), 'RGBA')

        # 3. Cloud layers -- volumetric sprites, far blurred for depth
        for cfg, clouds in self._cloud_layers:
            layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
            for cloud in clouds:
                cx = ((cloud['x'] + cloud['speed'] * t)
                      % (w + cloud['cw'] + 200) - cloud['cw'] - 100)
                cy = cloud['y'] + math.sin(t * 0.10 + cloud['phase']) * 7
                sprite = engine.render_cloud_sprite(
                    int(cloud['cw']), int(cloud['ch']),
                    cfg['color'], cfg['alpha'], cloud['seed'])
                px, py = int(cx), int(cy)
                if (px + sprite.size[0] > 0 and px < w
                        and py + sprite.size[1] > 0 and py < h):
                    layer.alpha_composite(sprite, dest=(px, py))
            if cfg['blur'] > 0:
                layer = layer.filter(ImageFilter.GaussianBlur(cfg['blur']))
            scene.alpha_composite(layer)

        # 4. Snowflakes -- update positions with sine-wave drift
        drift = np.sin(t * 0.6 + p.phase) * 0.8 + p.vx * 0.3
        p.x += drift
        p.y += p.vy

        # Recycle flakes that fall below frame
        below = p.y > h + 10
        if np.any(below):
            idx = np.where(below)[0]
            for i in idx:
                p.x[i] = random.uniform(0, w)
                p.y[i] = random.uniform(-25, -5)
                p.vx[i] = random.uniform(-0.8, 0.8)
                p.vy[i] = random.uniform(0.5, 2.5)
                p.depth[i] = random.uniform(0, 1)
                p.size[i] = random.uniform(1.0, 5.5)
                p.phase[i] = random.uniform(0, math.tau)
        # Wrap horizontal drift
        p.x[p.x < -15] += w + 30
        p.x[p.x > w + 15] -= w + 30

        # Draw back-to-front by depth
        snow_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        sorted_idx = p.get_sorted_indices()
        for i in sorted_idx:
            fx, fy = int(p.x[i]), int(p.y[i])
            if fy < -12 or fy > h + 12:
                continue
            sz = p.size[i]
            depth = p.depth[i]
            # Select from 4 sprites by size
            if sz < 2.0:
                sprite = self._sprites[0]
            elif sz < 3.2:
                sprite = self._sprites[1]
            elif sz < 4.4:
                sprite = self._sprites[2]
            else:
                sprite = self._sprites[3]
            # Depth-dependent alpha: farther = dimmer
            alpha_scale = 0.3 + 0.7 * depth
            if alpha_scale < 0.99:
                a = np.array(sprite, dtype=np.float32)
                a[..., 3] *= alpha_scale
                sprite_adj = Image.fromarray(
                    np.clip(a, 0, 255).astype(np.uint8), 'RGBA')
            else:
                sprite_adj = sprite
            try:
                engine.stamp_glow(snow_layer, fx, fy, sprite_adj)
            except (ValueError, Exception):
                pass

        # 5. Sparkle effect -- update timers, trigger new sparkles
        expired = [k for k, v in self._sparkle.items() if v <= 0]
        for k in expired:
            del self._sparkle[k]
        for k in self._sparkle:
            self._sparkle[k] -= 1
        # 2% chance per frame: random flake gets bright flash (3 frames)
        if random.random() < 0.02 * (p.max / 30):
            si = random.randint(0, p.max - 1)
            if si not in self._sparkle:
                self._sparkle[si] = 3
        # Draw sparkle overlays on sparkling flakes
        for si in self._sparkle:
            fx, fy = int(p.x[si]), int(p.y[si])
            if 0 <= fx < w and 0 <= fy < h:
                try:
                    engine.stamp_glow(snow_layer, fx, fy, self._sparkle_sprite)
                except (ValueError, Exception):
                    pass

        scene.alpha_composite(snow_layer)

        # 6. Snow accumulation -- growing wavy band at bottom
        self._accumulation_height = min(
            self._accumulation_height + 0.003, 20.0)
        ah = self._accumulation_height
        if ah > 0.5:
            acc_arr = np.zeros((h, w, 4), dtype=np.uint8)
            xs = np.arange(w, dtype=np.float32)
            wave = np.sin(xs * 0.015 + t * 0.08) * 4.0
            top_y = np.clip((h - ah + wave).astype(int), 0, h - 1)
            for x_col in range(w):
                ty = top_y[x_col]
                band_h = h - ty
                if band_h <= 0:
                    continue
                rows = np.arange(ty, h)
                frac = (rows - ty).astype(np.float32) / max(band_h, 1)
                alpha = (frac * 120).astype(np.uint8)
                acc_arr[ty:h, x_col, 0] = 180
                acc_arr[ty:h, x_col, 1] = 190
                acc_arr[ty:h, x_col, 2] = 215
                acc_arr[ty:h, x_col, 3] = alpha
            scene.alpha_composite(Image.fromarray(acc_arr, 'RGBA'))

        # 7. Ground mist -- cold-toned puffs, breathing, blurred
        mist_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        mist_draw = ImageDraw.Draw(mist_layer)
        for puff in self._mist_puffs:
            mx = ((puff['x'] + puff['speed'] * t)
                  % (w + puff['rx'] * 2) - puff['rx'])
            my = puff['y'] + math.sin(t * 0.22 + puff['phase']) * 5
            breath = 0.65 + 0.35 * math.sin(t * 0.18 + puff['phase'] * 1.4)
            ma = int(puff['alpha'] * breath)
            engine.draw_soft_ellipse(
                mist_draw, int(mx), int(my),
                int(puff['rx']), int(puff['ry']),
                (140, 148, 165), ma)
        mist_layer = mist_layer.filter(ImageFilter.GaussianBlur(3))
        scene.alpha_composite(mist_layer)

        # 8. Bottom atmosphere -- soft white-blue gradient (cool tone)
        bottom_h = 55
        bottom_arr = np.zeros((h, w, 4), dtype=np.uint8)
        for y in range(h - bottom_h, h):
            frac = (y - (h - bottom_h)) / bottom_h
            bottom_arr[y, :, 0] = 150
            bottom_arr[y, :, 1] = 158
            bottom_arr[y, :, 2] = 175
            bottom_arr[y, :, 3] = int(frac ** 1.5 * 45)
        scene.alpha_composite(Image.fromarray(bottom_arr, 'RGBA'))

        # 9. Cool desaturation -- blend RGB toward gray (8%)
        arr = np.array(scene, dtype=np.float32)
        gray = np.mean(arr[..., :3], axis=2, keepdims=True)
        arr[..., :3] = arr[..., :3] * 0.92 + gray * 0.08

        # 10. Vignette -- 0.12 strength (pre-computed)
        arr[..., :3] *= self._vignette[..., np.newaxis]

        scene = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGBA')
        return scene


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    import os
    import sys

    W, H = 1920, 440
    scene = SnowScene(W, H)

    if '--anim' in sys.argv:
        os.makedirs('/tmp/scene_snow_frames', exist_ok=True)
        for i in range(30):
            t = i * (1.0 / 15.0)
            img = scene.render(t, {})
            out = img.convert('RGB')
            path = f'/tmp/scene_snow_frames/frame_{i:03d}.png'
            out.save(path)
            print(f'Saved {path}')
        print('Done. 30 frames saved to /tmp/scene_snow_frames/')
    else:
        img = scene.render(5.0, {})
        out = img.convert('RGB')
        out.save('/tmp/scene_snow.png')
        print('Saved /tmp/scene_snow.png')
