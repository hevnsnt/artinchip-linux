import math
import random
import numpy as np
from PIL import Image, ImageDraw
from scenes.base import BaseScene
from scenes import engine


class FogScene(BaseScene):
    """Dense fog with noise-driven density, 3-layer parallax fog bands, and faint silhouettes."""

    def __init__(self, w=1920, h=440):
        super().__init__(w, h)
        self.grad_top = (30, 33, 42)
        self.grad_bot = (50, 55, 65)

        # 3 layers of fog band configs: far (0), mid (1), near (2)
        rng = random.Random(77)
        self.fog_layers = []
        layer_configs = [
            # (count, speed_range, opacity_range, ry_range, y_center_frac)
            (9,  (3, 8),    (25, 45),  (20, 45),  0.50),   # far  -- slow, dim
            (9,  (10, 22),  (35, 65),  (25, 55),  0.62),   # mid
            (8,  (18, 40),  (50, 85),  (30, 65),  0.75),   # near -- fast, bright
        ]
        for li, (count, spd, opa, ry_r, y_ctr) in enumerate(layer_configs):
            puffs = []
            for _ in range(count):
                puffs.append({
                    'x': rng.uniform(0, w),
                    'y': rng.uniform(h * (y_ctr - 0.15), h * (y_ctr + 0.15)),
                    'rx': rng.uniform(120, 300),
                    'ry': rng.uniform(ry_r[0], ry_r[1]),
                    'speed': rng.uniform(spd[0], spd[1]),
                    'opacity': rng.randint(opa[0], opa[1]),
                    'phase': rng.uniform(0, math.tau),
                    'depth': li,
                })
            self.fog_layers.append(puffs)

        # Pre-render silhouette layer with bloom at init (not per-frame)
        self._sil_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        sil_draw = ImageDraw.Draw(self._sil_layer)
        shapes = [
            (100, h - 120, 180, h, 40),   # tall building
            (250, h - 80, 380, h, 35),    # shorter building
            (500, h - 100, 600, h, 38),   # medium building
            (700, h - 60, 820, h, 30),    # low building
            (950, h - 140, 1050, h, 42),  # tall tower
            (1200, h - 70, 1350, h, 32),  # wide low building
            (1500, h - 90, 1600, h, 36),  # medium
            (1700, h - 110, 1820, h, 40), # tall
        ]
        for x1, y1, x2, y2, alpha in shapes:
            sil_draw.rectangle([x1, y1, x2, y2], fill=(20, 22, 30, alpha))
        self._sil_layer = engine.bloom(self._sil_layer, radius=6, intensity=1.0, downsample=4)

        # Visibility windows (areas of slightly less fog)
        self._windows = []
        for _ in range(3):
            rx = int(rng.uniform(60, 100))
            self._windows.append({
                'x': rng.uniform(0, w),
                'y': rng.uniform(h * 0.4, h * 0.7),
                'rx': rx,
                'speed': rng.uniform(1, 4),
                'phase': rng.uniform(0, math.tau),
                'sprite': engine.glow_sprite(rx, (120, 125, 140), alpha_peak=15),
            })

        # Cache gradient as instance variable
        self._gradient = engine.gradient_fill(w, h, self.grad_top, self.grad_bot)

    def render(self, t, weather_data):
        w, h = self.w, self.h

        # 1. Gradient base (cached at init)
        base = self._gradient.copy()

        # 2. Noise fog density with vertical gradient
        nf = engine.noise_field(w, h, t * 0.2, scale=0.003, octaves=2)

        # Vertical density gradient: denser at bottom -- multiply noise by (y/h)^1.5
        y_indices = np.arange(h).reshape(h, 1).astype(np.float64)
        vert_grad = np.power(y_indices / h, 1.5)
        density = nf * vert_grad

        fog_density_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        arr = np.array(fog_density_layer)

        alpha_map = (density * 160).clip(0, 120).astype(np.uint8)
        fog_mask = alpha_map > 0
        if fog_mask.any():
            arr[fog_mask, 0] = 85
            arr[fog_mask, 1] = 90
            arr[fog_mask, 2] = 105
            arr[fog_mask, 3] = alpha_map[fog_mask]

        fog_density_layer = Image.fromarray(arr, 'RGBA')
        fog_density_layer = engine.bloom(fog_density_layer, radius=4, intensity=1.0, downsample=4)
        base = Image.alpha_composite(base, fog_density_layer)

        # 3. 3-layer parallax fog bands
        blur_by_layer = [2, 0, 0]
        color_by_layer = [
            (75, 80, 95),    # far
            (90, 95, 110),   # mid
            (110, 115, 130), # near -- brighter
        ]

        for li, puffs in enumerate(self.fog_layers):
            band_layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
            band_draw = ImageDraw.Draw(band_layer)
            cc = color_by_layer[li]

            for puff in puffs:
                px = (puff['x'] + puff['speed'] * t) % (w + puff['rx'] * 2) - puff['rx']
                py = puff['y'] + math.sin(t * 0.3 + puff['phase']) * 6
                engine.draw_soft_ellipse(
                    band_draw, int(px), int(py),
                    int(puff['rx']), int(puff['ry']),
                    cc, puff['opacity']
                )

            if blur_by_layer[li] > 0:
                band_layer = engine.bloom(band_layer, radius=blur_by_layer[li] * 2, intensity=1.0, downsample=4)
            base = Image.alpha_composite(base, band_layer)

        # 4. Visibility windows -- drifting areas of slightly less fog
        for win in self._windows:
            wx = (win['x'] + win['speed'] * t) % (w + win['rx'] * 2) - win['rx']
            wy = win['y'] + math.sin(t * 0.1 + win['phase']) * 5
            engine.stamp_glow(base, int(wx), int(wy), win['sprite'])

        # 5. Silhouettes -- pre-rendered at init with bloom
        base = Image.alpha_composite(base, self._sil_layer)

        # 6. Color grade
        base = engine.color_grade(base, 'fog')
        return base


if __name__ == '__main__':
    import sys
    scene = FogScene(1920, 440)
    if '--anim' in sys.argv:
        for i in range(30):
            img = scene.render(i * 0.067, {})
            img.convert('RGB').save(f'/tmp/scene_fog_{i:03d}.png')
        print("Saved 30 frames")
    else:
        img = scene.render(1.0, {})
        img.convert('RGB').save('/tmp/scene_fog.png')
        print("Saved /tmp/scene_fog.png")
