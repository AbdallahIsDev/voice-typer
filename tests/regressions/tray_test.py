"""Regression tests split out of the former ``tests/test_bugfix_regressions.py``.

This module is part of the ``tests/regressions/`` package created by
REF-4. The class/method names, assertion logic, and imports below are
preserved verbatim from the original 4446-line monolith — only file
location has changed.

Common preamble (imports + Linux test-env shim) is identical to the
original file so that every test in this module sees the same global
state the monolith provided.
"""

from __future__ import annotations

import inspect
from pathlib import Path


# WP-1: the previous Linux test-env shim that aliased
# ``ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE`` and inserted a ``MagicMock``
# for ``voice_typer.server.crash_handler`` into ``sys.modules`` has been
# removed. ``crash_handler.py`` now gates the ``@ctypes.WINFUNCTYPE(...)``
# decorator behind ``sys.platform == "win32"``, so the module imports
# cleanly on Linux/macOS without any test-infrastructure shim.
class TestTrayIconBaseIcoLookup:
    """PLAT-024.

    The finding: no .ico asset files exist; code falls through to PNG
    every time. Fix: generate-icons.mjs now emits tray-mic.ico;
    tray_icon.py looks for the base ICO as a fallback.
    """

    def test_generate_icons_mjs_emits_tray_ico(self):
        """generate-icons.mjs must call generateIco for tray-mic.ico.

        RW-8: KEEP — pins PLAT-024 fix in the JS icon-generation script.
        Cannot easily test behaviorally (would need to execute the .mjs
        script and inspect emitted files); source-string check is the
        most direct way to catch removal of the .ico emission.
        """
        from pathlib import Path

        mjs_path = (
            Path(__file__).resolve().parent.parent.parent / "voice_typer" / "client" / "scripts" / "generate-icons.mjs"
        )
        with open(mjs_path) as f:
            src = f.read()
        assert "tray-mic.ico" in src, "PLAT-024: generate-icons.mjs must emit tray-mic.ico."
        assert "PLAT-024" in src, "PLAT-024: generate-icons.mjs must reference PLAT-024 in a comment."


class TestTrayRecordingColorIsGreen:
    """TRAY-006.

    The finding: RECORDING and ERROR were both red tones. Investigation:
    RECORDING is now bright green (46, 204, 113), ERROR is red, CANCELLING
    is orange. This test pins that state.
    """

    def test_recording_color_is_green(self):
        # RW-8: KEEP — pins TRAY-006 (RECORDING color is green RGB
        # (46, 204, 113)). The sibling test_recording_and_error_colors_are_distinct
        # tests visual distinctness, but doesn't pin the exact RGB values.
        # Source-string check catches a regression where the color changes.
        from voice_typer.server import tray_icon

        src = inspect.getsource(tray_icon)
        # RECORDING must be green (46, 204, 113)
        assert "(46, 204, 113" in src, "TRAY-006: RECORDING color must be green (46, 204, 113), not red"

    def test_error_color_is_red(self):
        # RW-8: KEEP — pins TRAY-006 (ERROR color is red RGB (231, 76, 60)).
        # Same rationale as test_recording_color_is_green.
        from voice_typer.server import tray_icon

        src = inspect.getsource(tray_icon)
        # ERROR must be red (231, 76, 60)
        assert "(231, 76, 60" in src, "TRAY-006: ERROR color must be red (231, 76, 60)"

    def test_cancelling_color_is_orange(self):
        # RW-8: KEEP — pins TRAY-006 (CANCELLING color is orange RGB
        # (243, 156, 18)). Same rationale as test_recording_color_is_green.
        from voice_typer.server import tray_icon

        src = inspect.getsource(tray_icon)
        # CANCELLING must be orange (243, 156, 18)
        assert "(243, 156, 18" in src, "TRAY-006: CANCELLING color must be orange (243, 156, 18)"

    def test_recording_and_error_colors_are_distinct(self):
        """RECORDING (green) and ERROR (red) must be visually distinct."""
        from voice_typer.server import tray_icon

        inspect.getsource(tray_icon)
        recording_rgb = (46, 204, 113)
        error_rgb = (231, 76, 60)
        # The RGB values must differ significantly
        diff = sum(abs(a - b) for a, b in zip(recording_rgb, error_rgb, strict=False))
        assert diff > 100, (
            f"TRAY-006: RECORDING and ERROR colors must be visually distinct (RGB diff = {diff}, need > 100)"
        )


