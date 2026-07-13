"""Tests for tray_icon module — shape drawing and indicator overlay.

TRAY-032: Tests for _draw_shape() (all 4 shapes + unknown fallback)
and _draw_shape_indicator() (overlay positioning, invalid size safety).
"""

import pytest

# Use real PIL for these tests (conftest.py mocks PIL by default)
pytestmark = pytest.mark.real_pil

from unittest.mock import MagicMock  # noqa: E402

from PIL import Image  # noqa: E402


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
        from voice_typer.server.tray_icon import _icon_cache, _make_icon
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
        from voice_typer.server.tray_icon import _icon_cache, _make_icon
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



# =============================================================================
# === Merged from test_new_perf_consolidated.py (NEW-PERF-005: DPI cache) ===
# =============================================================================
"""Regression tests for NEW-PERF-005: DPI-aware icon size caching.

Previously, ``_get_dpi_aware_icon_size()`` ran Win32 ``GetDC(0)`` +
``GetDeviceCaps`` + ``ReleaseDC`` on every tray state change.  DPI
never changes within a session, so this was pure waste (10–30 ms per
state change).

The fix caches the result after the first call.
"""

import ctypes  # noqa: E402

from voice_typer.server import tray_icon  # noqa: E402
from voice_typer.server.tray_icon import (  # noqa: E402
    _get_dpi_aware_icon_size,
    invalidate_dpi_cache,
)


def _install_fake_windll(monkeypatch):
    """Install a fake ``ctypes.windll`` (Linux doesn't have one)."""
    fake_windll = MagicMock()
    # ctypes.windll is a magic attribute on Windows; on Linux we have
    # to set it manually for the import-time `import ctypes; ctypes.windll`
    # pattern used in tray_icon.py to work.
    monkeypatch.setattr(ctypes, "windll", fake_windll, raising=False)
    return fake_windll


class TestDpiCache:
    """NEW-PERF-005: DPI-aware icon size is cached after first call."""

    @pytest.fixture(autouse=True)
    def _reset_dpi_cache(self):
        """Clear the DPI cache before/after each test."""
        invalidate_dpi_cache()
        yield
        invalidate_dpi_cache()

    def test_first_call_queries_win32(self):
        """The first call must invoke the Win32 GetDC chain."""
        # On non-Windows platforms, the function returns the base size
        # without calling GetDC.  We can still verify the cache is
        # populated.
        result = _get_dpi_aware_icon_size()
        assert isinstance(result, int)
        assert result > 0
        # Cache must be populated.
        assert tray_icon._dpi_aware_size_cache is not None
        assert tray_icon._dpi_aware_size_cache == result

    def test_second_call_uses_cache(self, monkeypatch):
        """The second call must NOT re-invoke Win32 — it returns the
        cached value directly.
        """
        # Mock the platform check + ctypes to detect calls.
        monkeypatch.setattr(tray_icon.sys, "platform", "win32")
        fake_windll = _install_fake_windll(monkeypatch)
        mock_user32 = fake_windll.user32
        mock_user32.GetDC.return_value = 0  # falsy → fallback path

        result1 = _get_dpi_aware_icon_size()
        getdc_calls_after_first = mock_user32.GetDC.call_count

        result2 = _get_dpi_aware_icon_size()
        getdc_calls_after_second = mock_user32.GetDC.call_count

        result3 = _get_dpi_aware_icon_size()
        getdc_calls_after_third = mock_user32.GetDC.call_count

        # All three results must be the same.
        assert result1 == result2 == result3
        # GetDC must only have been called ONCE (the first call).
        # The second and third calls must hit the cache.
        assert getdc_calls_after_first == 1, (
            f"GetDC called {getdc_calls_after_first} times after first call; "
            "expected 1"
        )
        assert getdc_calls_after_second == 1, (
            f"GetDC called {getdc_calls_after_second} times after second call; "
            "expected 1 (cache hit)"
        )
        assert getdc_calls_after_third == 1, (
            f"GetDC called {getdc_calls_after_third} times after third call; "
            "expected 1 (cache hit)"
        )

    def test_invalidate_cache_forces_requery(self, monkeypatch):
        """After invalidate_dpi_cache(), the next call must re-query."""
        monkeypatch.setattr(tray_icon.sys, "platform", "win32")
        fake_windll = _install_fake_windll(monkeypatch)
        mock_user32 = fake_windll.user32
        mock_user32.GetDC.return_value = 0

        _get_dpi_aware_icon_size()
        calls_after_first = mock_user32.GetDC.call_count
        assert calls_after_first == 1

        # Invalidate and call again.
        invalidate_dpi_cache()
        _get_dpi_aware_icon_size()
        calls_after_second = mock_user32.GetDC.call_count
        assert calls_after_second == 2, (
            "invalidate_dpi_cache() should force re-query on next call"
        )

    def test_dpi_scale_applied_correctly(self, monkeypatch):
        """When DPI > 96, the returned size must be scaled."""
        monkeypatch.setattr(tray_icon.sys, "platform", "win32")
        fake_windll = _install_fake_windll(monkeypatch)
        mock_user32 = fake_windll.user32
        mock_user32.GetDC.return_value = 1  # truthy
        mock_gdi32 = fake_windll.gdi32
        mock_gdi32.GetDeviceCaps.return_value = 144  # 1.5x scale

        result = _get_dpi_aware_icon_size()

        # 64 * (144/96) = 96
        assert result == 96, f"expected 96 (1.5x scale), got {result}"

    def test_default_dpi_returns_base_size(self, monkeypatch):
        """When DPI == 96 (100% scale), the returned size is the base 64."""
        monkeypatch.setattr(tray_icon.sys, "platform", "win32")
        fake_windll = _install_fake_windll(monkeypatch)
        mock_user32 = fake_windll.user32
        mock_user32.GetDC.return_value = 1
        mock_gdi32 = fake_windll.gdi32
        mock_gdi32.GetDeviceCaps.return_value = 96

        result = _get_dpi_aware_icon_size()

        assert result == 64, f"expected 64 (base), got {result}"
