"""Overcast weather scene -- moody, atmospheric, layered sky.

Multiple parallax cloud layers using volumetric sprites, diffuse light
breaking through gaps, distant rain veils, atmospheric ground mist,
and slow shifting ambient light. Designed to feel heavy and immersive.
"""

import math
import random
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scenes.base import BaseScene
from scenes import engine


class OvercastScene(BaseScene):

    def __init__(self, w=1920, h=440):
        super().__init__(w, h)

        # Moody gradient -- slightly brighter at horizon for depth
        self._gradient = engine.gradient_fill(w, h, (22, 26, 40), (40, 45, 58))

        rng = random.Random(42)

        # === 3 PARALLAX CLOUD LAYERS ===
        # Far layer: slow, dim, slightly blurred -- gives depth
        # Mid layer: medium speed, main cloud mass
        # Near layer: faster, darker undersides, dramatic
        self._cloud_layers = []
        layer_configs = [
            # (count, speed_range, y_range, w_range, h_range, color, alpha, blur)
            {
                'count': 5, 'speed': (4, 10), 'y': (-20, 80),
                'cw': (400, 700), 'ch': (120, 200),
                'color': (100, 105, 120), 'alpha': 140, 'blur': 3,
            },
            {
                'count': 6, 'speed': (10, 25), 'y': (20, 160),
                'cw': (350, 600), 'ch': (100, 180),
                'color': (85, 90, 108), 'alpha': 170, 'blur': 1,
            },
            {
                'count': 5, 'speed': (18, 40), 'y': (60, 200),
                'cw': (300, 550), 'ch': (90, 160),
                'color': (65, 70, 88), 'alpha': 190, 'blur': 0,
            },
        ]
        for li, cfg in enumerate(layer_configs):
            clouds = []
            for ci in range(cfg['count']):
                clouds.append({
                    'x': rng.uniform(-400, w + 400),
                    'y': rng.uniform(*cfg['y']),
                    'cw': rng.uniform(*cfg['cw']),
                    'ch': rng.uniform(*cfg['ch']),
                    'speed': rng.uniform(*cfg['speed']),
                    'seed': rng.randint(0, 99999),
                    'phase': rng.uniform(0, math.tau),
                })
            self._cloud_layers.append((cfg, clouds))

        # Consume rng state for deterministic seeding (light patches rebuilt below as glow sprites)
        for _ in range(4):
            rng.uniform(0, w); rng.uniform(h * 0.1, h * 0.5)
            rng.uniform(100, 250); rng.uniform(40, 80)
            rng.uniform(5, 15); rng.uniform(0, math.tau)
            rng.uniform(0.5, 1.0)

        # === DISTANT RAIN VEILS === (translucent diagonal streaks)
        self._rain_veils = []
        for _ in range(3):
            self._rain_veils.append({
                'x': rng.uniform(0, w),
                'width': rng.uniform(150, 350),
                'speed': rng.uniform(8, 20),
                'alpha': rng.randint(8, 18),
                'phase': rng.uniform(0, math.tau),
            })

        # === GROUND MIST === (low atmospheric fog)
        self._mist_puffs = []
        for _ in range(12):
            self._mist_puffs.append({
                'x': rng.uniform(-200, w + 200),
                'y': rng.uniform(h * 0.75, h * 0.95),
                'rx': rng.uniform(120, 350),
                'ry': rng.uniform(15, 40),
                'speed': rng.uniform(6, 22),
                'alpha': rng.randint(20, 45),
                'phase': rng.uniform(0, math.tau),
            })

        # Pre-render cloud sprites + pre-blur at init (avoids per-frame blur)
        for cfg, clouds in self._cloud_layers:
            for cloud in clouds:
                sprite = engine.render_cloud_sprite(
                    int(cloud['cw']), int(cloud['ch']),
                    cfg['color'], cfg['alpha'], cloud['seed'])
                if cfg['blur'] > 0:
                    cloud['_sprite'] = sprite.filter(
                        ImageFilter.GaussianBlur(cfg['blur']))
                else:
                    cloud['_sprite'] = sprite

        # === LIGHT PATCHES — pre-rendered glow sprites (no per-frame blur) ===
        self._light_patches = []
        for _ in range(4):
            rx = int(rng.uniform(120, 260))
            self._light_patches.append({
                'x': rng.uniform(0, w),
                'y': rng.uniform(h * 0.1, h * 0.4),
                'rx': rx,
                'speed': rng.uniform(3, 8),
                'phase': rng.uniform(0, math.tau),
                'sprite': engine.glow_sprite(rx, (100, 105, 120), alpha_peak=15),
            })

        # === GROUND MIST — pre-rendered glow sprites (no per-frame blur) ===
        for puff in self._mist_puffs:
            rx = int(puff['rx'])
            puff['_sprite'] = engine.glow_sprite(rx, (75, 80, 95), alpha_peak=puff['alpha'])

        # === SPARSE RAIN DROPS — slow, translucent streaks ===
        self._raindrops = []
        for _ in range(6):
            self._raindrops.append({
                'x': rng.uniform(0, w),
                'y': rng.uniform(-h, h),
                'speed': rng.uniform(3, 6),
                'length': rng.uniform(20, 50),
                'alpha': rng.randint(30, 60),
            })

        # === PRE-COMPUTE VIGNETTE as RGBA overlay ===
        x_coords = np.linspace(-1, 1, w, dtype=np.float32)
        y_coords = np.linspace(-1, 1, h, dtype=np.float32)
        X, Y = np.meshgrid(x_coords, y_coords)
        vignette = np.clip(
            1.0 - 0.18 * (X ** 2 * 0.3 + Y ** 2 * 0.7), 0.6, 1.0)
        vig_alpha = ((1.0 - vignette) * 255).astype(np.uint8)
        vig_rgba = np.zeros((h, w, 4), dtype=np.uint8)
        vig_rgba[..., 3] = vig_alpha
        self._vignette_img = Image.fromarray(vig_rgba, 'RGBA')

    def render(self, t, weather_data):
        w, h = self.w, self.h
        scene = self._gradient.copy()

        # === AMBIENT LIGHT SHIFT ===
        # Slowly pulsing overall brightness to simulate shifting light
        ambient_shift = 0.03 * math.sin(t * 0.15)
        if abs(ambient_shift) > 0.005:
            arr = np.array(scene, dtype=np.float32)
            arr[..., :3] = np.clip(arr[..., :3] * (1.0 + ambient_shift), 0, 255)
            scene = Image.fromarray(arr.astype(np.uint8), 'RGBA')

        # === LIGHT PATCHES === stamp pre-rendered glow sprites (no per-frame blur)
        for lp in self._light_patches:
            lx = (lp['x'] + lp['speed'] * t) % (w + lp['rx'] * 2) - lp['rx']
            ly = lp['y'] + math.sin(t * 0.2 + lp['phase']) * 8
            engine.stamp_glow(scene, int(lx), int(ly), lp['sprite'])

        # === CLOUD LAYERS (back to front) -- use pre-blurred sprites directly ===
        for cfg, clouds in self._cloud_layers:
            for cloud in clouds:
                cx = ((cloud['x'] + cloud['speed'] * t)
                      % (w + cloud['cw'] + 200) - cloud['cw'] - 100)
                cy = cloud['y'] + math.sin(t * 0.12 + cloud['phase']) * 8
                sprite = cloud['_sprite']
                px, py = int(cx), int(cy)
                if (px + sprite.size[0] > 0 and px < w
                        and py + sprite.size[1] > 0 and py < h):
                    scene.alpha_composite(sprite, dest=(px, py))

        # === DISTANT RAIN VEILS === (translucent diagonal streaks)
        rain_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        rain_draw = ImageDraw.Draw(rain_layer)
        for veil in self._rain_veils:
            vx = (veil['x'] + veil['speed'] * t) % (w + veil['width'] * 2) - veil['width']
            vw = int(veil['width'])
            # Pulsing visibility
            pulse = 0.6 + 0.4 * math.sin(t * 0.25 + veil['phase'])
            va = int(veil['alpha'] * pulse)
            # Draw as many thin diagonal lines within the veil width
            for sx in range(int(vx), int(vx) + vw, 12):
                if 0 <= sx < w:
                    # Diagonal streak from cloud base to ground
                    streak_top = int(h * 0.35 + math.sin(sx * 0.01 + t * 0.3) * 20)
                    wind_offset = int((h - streak_top) * 0.15)
                    rain_draw.line(
                        [(sx, streak_top), (sx - wind_offset, h)],
                        fill=(80, 90, 120, va), width=1)

        rain_layer = engine.bloom(rain_layer, radius=4, intensity=1.0, downsample=4)
        scene.alpha_composite(rain_layer)

        # === SPARSE RAIN DROPS === (slow, translucent streaks)
        drop_draw = ImageDraw.Draw(scene)
        for drop in self._raindrops:
            drop['y'] += drop['speed']
            if drop['y'] > h:
                drop['y'] = random.uniform(-50, -10)
                drop['x'] = random.uniform(0, w)
            dx = int(drop['x'])
            dy = int(drop['y'])
            drop_draw.line(
                [(dx, dy), (dx - 1, dy + int(drop['length']))],
                fill=(80, 90, 120, drop['alpha']), width=1)

        # === GROUND MIST === stamp pre-rendered glow sprites (no per-frame blur)
        for puff in self._mist_puffs:
            mx = (puff['x'] + puff['speed'] * t) % (w + puff['rx'] * 2) - puff['rx']
            my = puff['y'] + math.sin(t * 0.25 + puff['phase']) * 6
            engine.stamp_glow(scene, int(mx), int(my), puff['_sprite'])

        # === BOTTOM ATMOSPHERE === (gradient fade at very bottom)
        bottom_h = 50
        bottom_arr = np.zeros((h, w, 4), dtype=np.uint8)
        for y in range(h - bottom_h, h):
            frac = (y - (h - bottom_h)) / bottom_h
            bottom_arr[y, :, 0] = 45
            bottom_arr[y, :, 1] = 50
            bottom_arr[y, :, 2] = 60
            bottom_arr[y, :, 3] = int(frac ** 1.5 * 40)
        scene.alpha_composite(Image.fromarray(bottom_arr, 'RGBA'))

        # === SUBTLE VIGNETTE === pre-rendered RGBA overlay
        scene.alpha_composite(self._vignette_img)

        return scene


if __name__ == '__main__':
    import sys
    scene = OvercastScene(1920, 440)
    if '--anim' in sys.argv:
        for i in range(30):
            img = scene.render(i * 0.067, {})
            img.convert('RGB').save(f'/tmp/scene_overcast_{i:03d}.png')
        print("Saved 30 frames")
    else:
        img = scene.render(1.0, {})
        img.convert('RGB').save('/tmp/scene_overcast.png')
        print("Saved /tmp/scene_overcast.png")
