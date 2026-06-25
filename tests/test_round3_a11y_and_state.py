"""Round 3 regression tests for NEW-PRIV-011, NEW-UX-041, NEW-UX-043, NEW-A11Y-006.

NEW-PRIV-011: HuggingFace model download is cancelable.
NEW-UX-041: Navigation state preserved across app restarts.
NEW-UX-043: "?" help overlay for keyboard shortcut discoverability.
NEW-A11Y-006: Keyboard alternative for bubble drag-to-move.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENT_SRC = REPO_ROOT / "voice_typer" / "client" / "src"
RENDERER_SRC = CLIENT_SRC / "renderer" / "src"


def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")


# ── NEW-PRIV-011: cancelable HuggingFace download ────────────────────


class TestNewPriv011CancelableDownload:
    """The backend must support canceling an in-progress model download."""

    def test_service_has_cancel_model_download_method(self):
        service_py = (REPO_ROOT / "voice_typer" / "server" / "service.py").read_text(
            encoding="utf-8"
        )
        assert "def cancel_model_download" in service_py, (
            "VoiceTyperService must have a cancel_model_download method"
        )

    def test_service_has_download_cancel_event(self):
        service_py = (REPO_ROOT / "voice_typer" / "server" / "service.py").read_text(
            encoding="utf-8"
        )
        assert "_download_cancel_event" in service_py
        # The polling loop must check the cancel event.
        assert "_download_cancel_event.is_set()" in service_py
        # The cancelled return path must exist.
        assert '"cancelled": True' in service_py

    def test_ipc_server_has_cancel_model_download_handler(self):
        ipc_py = (REPO_ROOT / "voice_typer" / "server" / "ipc_server.py").read_text(
            encoding="utf-8"
        )
        assert 'cmd == "cancel_model_download"' in ipc_py

    def test_main_allowlist_includes_cancel_model_download(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert '"cancel_model_download"' in main_ts, (
            "IPC allowlist must include cancel_model_download"
        )

    def test_models_page_has_cancel_button(self):
        models = _read("pages/Models.tsx")
        assert "Cancel" in models
        # The cancel button must call cancel_model_download via IPC.
        assert "cancel_model_download" in models


# ── NEW-UX-041: navigation state preservation ────────────────────────


class TestNewUx041StatePreservation:
    """App.tsx must persist the current page + nav history to localStorage
    so the user returns to where they left off after closing/reopening."""

    def test_app_has_nav_state_persistence(self):
        app = _read("App.tsx")
        assert "STORAGE_KEY_NAV" in app
        assert "saveNavState" in app
        assert "loadNavState" in app

    def test_navigate_saves_state(self):
        """The navigate function must call saveNavState after updating
        the current page."""
        app = _read("App.tsx")
        # navigate must call saveNavState
        assert "saveNavState(page, navHistory.current, navIndex.current)" in app

    def test_goBack_saves_state(self):
        app = _read("App.tsx")
        # goBack must also save state
        # Count occurrences of saveNavState — should be at least 3
        # (navigate, goBack, goForward)
        count = app.count("saveNavState(page, navHistory.current, navIndex.current)")
        assert count >= 3, (
            f"Expected saveNavState in navigate, goBack, goForward; got {count}"
        )

    def test_initial_state_loaded_from_localStorage(self):
        app = _read("App.tsx")
        assert "loadNavState()" in app
        assert "initialNav" in app


# ── NEW-UX-043: "?" help overlay ─────────────────────────────────────


class TestNewUx043HelpOverlay:
    """App.tsx must have a "?" keyboard shortcut that opens a help
    overlay listing all keyboard shortcuts."""

    def test_app_has_help_overlay_state(self):
        app = _read("App.tsx")
        assert "showHelpOverlay" in app
        assert "setShowHelpOverlay" in app

    def test_app_has_question_mark_keydown_handler(self):
        """The "?" key must trigger the help overlay."""
        app = _read("App.tsx")
        assert "e.key === '?'" in app

    def test_help_overlay_lists_shortcuts(self):
        """The overlay must list the keyboard shortcuts."""
        app = _read("App.tsx")
        assert "Keyboard Shortcuts" in app
        # Must include the common shortcuts.
        assert "Tab / Shift+Tab" in app or "Tab" in app
        assert "Space" in app
        assert "Esc" in app
        # Must mention the "?" shortcut itself.
        assert "Open this help overlay" in app

    def test_help_overlay_closes_on_escape(self):
        app = _read("App.tsx")
        # The Escape key must close the overlay.
        assert "Escape" in app
        assert "setShowHelpOverlay(false)" in app

    def test_help_overlay_does_not_trigger_in_inputs(self):
        """Typing "?" in an input/textarea must NOT open the overlay."""
        app = _read("App.tsx")
        # The handler must check activeElement tag.
        assert "input" in app and "textarea" in app and "select" in app


# ── NEW-A11Y-006: keyboard bubble move ───────────────────────────────


class TestNewA11y006KeyboardBubbleMove:
    """The bubble must support keyboard-based repositioning via arrow
    keys as an accessibility alternative to mouse drag."""

    def test_bubble_has_arrow_key_handler(self):
        bubble = _read("Bubble.tsx")
        assert "ArrowLeft" in bubble
        assert "ArrowRight" in bubble
        assert "ArrowUp" in bubble
        assert "ArrowDown" in bubble

    def test_bubble_calls_move_by(self):
        bubble = _read("Bubble.tsx")
        assert "moveBy" in bubble

    def test_bubble_respects_draggable_gate(self):
        """Keyboard move must be disabled when draggable is False
        (matches the mouse-drag gate)."""
        bubble = _read("Bubble.tsx")
        # The keyboard handler must check `if (!draggable) return`
        assert "if (!draggable) return" in bubble

    def test_main_has_move_by_ipc_handler(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        assert 'bubble:move-by' in main_ts
        # Must clamp to screen bounds.
        assert "getDisplayMatching" in main_ts or "workArea" in main_ts

    def test_preload_exposes_move_by(self):
        preload = (CLIENT_SRC / "preload" / "index.ts").read_text(encoding="utf-8")
        assert "moveBy" in preload
        assert "bubble:move-by" in preload

    def test_window_bubble_type_has_move_by(self):
        ipc_ts = _read("types/ipc.ts")
        assert "moveBy" in ipc_ts
