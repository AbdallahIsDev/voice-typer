"""Tests for UI/UX components: About page, Settings, Navigation, Accessibility,
ErrorBoundary, bubble, loading screen, vocabulary/templates dialogs."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENT_SRC = REPO_ROOT / "voice_typer" / "client" / "src"
RENDERER_SRC = CLIENT_SRC / "renderer" / "src"


def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")


class TestBubbleSupportsKeyboardArrowMove:
    """Bubble supports keyboard-based repositioning via arrow keys."""

    def test_main_has_move_by_ipc_handler(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert "bubble:move-by" in main_ts
        assert "getDisplayMatching" in main_ts or "workArea" in main_ts

    def test_preload_exposes_move_by(self):
        preload = (CLIENT_SRC / "preload" / "index.ts").read_text(encoding="utf-8")
        assert "moveBy" in preload
        assert "bubble:move-by" in preload

    def test_window_bubble_type_has_move_by(self):
        # DT-31 / DT-FIX-7: the former monolithic ipc types file was
        # split into a ``types/ipc/`` directory. The bubble-window
        # ``moveBy`` mutator now lives in ``types/ipc/bubble_bridge.ts``
        # (BubbleWindowExtras).
        bubble_bridge_ts = _read("types/ipc/bubble_bridge.ts")
        assert "moveBy" in bubble_bridge_ts


class TestGetStatusExposesLoadedVia:
    """get_status IPC returns loaded_via for the active model."""

    def test_service_get_status_returns_loaded_via(self):
        service_py = (REPO_ROOT / "voice_typer" / "server" / "service.py").read_text(encoding="utf-8")
        assert "loaded_via" in service_py


class TestAboutDiagnosticsPageExists:
    """About/Diagnostics page exists and is routed."""

    def test_about_page_exists(self):
        assert (RENDERER_SRC / "pages" / "About.tsx").exists()


class TestDeleteModelRouteRemovesFiles:
    """delete_model IPC route exists."""

    def test_service_has_delete_model(self):
        from voice_typer.server.service import VoiceTyperService

        assert hasattr(VoiceTyperService, "delete_model")

    def test_ipc_has_delete_model_route(self):
        from voice_typer.server.ipc_server import IPCServer

        assert "delete_model" in IPCServer._COMMAND_REGISTRY
        assert hasattr(IPCServer, "_handle_delete_model")

    def test_renderer_allowlist_has_delete_model(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert '"delete_model"' in main_ts


class TestErrorBoundaryComponentExists:
    """ErrorBoundary component exists and is wired."""

    def test_error_boundary_file_exists(self):
        assert (RENDERER_SRC / "components" / "feedback" / "ErrorBoundary.tsx").exists()


class TestCssHandlesPrefersReducedMotion:
    """CSS handles prefers-reduced-motion."""

    def test_css_has_reduced_motion(self):
        css = _read("index.css")
        assert "prefers-reduced-motion" in css


class TestCssSupportsWindowsHighContrastMode:
    """CSS supports forced-colors (high-contrast mode)."""

    def test_css_has_forced_colors(self):
        css = _read("index.css")
        assert "forced-colors" in css


class TestIndexHtmlHasLangAttribute:
    """HTML files have lang attribute."""

    def test_index_html_has_lang(self):
        html = (CLIENT_SRC / "renderer" / "index.html").read_text()
        assert 'lang="en"' in html

    def test_bubble_html_has_lang(self):
        html = (CLIENT_SRC / "renderer" / "bubble.html").read_text()
        assert 'lang="en"' in html


class TestModelDownloadSupportsCancel:
    """Backend supports canceling an in-progress model download."""

    def test_service_has_cancel_model_download_method(self):
        service_py = (REPO_ROOT / "voice_typer" / "server" / "service.py").read_text(encoding="utf-8")
        assert "def cancel_model_download" in service_py

    def test_service_has_download_cancel_events(self):
        """service.py declares the per-download cancel Event dict.

        EC-FIX-15 / EC-24: the legacy single-instance
        ``_download_cancel_event`` attribute has been REMOVED; the
        per-download dict (``_download_cancel_events``) plus the
        ``_register_download`` helper are the production API.
        """
        service_py = (REPO_ROOT / "voice_typer" / "server" / "service.py").read_text(encoding="utf-8")
        assert "_download_cancel_events" in service_py
        assert "_register_download" in service_py
        assert '"cancelled": True' in service_py

    def test_ipc_server_has_cancel_model_download_handler(self):
        ipc_py = (REPO_ROOT / "voice_typer" / "server" / "ipc_server.py").read_text(encoding="utf-8")
        assert (
            'cmd == "cancel_model_download"' in ipc_py
            or '"cancel_model_download": "_handle_cancel_model_download"' in ipc_py
        )

    def test_main_allowlist_includes_cancel_model_download(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert '"cancel_model_download"' in main_ts

    def test_models_page_has_cancel_button(self):
        models = _read("pages/Models.tsx")
        assert "Cancel" in models
        assert "cancel_model_download" in models
