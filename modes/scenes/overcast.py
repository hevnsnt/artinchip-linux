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

        # === LIGHT PATCHES === (where sun tries to peek through)
        self._light_patches = []
        for _ in range(4):
            self._light_patches.append({
                'x': rng.uniform(0, w),
                'y': rng.uniform(h * 0.1, h * 0.5),
                'rx': rng.uniform(100, 250),
                'ry': rng.uniform(40, 80),
                'speed': rng.uniform(5, 15),
                'phase': rng.uniform(0, math.tau),
                'intensity': rng.uniform(0.5, 1.0),
            })

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

        # Pre-render cloud sprites (cached in engine, only built once)
        for cfg, clouds in self._cloud_layers:
            for cloud in clouds:
                engine.render_cloud_sprite(
                    int(cloud['cw']), int(cloud['ch']),
                    cfg['color'], cfg['alpha'], cloud['seed'])

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

        # === LIGHT PATCHES === (diffuse light breaking through cloud gaps)
        light_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        light_draw = ImageDraw.Draw(light_layer)
        for lp in self._light_patches:
            lx = (lp['x'] + lp['speed'] * t) % (w + lp['rx'] * 2) - lp['rx']
            ly = lp['y'] + math.sin(t * 0.2 + lp['phase']) * 15
            # Pulsing intensity
            pulse = 0.5 + 0.5 * math.sin(t * 0.3 + lp['phase'] * 2)
            alpha = int(18 * lp['intensity'] * pulse)
            rx = int(lp['rx'] + math.sin(t * 0.15 + lp['phase']) * 20)
            ry = int(lp['ry'] + math.sin(t * 0.2 + lp['phase']) * 8)
            # Warm-tinted light
            light_draw.ellipse(
                [int(lx) - rx, int(ly) - ry, int(lx) + rx, int(ly) + ry],
                fill=(140, 135, 115, alpha))
        light_layer = light_layer.filter(ImageFilter.GaussianBlur(8))
        scene = engine.additive_composite(scene, light_layer)

        # === CLOUD LAYERS (back to front) ===
        for li, (cfg, clouds) in enumerate(self._cloud_layers):
            cloud_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))

            for cloud in clouds:
                cx = (cloud['x'] + cloud['speed'] * t) % (w + cloud['cw'] + 200) - cloud['cw'] - 100
                cy = cloud['y'] + math.sin(t * 0.12 + cloud['phase']) * 8

                sprite = engine.render_cloud_sprite(
                    int(cloud['cw']), int(cloud['ch']),
                    cfg['color'], cfg['alpha'], cloud['seed'])

                px, py = int(cx), int(cy)
                if px + sprite.size[0] > 0 and px < w and py + sprite.size[1] > 0 and py < h:
                    cloud_layer.alpha_composite(sprite, dest=(px, py))

            # Far layer gets blur for atmospheric depth
            if cfg['blur'] > 0:
                cloud_layer = cloud_layer.filter(
                    ImageFilter.GaussianBlur(cfg['blur']))

            scene.alpha_composite(cloud_layer)

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

        rain_layer = rain_layer.filter(ImageFilter.GaussianBlur(2))
        scene.alpha_composite(rain_layer)

        # === GROUND MIST === (atmospheric low fog)
        mist_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        mist_draw = ImageDraw.Draw(mist_layer)
        for puff in self._mist_puffs:
            mx = (puff['x'] + puff['speed'] * t) % (w + puff['rx'] * 2) - puff['rx']
            my = puff['y'] + math.sin(t * 0.25 + puff['phase']) * 6
            # Breathing alpha
            breath = 0.7 + 0.3 * math.sin(t * 0.2 + puff['phase'] * 1.5)
            ma = int(puff['alpha'] * breath)
            engine.draw_soft_ellipse(
                mist_draw, int(mx), int(my),
                int(puff['rx']), int(puff['ry']),
                (75, 80, 95), ma)
        mist_layer = mist_layer.filter(ImageFilter.GaussianBlur(4))
        scene.alpha_composite(mist_layer)

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

        # === SUBTLE VIGNETTE === (darken edges for mood)
        arr = np.array(scene, dtype=np.float32)
        x_coords = np.linspace(-1, 1, w, dtype=np.float32)
        y_coords = np.linspace(-1, 1, h, dtype=np.float32)
        X, Y = np.meshgrid(x_coords, y_coords)
        vig = 1.0 - 0.18 * (X ** 2 * 0.3 + Y ** 2 * 0.7)
        vig = np.clip(vig, 0.6, 1.0)
        arr[..., :3] *= vig[..., np.newaxis]
        scene = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGBA')

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
