"""Tests for tray_icon module — shape drawing and indicator overlay.

TRAY-032: Tests for _draw_shape() (all 4 shapes + unknown fallback)
and _draw_shape_indicator() (overlay positioning, invalid size safety).
"""

import pytest

# Use real PIL for these tests (conftest.py mocks PIL by default)
pytestmark = pytest.mark.real_pil

from unittest.mock import MagicMock, patch
from PIL import Image


class TestDrawShape:
    """TRAY-032: _draw_shape renders each shape correctly."""

    def test_draw_circle(self):
        from voice_typer.server.tray_icon import _draw_shape
        img = _draw_shape("circle", 32, (255, 0, 0, 255))
        assert img.size == (32, 32)
        assert img.mode == "RGBA"
        # Circle should have non-transparent pixels in the center
        center = img.getpixel((16, 16))
        assert center[3] > 0  # alpha > 0 (something drawn)

    def test_draw_square(self):
        from voice_typer.server.tray_icon import _draw_shape
        img = _draw_shape("square", 32, (0, 255, 0, 255))
        assert img.size == (32, 32)
        assert img.mode == "RGBA"
        center = img.getpixel((16, 16))
        assert center[3] > 0

    def test_draw_diamond(self):
        from voice_typer.server.tray_icon import _draw_shape
        img = _draw_shape("diamond", 32, (0, 0, 255, 255))
        assert img.size == (32, 32)
        assert img.mode == "RGBA"
        # Diamond has pixels in the center (it's a rotated square)
        center = img.getpixel((16, 16))
        assert center[3] > 0

    def test_draw_triangle(self):
        from voice_typer.server.tray_icon import _draw_shape
        img = _draw_shape("triangle", 32, (255, 255, 0, 255))
        assert img.size == (32, 32)
        assert img.mode == "RGBA"
        # Triangle has pixels near center-bottom
        center = img.getpixel((16, 20))
        assert center[3] > 0

    def test_unknown_shape_falls_back_to_circle(self):
        """Unknown shape names should fall back to circle without crashing."""
        from voice_typer.server.tray_icon import _draw_shape
        img = _draw_shape("hexagon", 32, (128, 128, 128, 255))
        assert img.size == (32, 32)
        assert img.mode == "RGBA"
        center = img.getpixel((16, 16))
        assert center[3] > 0  # circle fallback drawn

    @pytest.mark.parametrize("size", [16, 24, 32, 48, 64])
    def test_draw_shape_various_sizes(self, size):
        """_draw_shape should produce correct size for various icon sizes."""
        from voice_typer.server.tray_icon import _draw_shape
        img = _draw_shape("circle", size, (255, 0, 0, 255))
        assert img.size == (size, size)
        assert img.mode == "RGBA"

    def test_draw_shape_preserves_color(self):
        """The drawn shape should use the provided RGBA color."""
        from voice_typer.server.tray_icon import _draw_shape
        color = (46, 204, 113, 255)  # green
        img = _draw_shape("square", 32, color)
        # Find a non-transparent pixel and verify its color
        for x in range(32):
            for y in range(32):
                px = img.getpixel((x, y))
                if px[3] > 0:
                    assert px[0] == color[0]
                    assert px[1] == color[1]
                    assert px[2] == color[2]
                    assert px[3] == color[3]
                    return
        pytest.fail("No non-transparent pixel found in shape")

    def test_draw_shape_background_is_transparent(self):
        """The background (outside the shape) should be fully transparent."""
        from voice_typer.server.tray_icon import _draw_shape
        img = _draw_shape("circle", 32, (255, 0, 0, 255))
        # Top-left corner should be transparent (outside the shape margin)
        corner = img.getpixel((0, 0))
        assert corner[3] == 0  # fully transparent