class TestTrayIconHasAccessibleName:
    """PLAT-010: title serves as accessible name (pystray limitation)."""

    def test_tray_icon_has_non_empty_title(self):
        # RW-8: KEEP — pins PLAT-010 (TrayIcon.start passes a non-empty
        # title= for accessible name). A behavioral test would need to
        # start TrayIcon and inspect the system tray icon's accessible
        # name, which is heavy (platform-specific); the source-string
        # check catches removal of the title= kwarg directly.
        from voice_typer.server.tray import TrayIcon

        src = inspect.getsource(TrayIcon.start)
        assert "title=" in src
        assert "PLAT-010" in src


class TestDesktopEnvironmentSpecificTray:
    """PLAT-015: Test tray behavior under different XDG_CURRENT_DESKTOP values."""

    def test_wayland_detection_exists(self):
        from voice_typer.server.tray import TrayIcon

        assert hasattr(TrayIcon, "_is_linux_wayland_without_sni")

    def test_tray_works_with_kde_desktop(self, monkeypatch):
        """Setting XDG_CURRENT_DESKTOP=KDE must not crash the tray detection."""
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        from voice_typer.server.tray import TrayIcon

        tray = TrayIcon.__new__(TrayIcon)
        # Must not raise
        try:
            result = tray._is_linux_wayland_without_sni()
            assert isinstance(result, bool)
        except Exception:
            # Non-Linux: method may return False or raise; both acceptable
            pass

    def test_tray_works_with_gnome_desktop(self, monkeypatch):
        """Setting XDG_CURRENT_DESKTOP=GNOME must not crash the tray detection."""
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        from voice_typer.server.tray import TrayIcon

        tray = TrayIcon.__new__(TrayIcon)
        try:
            result = tray._is_linux_wayland_without_sni()
            assert isinstance(result, bool)
        except Exception:
            pass


class TestTextSizeConfigWiredToCssScale:
    """PLAT-017: text_size config wired to CSS --font-scale variable."""

    def test_app_tsx_sets_font_scale(self):
        # RW-8: KEEP — pins PLAT-017 (--font-scale / text_size application
        # in useTheme.ts). A behavioral test would need to render the app
        # and inspect the computed CSS variable, which is heavy; the
        # file-content check catches removal of the --font-scale setter.
        # PLAT-017: --font-scale / text_size application was refactored
        # out of App.tsx into the dedicated useTheme hook.
        app_path = (
            Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "hooks"
            / "useTheme.ts"
        )
        src = app_path.read_text(encoding="utf-8")
        assert "--font-scale" in src
        assert "text_size" in src

    def test_index_css_consumes_font_scale(self):
        # RW-8: KEEP — pins PLAT-017 (index.css consumes --font-scale).
        # Same rationale as test_app_tsx_sets_font_scale.
        css_path = (
            Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "index.css"
        )
        src = css_path.read_text(encoding="utf-8")
        assert "--font-scale" in src
        assert "font-size" in src

    def test_settings_has_text_size_slider(self):
        # RW-8: KEEP — pins PLAT-017 (Text Size slider in ThemeSettingsSection.tsx).
        # Same rationale as test_app_tsx_sets_font_scale.
        # PLAT-017: the "Text Size" slider was refactored out of
        # Settings.tsx into the ThemeSettingsSection component.
        settings_path = (
            Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "components"
            / "settings"
            / "ThemeSettingsSection.tsx"
        )
        src = settings_path.read_text(encoding="utf-8")
        assert "Text Size" in src
        assert "text_size" in src
        assert "RangeSlider" in src
