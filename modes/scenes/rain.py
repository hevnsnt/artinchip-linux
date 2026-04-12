"""Rain weather scene -- dark, immersive, layered storm atmosphere.

Heavy rain with parallax storm clouds, splash particles, puddle ripples,
wet ground reflections, ground mist, diffuse light patches, and a moody
vignette. Matches the atmospheric depth of the overcast scene.
"""

import math
import random
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scenes.base import BaseScene
from scenes import engine


class RainScene(BaseScene):

    def __init__(self, w=1920, h=440):
        super().__init__(w, h)

        # Dark blue-gray gradient -- storm sky
        self._gradient = engine.gradient_fill(w, h, (15, 18, 35), (28, 32, 48))

        rng = random.Random(77)

        # === 3 PARALLAX CLOUD LAYERS (dark storm clouds) ===
        self._cloud_layers = []
        layer_configs = [
            {   # Far -- slow, dim, heavily blurred
                'count': 5, 'speed': (4, 10), 'y': (-30, 70),
                'cw': (420, 720), 'ch': (130, 210),
                'color': (80, 85, 100), 'alpha': 130, 'blur': 3,
            },
            {   # Mid -- moderate speed, main cloud mass
                'count': 6, 'speed': (10, 24), 'y': (10, 150),
                'cw': (350, 620), 'ch': (100, 180),
                'color': (65, 70, 88), 'alpha': 160, 'blur': 1,
            },
            {   # Near -- faster, darkest undersides, sharp
                'count': 5, 'speed': (18, 38), 'y': (50, 190),
                'cw': (300, 560), 'ch': (90, 160),
                'color': (50, 55, 72), 'alpha': 185, 'blur': 0,
            },
        ]
        for cfg in layer_configs:
            clouds = []
            for _ in range(cfg['count']):
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

        # Pre-render cloud sprites + pre-blur far/mid layers at init
        for cfg, clouds in self._cloud_layers:
            for cloud in clouds:
                sprite = engine.render_cloud_sprite(
                    int(cloud['cw']), int(cloud['ch']),
                    cfg['color'], cfg['alpha'], cloud['seed'])
                # Pre-blur sprites for far/mid layers (avoids per-frame blur)
                if cfg['blur'] > 0:
                    cloud['_sprite'] = sprite.filter(
                        ImageFilter.GaussianBlur(cfg['blur']))
                else:
                    cloud['_sprite'] = sprite

        # === RAIN PARTICLE POOL (100 drops — fewer but beautiful) ===
        self._pool = engine.ParticlePool(100)
        pool = self._pool
        for i in range(100):
            pool.active[i] = True
            pool.x[i] = rng.uniform(0, w + 60)
            pool.y[i] = rng.uniform(-h, h)
            pool.vx[i] = rng.uniform(-3.0, -1.5)
            pool.vy[i] = rng.uniform(9, 20)
            pool.depth[i] = rng.uniform(0, 1)
            pool.size[i] = rng.uniform(0.2, 1.0) + 0.5 * pool.depth[i]
            pool.phase[i] = rng.uniform(0, math.tau)
            pool.life[i] = rng.uniform(0.5, 2.0)  # used as length multiplier

        # === SPLASH + RIPPLE LISTS ===
        self._splashes: list[dict] = []
        self._ripples: list[dict] = []

        # === LIGHT PATCHES — pre-rendered glow sprites (no per-frame blur) ===
        self._light_patches = []
        for _ in range(3):
            rx = int(rng.uniform(120, 280))
            self._light_patches.append({
                'x': rng.uniform(0, w),
                'y': rng.uniform(h * 0.08, h * 0.45),
                'rx': rx,
                'ry': int(rng.uniform(35, 70)),
                'speed': rng.uniform(4, 12),
                'phase': rng.uniform(0, math.tau),
                'sprite': engine.glow_sprite(rx, (110, 120, 150), alpha_peak=14),
            })

        # === GROUND MIST — pre-rendered glow sprites (no per-frame blur) ===
        self._mist_puffs = []
        for _ in range(8):
            rx = int(rng.uniform(100, 320))
            alpha = rng.randint(18, 40)
            self._mist_puffs.append({
                'x': rng.uniform(-200, w + 200),
                'y': rng.uniform(h * 0.78, h * 0.96),
                'rx': rx,
                'speed': rng.uniform(5, 18),
                'alpha': alpha,
                'phase': rng.uniform(0, math.tau),
                'sprite': engine.glow_sprite(rx, (55, 62, 82), alpha_peak=alpha),
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _respawn_drop(self, i):
        pool = self._pool
        pool.x[i] = random.uniform(0, self.w + 60)
        pool.y[i] = random.uniform(-self.h, -10)
        pool.vx[i] = random.uniform(-3.0, -1.5)
        pool.vy[i] = random.uniform(9, 20)
        pool.depth[i] = random.uniform(0, 1)
        pool.size[i] = random.uniform(0.2, 1.0) + 0.5 * pool.depth[i]
        pool.life[i] = random.uniform(0.5, 2.0)

    def _maybe_splash(self, x, depth):
        # Every drop splashes — scale intensity by depth (near drops splash bigger)
        count = 2 if depth < 0.4 else random.randint(3, 5)
        for _ in range(count):
            self._splashes.append({
                'x': x + random.uniform(-4, 4),
                'y': float(self.h - random.randint(2, 6)),
                'vx': random.uniform(-3.0, 3.0),
                'vy': random.uniform(-6.0, -2.0) * (0.5 + depth * 0.5),
                'gravity': 0.5,
                'life': random.uniform(4, 10),
                'max_life': 10,
                'size': random.uniform(0.8, 2.0) * (0.5 + depth * 0.5),
            })
        # Ripple at impact point
        self._ripples.append({
            'x': x,
            'y': float(self.h - random.randint(2, 8)),
            'radius': 1.0,
            'max_radius': random.uniform(6, 18) * (0.5 + depth * 0.5),
            'expand_speed': random.uniform(0.8, 1.8),
            'life': 1.0,
        })

    # ------------------------------------------------------------------
    # Main render
    # ------------------------------------------------------------------

    def render(self, t, weather_data):
        w, h = self.w, self.h
        pool = self._pool

        # 1. Base gradient
        scene = self._gradient.copy()

        # 2. Light patches — stamp pre-rendered glow sprites (no per-frame blur)
        for lp in self._light_patches:
            lx = (lp['x'] + lp['speed'] * t) % (w + lp['rx'] * 2) - lp['rx']
            ly = lp['y'] + math.sin(t * 0.18 + lp['phase']) * 12
            engine.stamp_glow(scene, int(lx), int(ly), lp['sprite'])

        # 3. Cloud layers back-to-front — use pre-blurred sprites directly
        for cfg, clouds in self._cloud_layers:
            for cloud in clouds:
                cx = ((cloud['x'] + cloud['speed'] * t)
                      % (w + cloud['cw'] + 200) - cloud['cw'] - 100)
                cy = cloud['y'] + math.sin(t * 0.10 + cloud['phase']) * 7
                sprite = cloud['_sprite']
                px, py = int(cx), int(cy)
                if (px + sprite.size[0] > 0 and px < w
                        and py + sprite.size[1] > 0 and py < h):
                    scene.alpha_composite(sprite, dest=(px, py))

        # 5. Rain particles -- time-based update for smooth motion
        if not hasattr(self, '_last_t'):
            self._last_t = t
        dt = min(t - self._last_t, 0.2)  # cap at 200ms to prevent jumps
        self._last_t = t
        dt_scale = dt * 15.0  # normalize to ~1.0 at the original 15fps
        pool.update(dt=max(dt, 0.01) * 8.0)  # scale to match velocity units
        rain_draw = ImageDraw.Draw(scene)

        for i in range(pool.max):
            if pool.y[i] > h or pool.x[i] < -20:
                # Drop hit ground — splash and ripple
                if pool.y[i] > h:
                    self._maybe_splash(float(pool.x[i]), float(pool.depth[i]))
                self._respawn_drop(i)
                continue

            d = float(pool.depth[i])
            sz = float(pool.size[i])
            len_mult = float(pool.life[i])
            alpha = int(40 + 215 * d * sz)
            length = (8 + 28 * d) * len_mult
            wind = float(pool.vx[i])
            x, y = float(pool.x[i]), float(pool.y[i])

            x2 = x + wind * 0.7
            y2 = y + length

            # Split streak: dim tail (upper 40%) + bright head (lower 60%)
            mid_x = x + (x2 - x) * 0.4
            mid_y = y + (y2 - y) * 0.4

            # Storm blue-purple tones scaled by depth
            tail_c = (50 + int(30 * d), 65 + int(45 * d), 140 + int(60 * d))
            head_c = (90 + int(55 * d), 120 + int(50 * d), 200 + int(40 * d))
            w_px = 2 if sz > 0.9 else 1

            rain_draw.line(
                [(int(x), int(y)), (int(mid_x), int(mid_y))],
                fill=(*tail_c, alpha // 2), width=w_px)
            rain_draw.line(
                [(int(mid_x), int(mid_y)), (int(x2), int(y2))],
                fill=(*head_c, alpha), width=w_px)

        # 6. Splash particles -- tiny arcing ellipses with gravity
        splash_draw = ImageDraw.Draw(scene)
        alive_splashes = []
        for sp in self._splashes:
            sp['x'] += sp['vx'] * dt_scale
            sp['y'] += sp['vy'] * dt_scale
            sp['vy'] += sp['gravity'] * dt_scale
            sp['life'] -= dt_scale
            if sp['life'] <= 0:
                continue
            alive_splashes.append(sp)
            frac = sp['life'] / sp['max_life']
            a = int(220 * frac)
            sz = max(1, int(sp['size'] * frac * 2))
            sx, sy = int(sp['x']), int(sp['y'])
            # Bright white-blue splash droplet
            splash_draw.ellipse(
                [sx - sz, sy - sz, sx + sz, sy + sz],
                fill=(180, 200, 255, a))
        self._splashes = alive_splashes

        # 7. Puddle ripple rings -- expanding flat ellipse outlines
        ripple_draw = ImageDraw.Draw(scene)
        alive_ripples = []
        for rp in self._ripples:
            rp['radius'] += rp['expand_speed'] * dt_scale
            rp['life'] = max(0.0, 1.0 - rp['radius'] / rp['max_radius'])
            if rp['life'] <= 0:
                continue
            alive_ripples.append(rp)
            rx = int(rp['radius'])
            ry = max(1, rx // 3)
            a = int(100 * rp['life'])
            cx_r, cy_r = int(rp['x']), int(rp['y'])
            ripple_draw.ellipse(
                [cx_r - rx, cy_r - ry, cx_r + rx, cy_r + ry],
                outline=(120, 150, 220, a), width=1)
        self._ripples = alive_ripples

        # 8. Ground mist — stamp pre-rendered glow sprites (no per-frame blur)
        for puff in self._mist_puffs:
            mx = (puff['x'] + puff['speed'] * t) % (w + puff['rx'] * 2) - puff['rx']
            my = puff['y'] + math.sin(t * 0.22 + puff['phase']) * 5
            engine.stamp_glow(scene, int(mx), int(my), puff['sprite'])

        # 11. Vignette -- pre-rendered RGBA overlay
        scene.alpha_composite(self._vignette_img)

        return scene


# --------------------------------------------------------------------------
# Standalone test
# --------------------------------------------------------------------------
if __name__ == '__main__':
    import sys
    import time

    scene = RainScene(1920, 440)
    if '--anim' in sys.argv:
        t0 = time.time()
        for i in range(30):
            img = scene.render(i * 0.067, {})
            img.convert('RGB').save(f'/tmp/scene_rain_{i:03d}.png')
        elapsed = time.time() - t0
        print(f"Saved 30 frames to /tmp/scene_rain_*.png ({elapsed:.1f}s)")
    else:
        t0 = time.time()
        img = scene.render(1.0, {})
        img.convert('RGB').save('/tmp/scene_rain.png')
        elapsed = time.time() - t0
        print(f"Saved /tmp/scene_rain.png ({elapsed:.2f}s)")