class TestDrawShapeIndicator:
    """TRAY-032: _draw_shape_indicator overlays shape in bottom-right corner."""

    def test_indicator_returns_image(self):
        from voice_typer.server.tray_icon import _draw_shape_indicator
        base = Image.new("RGBA", (32, 32), (0, 0, 0, 255))
        result = _draw_shape_indicator(base, "circle", (255, 0, 0, 255))
        assert isinstance(result, Image.Image)
        assert result.size == (32, 32)

    def test_indicator_does_not_modify_original(self):
        """_draw_shape_indicator should return a copy, not modify the original."""
        from voice_typer.server.tray_icon import _draw_shape_indicator
        base = Image.new("RGBA", (32, 32), (0, 0, 0, 255))
        original_pixel = base.getpixel((0, 0))
        _draw_shape_indicator(base, "circle", (255, 0, 0, 255))
        # Original should be unchanged
        assert base.getpixel((0, 0)) == original_pixel

    def test_indicator_circle_shape(self):
        from voice_typer.server.tray_icon import _draw_shape_indicator
        base = Image.new("RGBA", (32, 32), (0, 0, 0, 255))
        result = _draw_shape_indicator(base, "circle", (255, 0, 0, 255))
        assert result.size == (32, 32)
        # Bottom-right area should have indicator pixels
        br_pixel = result.getpixel((28, 28))
        assert br_pixel[3] > 0  # something drawn in indicator area

    def test_indicator_square_shape(self):
        from voice_typer.server.tray_icon import _draw_shape_indicator
        base = Image.new("RGBA", (32, 32), (0, 0, 0, 255))
        result = _draw_shape_indicator(base, "square", (0, 255, 0, 255))
        assert result.size == (32, 32)

    def test_indicator_diamond_shape(self):
        from voice_typer.server.tray_icon import _draw_shape_indicator
        base = Image.new("RGBA", (32, 32), (0, 0, 0, 255))
        result = _draw_shape_indicator(base, "diamond", (0, 0, 255, 255))
        assert result.size == (32, 32)

    def test_indicator_triangle_shape(self):
        from voice_typer.server.tray_icon import _draw_shape_indicator
        base = Image.new("RGBA", (32, 32), (0, 0, 0, 255))
        result = _draw_shape_indicator(base, "triangle", (255, 255, 0, 255))
        assert result.size == (32, 32)

    def test_indicator_invalid_size_returns_image_with_warning(self, caplog):
        """When image has invalid .size, return image unchanged and log warning."""
        import logging
        from voice_typer.server.tray_icon import _draw_shape_indicator

        # Create a real image but make its .size return None by
        # patching at the class level on the copied image.
        real_img = Image.new("RGBA", (32, 32), (0, 0, 0, 255))

        # The function does: img = img.copy() then checks img.size
        # Patch Image.Image.size to return None for this test
        original_size = Image.Image.size
        try:
            Image.Image.size = property(lambda self: None)  # type: ignore[assignment]
            with caplog.at_level(logging.WARNING):
                result = _draw_shape_indicator(real_img, "circle", (255, 0, 0, 255))
        finally:
            Image.Image.size = original_size  # type: ignore[assignment]

        assert isinstance(result, Image.Image)
        assert "invalid size" in caplog.text.lower() or "invalid size" in caplog.text

    def test_indicator_empty_size_tuple_returns_image_with_warning(self, caplog):
        """When image has empty size tuple, return image and log warning."""
        import logging
        from voice_typer.server.tray_icon import _draw_shape_indicator

        real_img = Image.new("RGBA", (32, 32), (0, 0, 0, 255))

        original_size = Image.Image.size
        try:
            Image.Image.size = property(lambda self: ())  # type: ignore[assignment]
            with caplog.at_level(logging.WARNING):
                result = _draw_shape_indicator(real_img, "circle", (255, 0, 0, 255))
        finally:
            Image.Image.size = original_size  # type: ignore[assignment]

        assert isinstance(result, Image.Image)
        assert "invalid size" in caplog.text.lower() or "invalid size" in caplog.text

    def test_indicator_3_tuple_size_returns_image_with_warning(self, caplog):
        """When image has 3-element size tuple, return image and log warning."""
        import logging
        from voice_typer.server.tray_icon import _draw_shape_indicator

        real_img = Image.new("RGBA", (32, 32), (0, 0, 0, 255))

        original_size = Image.Image.size
        try:
            Image.Image.size = property(lambda self: (32, 32, 4))  # type: ignore[assignment]
            with caplog.at_level(logging.WARNING):
                result = _draw_shape_indicator(real_img, "circle", (255, 0, 0, 255))
        finally:
            Image.Image.size = original_size  # type: ignore[assignment]

        assert isinstance(result, Image.Image)
        assert "invalid size" in caplog.text.lower() or "invalid size" in caplog.text

    @pytest.mark.parametrize("size", [16, 32, 48, 64])
    def test_indicator_various_sizes(self, size):
        """Indicator should work with various icon sizes."""
        from voice_typer.server.tray_icon import _draw_shape_indicator
        base = Image.new("RGBA", (size, size), (0, 0, 0, 255))
        result = _draw_shape_indicator(base, "circle", (255, 0, 0, 255))
        assert result.size == (size, size)

    def test_indicator_positioned_in_bottom_right(self):
        """The indicator should be drawn in the bottom-right corner area."""
        from voice_typer.server.tray_icon import _draw_shape_indicator
        base = Image.new("RGBA", (64, 64), (0, 0, 0, 0))  # transparent base
        color = (255, 0, 0, 255)
        result = _draw_shape_indicator(base, "square", color)
        # Bottom-right quadrant should have indicator pixels
        # (the indicator is ~20% of icon size = ~13px for 64px icon)
        br_pixel = result.getpixel((58, 58))
        assert br_pixel[3] > 0  # indicator drawn in bottom-right


class TestMakeIcon:
    """TRAY-032: _make_icon integration test — shape-only fallback path."""

    def test_make_icon_shape_fallback_when_no_png(self, monkeypatch):
        """When no PNG icon is available, _make_icon falls back to shape-only."""
        from voice_typer.server.tray_icon import _make_icon, _icon_cache
        from voice_typer.server.tray_types import AppState

        # Clear the cache
        _icon_cache.clear()

        # Patch _get_icon_path to return None (no PNG available)
        monkeypatch.setattr("voice_typer.server.tray_icon._get_icon_path", lambda state, size=0: None)

        icon = _make_icon(AppState.IDLE, size=32)
        assert icon is not None
        assert icon.size == (32, 32)
        assert icon.mode == "RGBA"

        # Cleanup
        _icon_cache.clear()

    def test_make_icon_caches_result(self, monkeypatch):
        """_make_icon should cache and return the same image for same state+size."""
        from voice_typer.server.tray_icon import _make_icon, _icon_cache
        from voice_typer.server.tray_types import AppState

        _icon_cache.clear()
        monkeypatch.setattr("voice_typer.server.tray_icon._get_icon_path", lambda state, size=0: None)

        icon1 = _make_icon(AppState.IDLE, size=32)
        icon2 = _make_icon(AppState.IDLE, size=32)
        assert icon1 is icon2  # same object from cache

        _icon_cache.clear()

    def test_icon_shapes_map_covers_all_states(self):
        """Every AppState should have a shape defined in _ICON_SHAPES."""
        from voice_typer.server.tray_icon import _ICON_SHAPES
        from voice_typer.server.tray_types import AppState
        for state in AppState:
            assert state in _ICON_SHAPES, f"AppState.{state.name} missing from _ICON_SHAPES"
