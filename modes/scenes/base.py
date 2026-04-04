"""Base class for all weather scenes."""

from PIL import Image


class BaseScene:
    """Abstract base for weather scene renderers.

    Subclasses must implement render(). May optionally override cleanup().
    """

    def __init__(self, w: int, h: int):
        self.w = w
        self.h = h

    def render(self, t: float, weather_data: dict) -> Image.Image:
        """Render one frame of the weather scene.

        Args:
            t: monotonic animation time in seconds.
            weather_data: dict with keys like temp_f, condition, humidity, etc.

        Returns:
            RGBA PIL Image at (self.w, self.h).
        """
        raise NotImplementedError

    def cleanup(self):
        """Release resources. Called when switching to a different scene."""
        pass
