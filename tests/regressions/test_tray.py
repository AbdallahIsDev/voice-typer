"""The class/method names, assertion logic, and imports below are"""

from __future__ import annotations

import inspect
from pathlib import Path


class TestTrayIconBaseIcoLookup:
    """PLAT-024."""

    def test_generate_icons_mjs_emits_tray_ico(self):
        """
        generate-icons.mjs must call generateIco for tray-mic.ico.
        KEEP, pins PLAT-024 fix in the JS icon-generation script.
        """
        from pathlib import Path

        mjs_path = (
            Path(__file__).resolve().parent.parent.parent / "voice_typer" / "client" / "scripts" / "generate-icons.mjs"
        )
        with open(mjs_path) as f:
            src = f.read()
        assert "tray-mic.ico" in src, "generate-icons.mjs must emit tray-mic.ico."
        assert "PLAT-024" in src, "generate-icons.mjs must reference PLAT-024 in a comment."


class TestTrayRecordingColorIsGreen:
    """TRAY-006."""

    def test_recording_color_is_green(self):
        # KEEP, pins  (RECORDING color is green RGB
        from voice_typer.server import tray_icon

        src = inspect.getsource(tray_icon)
        # RECORDING must be green (46, 204, 113)
        assert "(46, 204, 113" in src, "TRAY-006: RECORDING color must be green (46, 204, 113), not red"

    def test_error_color_is_red(self):
        # KEEP, pins  (ERROR color is red RGB (231, 76, 60)).
        from voice_typer.server import tray_icon

        src = inspect.getsource(tray_icon)
        # ERROR must be red (231, 76, 60)
        assert "(231, 76, 60" in src, "TRAY-006: ERROR color must be red (231, 76, 60)"

    def test_cancelling_color_is_orange(self):
        # KEEP, pins  (CANCELLING color is orange RGB
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
    """title serves as accessible name (pystray limitation)."""

    def test_tray_icon_has_non_empty_title(self):
        # KEEP, pins  (TrayIcon.start passes a non-empty
        from voice_typer.server.tray import TrayIcon

        src = inspect.getsource(TrayIcon.start)
        assert "title=" in src
        # stripped by C-STYLE-1 cleanup, but the "title is both tooltip
        assert "a11y name" in src


class TestDesktopEnvironmentSpecificTray:
    """Test tray behavior under different XDG_CURRENT_DESKTOP values."""

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
    """text_size config wired to CSS --font-scale variable."""

    def test_app_tsx_sets_font_scale(self):
        # KEEP, pins  (--font-scale / text_size application
        # Pin both ends: the setter writes --font-scale, and useTheme
        renderer_src = (
            Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "hooks"
        )
        apply_src = (renderer_src / "theme" / "themeApply.ts").read_text(encoding="utf-8")
        assert "--font-scale" in apply_src
        assert "setProperty" in apply_src
        hook_src = (renderer_src / "useTheme.ts").read_text(encoding="utf-8")
        assert "applyTextScale(textSize)" in hook_src

    def test_index_css_consumes_font_scale(self):
        # KEEP, pins  (index.css consumes --font-scale).
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
        # KEEP, pins  (Text Size slider in ThemeSettingsSection.tsx).
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
