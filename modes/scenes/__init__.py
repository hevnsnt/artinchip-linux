"""Weather scene registry for tinyscreen clock mode."""

from scenes.base import BaseScene
from scenes.rain import RainScene
from scenes.thunder import ThunderScene
from scenes.snow import SnowScene
from scenes.overcast import OvercastScene
from scenes.overcast import OvercastScene as FogScene
from scenes.sunny import SunnyScene
from scenes.hot import HotScene
from scenes.partly_cloudy import PartlyCloudyScene

SCENE_MAP = {
    'rain': RainScene,
    'thunder': ThunderScene,
    'snow': SnowScene,
    'overcast': OvercastScene,
    'fog': OvercastScene,
    'sunny': SunnyScene,
    'hot': HotScene,
    'partly_cloudy': PartlyCloudyScene,
}

__all__ = ['SCENE_MAP', 'BaseScene']
