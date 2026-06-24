"""Regression tests for Round 5 fixes.

Covers: NEW-DOC-014, NEW-UX-009/010/019/020/031,
NEW-A11Y-002/005/007, NEW-XPLAT-001, NEW-BUILD-001.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RENDERER_SRC = REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "src"


def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")


class TestNewDoc014ElectronBuilderConfig:
    """NEW-DOC-014: electron-builder.yml has code signing + auto-update."""

    def test_has_publish_config(self):
        yml = (REPO_ROOT / "voice_typer" / "client" / "electron-builder.yml").read_text()
        assert "publish:" in yml, "electron-builder.yml must have publish config"
        assert "provider: github" in yml, "Must use GitHub provider for auto-update"

    def test_has_code_signing_config(self):
        yml = (REPO_ROOT / "voice_typer" / "client" / "electron-builder.yml").read_text()
        assert "certificateFile" in yml, "Must have Windows certificate config"
        assert "signAndEditExecutable" in yml, "Must enable signAndEditExecutable"
        assert "notarize" in yml, "Must have macOS notarization config"


class TestNewUx009AboutPage:
    """NEW-UX-009: About/Diagnostics page exists."""

    def test_about_page_exists(self):
        assert (RENDERER_SRC / "pages" / "About.tsx").exists()

    def test_about_page_exported(self):
        src = _read("pages/About.tsx")
        assert "export default" in src, "About page must have default export"

    def test_sidebar_has_about_nav(self):
        src = _read("components/Sidebar.tsx")
        assert "'about'" in src or '"about"' in src, "Sidebar must have about nav item"

    def test_app_routes_to_about(self):
        src = _read("App.tsx")
        assert "case 'about'" in src, "App must route to about page"
        assert "AboutPage" in src, "App must import AboutPage"


class TestNewUx010AutoPunctuationDefault:
    """NEW-UX-010: auto_punctuation defaults ON."""

    def test_auto_punctuation_defaults_true(self):
        from voice_typer.server.config import Config
        cfg = Config()
        assert cfg.auto_punctuation is True, (
            "auto_punctuation must default to True (NEW-UX-010)"
        )


class TestNewUx020EscCancelDefault:
    """NEW-UX-020: esc_cancel_enabled defaults ON."""

    def test_esc_cancel_defaults_true(self):
        from voice_typer.server.config import Config
        cfg = Config()
        assert cfg.esc_cancel_enabled is True, (
            "esc_cancel_enabled must default to True (NEW-UX-020)"
        )


class TestNewUx019OnboardingPreservedOnReset:
    """NEW-UX-019: Reset to Defaults preserves onboarding_completed."""

    def test_reset_skips_onboarding(self):
        src = _read("pages/Settings.tsx")
        assert "onboarding_completed" in src, (
            "Settings.tsx must reference onboarding_completed in reset logic"
        )
        # The code should explicitly skip it.
        assert "intentionally preserved" in src or "skip" in src.lower(), (
            "Settings.tsx must document why onboarding_completed is preserved"
        )


class TestNewUx031SearchClearButton:
    """NEW-UX-031: Search field has clear (×) button."""

    def test_history_has_clear_button(self):
        src = _read("pages/History.tsx")
        assert "Clear search" in src or "aria-label=\"Clear search\"" in src, (
            "History search must have a clear button"
        )


class TestNewA11y002AriaLiveRecording:
    """NEW-A11Y-002: Recording start/stop has aria-live announcement."""

    def test_app_has_aria_live(self):
        src = _read("App.tsx")
        assert "aria-live" in src, "App must have aria-live region"
        assert "Recording started" in src, (
            "aria-live must announce 'Recording started'"
        )


class TestNewA11y005HighContrast:
    """NEW-A11Y-005: Windows high-contrast mode support."""

    def test_css_has_forced_colors(self):
        css = _read("index.css")
        assert "forced-colors" in css, (
            "CSS must handle forced-colors (high-contrast mode)"
        )


class TestNewA11y007LangAttribute:
    """NEW-A11Y-007: HTML has lang attribute."""

    def test_index_html_has_lang(self):
        html = (REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "index.html").read_text()
        assert 'lang="en"' in html, "index.html must have lang attribute"

    def test_bubble_html_has_lang(self):
        html = (REPO_ROOT / "voice_typer" / "client" / "src" / "renderer" / "bubble.html").read_text()
        assert 'lang="en"' in html, "bubble.html must have lang attribute"


class TestNewXplat001PlatformConfigDir:
    """NEW-XPLAT-001: _config_dir() uses platform-aware paths."""

    def test_config_dir_checks_platform(self):
        from voice_typer.server.config import _config_dir
        source = inspect.getsource(_config_dir)
        assert "sys.platform" in source or "platform" in source, (
            "_config_dir must check platform for OS-specific paths"
        )
        assert "APPDATA" in source, "Must check APPDATA on Windows"
        assert "XDG_DATA_HOME" in source, "Must check XDG_DATA_HOME on Linux"
        assert "Library" in source and "Application Support" in source, (
            "Must use Library/Application Support on macOS"
        )

    def test_legacy_path_migration(self):
        from voice_typer.server.config import _config_dir
        source = inspect.getsource(_config_dir)
        assert "legacy" in source, "Must check legacy ~/.voice-typer for migration"


class TestNewBuild001HiddenImportsPresent:
    """NEW-BUILD-001: PyInstaller spec has ASR hiddenimports."""

    def test_spec_has_asr_imports(self):
        spec = (REPO_ROOT / "scripts" / "build" / "voice-typer.spec").read_text()
        for imp in ["parakeet_engine", "qwen_engine", "transformers", "ctranslate2"]:
            assert imp in spec, f"Spec must include {imp}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
