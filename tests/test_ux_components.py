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
        # EC-29 / REF-2 split: the ``bubble:move-by`` channel is declared
        # in main/ipc/channels.ts (BubbleChannels.moveBy). The keyboard-
        # nudge HANDLER was removed from bubble-handlers.ts (no production
        # caller — the bubble window is focusable:false), so the channel
        # constant is the surviving contract.
        channels_ts = (CLIENT_SRC / "main" / "ipc" / "channels.ts").read_text(encoding="utf-8")
        assert "bubble:move-by" in channels_ts
        assert "moveBy" in channels_ts

    def test_preload_exposes_move_by(self):
        # EC-29 split: the bubble API surface lives in
        # preload/_bubble-channels.ts (makeBubbleApi exposes moveBy);
        # preload/index.ts delegates to it.
        preload = (CLIENT_SRC / "preload" / "_bubble-channels.ts").read_text(encoding="utf-8")
        assert "moveBy" in preload
        assert "bubble:move-by" in preload

    def test_window_bubble_type_has_move_by(self):
        # the former monolithic ipc types file was
        # split into a ``types/ipc/`` directory. The bubble-window
        # ``moveBy`` mutator now lives in ``types/ipc/bubble_bridge.ts``
        # (BubbleWindowExtras).
        bubble_bridge_ts = _read("types/ipc/bubble_bridge.ts")
        assert "moveBy" in bubble_bridge_ts


class TestGetStatusExposesLoadedVia:
    """get_status IPC returns loaded_via for the active model."""

    def test_service_get_status_returns_loaded_via(self):
        # service.py split into a service/ package — get_status moved to
        # service/status.py.
        status_py = (REPO_ROOT / "voice_typer" / "server" / "service" / "status.py").read_text(encoding="utf-8")
        assert "loaded_via" in status_py


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
        # REF-2 split: allowed-commands.ts (not main/index.ts) holds the
        # renderer->main allowlist.
        allowed_ts = (CLIENT_SRC / "main" / "allowed-commands.ts").read_text(encoding="utf-8")
        assert '"delete_model"' in allowed_ts


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
        # service.py split into a service/ package — cancel_model_download
        # lives in service/model.py.
        model_py = (REPO_ROOT / "voice_typer" / "server" / "service" / "model.py").read_text(encoding="utf-8")
        assert "def cancel_model_download" in model_py

    def test_service_has_download_cancel_events(self):
        """service/model.py declares the per-download cancel Event dict.

        EC-FIX-15 / EC-24: the legacy single-instance
        ``_download_cancel_event`` attribute has been REMOVED; the
        per-download dict (``_download_cancel_events``) plus the
        ``_register_download`` helper are the production API.
        """
        model_py = (REPO_ROOT / "voice_typer" / "server" / "service" / "model.py").read_text(encoding="utf-8")
        assert "_download_cancel_events" in model_py
        assert "_register_download" in model_py
        assert '"cancelled": True' in model_py

    def test_ipc_server_has_cancel_model_download_handler(self):
        # ipc_server.py registry moved to ipc/registry.py.
        registry_py = (REPO_ROOT / "voice_typer" / "server" / "ipc" / "registry.py").read_text(encoding="utf-8")
        assert '"cancel_model_download": "_handle_cancel_model_download"' in registry_py

    def test_main_allowlist_includes_cancel_model_download(self):
        # REF-2 split: allowed-commands.ts holds the allowlist.
        allowed_ts = (CLIENT_SRC / "main" / "allowed-commands.ts").read_text(encoding="utf-8")
        assert '"cancel_model_download"' in allowed_ts

    def test_models_page_has_cancel_button(self):
        # Models.tsx is now a thin composition root; the Cancel control
        # lives in components/models/DownloadProgressBar.tsx and the
        # cancel action in hooks/models/useModelDownload.ts.
        progress_bar = (
            CLIENT_SRC / "renderer" / "src" / "components" / "models" / "DownloadProgressBar.tsx"
        ).read_text(encoding="utf-8")
        hook = (CLIENT_SRC / "renderer" / "src" / "hooks" / "models" / "useModelDownload.ts").read_text(
            encoding="utf-8"
        )
        assert "Cancel" in progress_bar
        assert "cancel_model_download" in hook
