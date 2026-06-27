"""Regression tests for NEW-PERF-005: DPI-aware icon size caching.

Previously, ``_get_dpi_aware_icon_size()`` ran Win32 ``GetDC(0)`` +
``GetDeviceCaps`` + ``ReleaseDC`` on every tray state change.  DPI
never changes within a session, so this was pure waste (10–30 ms per
state change).

The fix caches the result after the first call.
"""
from __future__ import annotations

import ctypes
from unittest.mock import MagicMock, patch, call  # TEST-033: unified mock import

import pytest

from voice_typer.server import tray_icon
from voice_typer.server.tray_icon import (
    _get_dpi_aware_icon_size,
    invalidate_dpi_cache,
)


@pytest.fixture(autouse=True)
def _reset_dpi_cache():
    """Clear the DPI cache before and after each test."""
    invalidate_dpi_cache()
    yield
    invalidate_dpi_cache()


def _install_fake_windll(monkeypatch):
    """Install a fake ``ctypes.windll`` (Linux doesn't have one)."""
    fake_windll = MagicMock()
    # ctypes.windll is a magic attribute on Windows; on Linux we have
    # to set it manually for the import-time `import ctypes; ctypes.windll`
    # pattern used in tray_icon.py to work.
    monkeypatch.setattr(ctypes, "windll", fake_windll, raising=False)
    return fake_windll


class TestDpiCache:
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

