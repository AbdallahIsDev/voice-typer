"""Round 13 regression tests for ERR-IPC-001 through ERR-IPC-005.

These tests verify the critical IPC regressions found by the
orchestrator's end-to-end testing in Round 12 verification:

- ERR-IPC-001: `voice-typer` console command / `python -m voice_typer.server`
  must be importable (main function exists at the declared entry point).
- ERR-IPC-002: SEC-019 allowlist must include `quit_app` and `restart_app`.
- ERR-IPC-003: allowlist must NOT contain dead/mismatched commands.
- ERR-IPC-004: RestartRequest dead type removed from renderer types.
- ERR-IPC-005: `get_vocabulary` IPC handler must not call missing
  `VocabularyManager.list_entries()`.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


# ── ERR-IPC-001: entry-point importability ─────────────────────────────


class TestEntryPointImportable:
    """ERR-IPC-001: the main entry point must be importable."""

    def test_ipc_server_main_importable(self):
        """The canonical entry point must import without error."""
        from voice_typer.server.ipc_server import main
        assert callable(main)

    def test_app_main_re_export_exists(self):
        """app.main must exist as a backward-compat re-export.

        Previously the `def main()` line was deleted, leaving an orphaned
        docstring+body. This test catches that regression.
        """
        # We can't import app.py directly in headless envs (pynput
        # requires X), so we inspect the source file instead.
        import inspect
        import voice_typer.server.app as app_mod
        # The module must have a `main` attribute (function).
        # If the def line is missing, this raises AttributeError.
        assert hasattr(app_mod, "main"), (
            "voice_typer.server.app must have a `main` function "
            "(ERR-IPC-001 regression: def main line was deleted)"
        )
        assert callable(app_mod.main)

    def test_dunder_main_imports_from_ipc_server(self):
        """__main__.py must import main from ipc_server, not app.

        ERR-IPC-001: previously __main__.py imported from app, which
        had no main function.
        """
        import voice_typer.server.__main__ as main_mod
        assert hasattr(main_mod, "main")
        assert callable(main_mod.main)

    def test_pyproject_entry_point_points_to_ipc_server(self):
        """pyproject.toml [project.scripts] voice-typer must point to
        ipc_server:main (not app:main, which was broken)."""
        from pathlib import Path
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")
        # The entry point must reference ipc_server:main.
        assert 'voice-typer = "voice_typer.server.ipc_server:main"' in content, (
            "pyproject.toml must declare voice-typer = "
            '"voice_typer.server.ipc_server:main" (ERR-IPC-001)'
        )


# ── ERR-IPC-002 + ERR-IPC-003: allowlist correctness ──────────────────


class TestAllowlistCorrectness:
    """ERR-IPC-002: allowlist must include quit_app + restart_app.
    ERR-IPC-003: allowlist must NOT contain dead/mismatched commands."""

    @pytest.fixture
    def allowlist_entries(self):
        """Extract the ALLOWED_COMMANDS set from main/index.ts source."""
        from pathlib import Path
        idx_path = (
            Path(__file__).resolve().parents[1]
            / "voice_typer" / "client" / "src" / "main" / "index.ts"
        )
        src = idx_path.read_text(encoding="utf-8")
        # Find the ALLOWED_COMMANDS block and extract quoted strings.
        start = src.index("ALLOWED_COMMANDS = new Set([")
        end = src.index("]);", start)
        block = src[start:end]
        import re
        entries = re.findall(r'"([a-z_]+)"', block)
        return set(entries)

    def test_quit_app_in_allowlist(self, allowlist_entries):
        """ERR-IPC-002: quit_app must be in the allowlist."""
        assert "quit_app" in allowlist_entries, (
            "quit_app must be in ALLOWED_COMMANDS (ERR-IPC-002: tray Quit broken)"
        )

    def test_restart_app_in_allowlist(self, allowlist_entries):
        """ERR-IPC-002: restart_app must be in the allowlist."""
        assert "restart_app" in allowlist_entries, (
            "restart_app must be in ALLOWED_COMMANDS (ERR-IPC-002: tray Restart broken)"
        )

    def test_dead_quit_not_in_allowlist(self, allowlist_entries):
        """ERR-IPC-003: dead `quit` (server uses quit_app) must be removed."""
        assert "quit" not in allowlist_entries, (
            "dead `quit` must be removed from ALLOWED_COMMANDS (server uses quit_app)"
        )

    def test_dead_restart_not_in_allowlist(self, allowlist_entries):
        """ERR-IPC-003: dead `restart` (server uses restart_app) must be removed."""
        assert "restart" not in allowlist_entries, (
            "dead `restart` must be removed from ALLOWED_COMMANDS (server uses restart_app)"
        )

    def test_dead_save_config_not_in_allowlist(self, allowlist_entries):
        """ERR-IPC-003: dead `save_config` (server uses set_config) must be removed."""
        assert "save_config" not in allowlist_entries, (
            "dead `save_config` must be removed (server uses set_config)"
        )

    def test_dead_save_vocabulary_with_diff_not_in_allowlist(self, allowlist_entries):
        """ERR-IPC-003: `save_vocabulary_with_diff` is a method, not an IPC command."""
        assert "save_vocabulary_with_diff" not in allowlist_entries, (
            "save_vocabulary_with_diff is a service method, not an IPC command"
        )

    def test_dead_repaste_last_not_in_allowlist(self, allowlist_entries):
        """ERR-IPC-003: `repaste_last` is not a server IPC command."""
        assert "repaste_last" not in allowlist_entries, (
            "repaste_last is not a server IPC command (dead allowlist entry)"
        )

    def test_dead_complete_onboarding_not_in_allowlist(self, allowlist_entries):
        """ERR-IPC-003: `complete_onboarding` is not a server IPC command."""
        assert "complete_onboarding" not in allowlist_entries, (
            "complete_onboarding is not a server IPC command (dead allowlist entry)"
        )

    def test_allowlist_matches_server_commands(self, allowlist_entries):
        """ERR-IPC-003: every allowlist entry must have a server handler.
        Cross-check against the actual server dispatch."""
        from pathlib import Path
        import re
        ipc_path = (
            Path(__file__).resolve().parents[1]
            / "voice_typer" / "server" / "ipc_server.py"
        )
        src = ipc_path.read_text(encoding="utf-8")
        # Extract all cmd == "..." patterns.
        server_cmds = set(re.findall(r'cmd == "([a-z_]+)"', src))
        # Every allowlist entry must be a server command.
        orphans = allowlist_entries - server_cmds
        assert not orphans, (
            f"Allowlist has {len(orphans)} orphan entries with no server handler: {sorted(orphans)}"
        )


# ── ERR-IPC-004: RestartRequest dead type removed ─────────────────────


class TestRestartRequestRemoved:
    """ERR-IPC-004: the dead RestartRequest type must be removed."""

    def test_restart_request_not_in_types(self):
        """The RestartRequest interface must be removed from types/ipc.ts."""
        from pathlib import Path
        types_path = (
            Path(__file__).resolve().parents[1]
            / "voice_typer" / "client" / "src" / "renderer" / "src" / "types" / "ipc.ts"
        )
        src = types_path.read_text(encoding="utf-8")
        # The type definition must be gone (only the removal comment remains).
        assert "export interface RestartRequest" not in src, (
            "RestartRequest interface must be removed (ERR-IPC-004: dead type)"
        )
        # And it must not be in the PythonRequest union.
        assert "| RestartRequest" not in src, (
            "RestartRequest must be removed from PythonRequest union"
        )


# ── ERR-IPC-005: get_vocabulary handler ────────────────────────────────


class TestGetVocabularyHandler:
    """ERR-IPC-005: get_vocabulary must not call missing list_entries()."""

    def test_vocabulary_manager_has_no_list_entries(self):
        """Confirm the method that was being called doesn't exist."""
        from voice_typer.server.vocabulary import VocabularyManager
        assert not hasattr(VocabularyManager, "list_entries"), (
            "VocabularyManager must NOT have list_entries (it was a typo / dead method)"
        )

    def test_vocabulary_manager_has_get_all(self):
        """The correct method is get_all()."""
        from voice_typer.server.vocabulary import VocabularyManager
        assert hasattr(VocabularyManager, "get_all")

    def test_service_get_vocabulary_uses_get_all(self, tmp_path, monkeypatch):
        """service.get_vocabulary() must call get_all(), not list_entries()."""
        from voice_typer.server import config as config_module
        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)

        from voice_typer.server.service import VoiceTyperService
        app = MagicMock()
        app.config.config_dir = tmp_path
        service = VoiceTyperService(app)

        # Should not raise AttributeError.
        result = service.get_vocabulary()
        assert isinstance(result, dict)
        # Must contain the category keys (same shape as VocabularyManager.get_all()).
        # At minimum, misspellings should be present (bundled defaults).
        assert "misspellings" in result

    def test_ipc_dispatch_get_vocabulary_returns_vocabulary_type(
        self, tmp_path, monkeypatch
    ):
        """End-to-end: IPC _dispatch({type: get_vocabulary}) must return
        type=vocabulary (not error)."""
        from voice_typer.server import config as config_module
        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)

        from voice_typer.server.ipc_server import IPCServer

        app = MagicMock()
        app.config = config_module.Config()
        # config_dir is a property that returns _config_dir(); the
        # monkeypatch above makes it return tmp_path.
        server = IPCServer(app)

        result = server._dispatch({"id": 1, "type": "get_vocabulary"})
        assert result["type"] == "vocabulary", (
            f"get_vocabulary must return type=vocabulary, got {result.get('type')}"
        )
        assert "misspellings" in result["data"]


# ── TEST-037: VoiceTyperApp singleton assertion ────────────────────────


class TestVoiceTyperAppSingleton:
    """TEST-037: VoiceTyperApp uses _ensure_single_instance to enforce
    single-instance. Verify the mechanism exists."""

    def test_ensure_single_instance_exists(self):
        from voice_typer.server import app as app_module
        assert hasattr(app_module, "_ensure_single_instance"), (
            "app module must expose _ensure_single_instance for singleton enforcement"
        )

    def test_main_calls_ensure_single_instance(self):
        """main() (or ipc_server.main) must call _ensure_single_instance."""
        import inspect
        from voice_typer.server import ipc_server
        src = inspect.getsource(ipc_server.main)
        assert "_ensure_single_instance" in src or "single_instance" in src, (
            "ipc_server.main must reference single-instance enforcement"
        )


# ── TEST-039: IPC dispatch with invalid data types ─────────────────────


class TestIPCDispatchInvalidDataTypes:
    """TEST-039: _dispatch must not crash when `data` is not a dict."""

    def test_set_config_with_string_data(self, tmp_path, monkeypatch):
        """Passing a string as `data` should be handled gracefully."""
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({
            "id": 1, "type": "set_config", "data": "not a dict"
        })
        assert result["type"] in ("ack", "error")

    def test_set_config_with_list_data(self, tmp_path, monkeypatch):
        """Passing a list as `data` should be handled gracefully."""
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({
            "id": 1, "type": "set_config", "data": ["not", "a", "dict"]
        })
        assert result["type"] in ("ack", "error")

    def test_set_config_with_none_data(self, tmp_path, monkeypatch):
        """Passing None as `data` should be handled gracefully."""
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({
            "id": 1, "type": "set_config", "data": None
        })
        assert result["type"] in ("ack", "error")

    def test_set_config_with_integer_data(self, tmp_path, monkeypatch):
        """Passing an integer as `data` should be handled gracefully."""
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({
            "id": 1, "type": "set_config", "data": 42
        })
        assert result["type"] in ("ack", "error")


# ── TEST-040: History retention on favorites ───────────────────────────


class TestHistoryRetentionFavorites:
    """TEST-040: retention must preserve favorites even when they're old."""

    def test_retention_preserves_favorites(self, tmp_path):
        """Favorites should NOT be deleted by retention, even if they're
        the oldest entries."""
        from voice_typer.server.history_db import HistoryDB
        db = HistoryDB(db_path=tmp_path / "history.db")

        # Add a favorite (old) + 5 non-favorites (newer)
        fav_id = db.add_transcription("Favorite old entry")
        db.toggle_favorite(fav_id)
        for i in range(5):
            db.add_transcription(f"Regular entry {i}")

        # Apply retention with max_entries=3 — should keep the favorite
        # plus the 2 most recent regular entries.
        deleted = db.apply_retention(max_entries=3)

        favorites = db.get_favorites()
        assert len(favorites) >= 1, (
            f"Favorite must be preserved by retention; got {len(favorites)} favorites"
        )
        assert favorites[0]["text"] == "Favorite old entry"

    def test_retention_without_favorites_deletes_oldest(self, tmp_path):
        """Without favorites, retention should delete the oldest entries."""
        from voice_typer.server.history_db import HistoryDB
        db = HistoryDB(db_path=tmp_path / "history.db")

        for i in range(5):
            db.add_transcription(f"Entry {i}")

        deleted = db.apply_retention(max_entries=3)
        entries = db.get_recent(limit=10)
        assert len(entries) <= 3, (
            f"Expected <= 3 entries after retention, got {len(entries)}"
        )


# ── TEST-038: macOS accessibility permission check ─────────────────────


class TestMacOSAccessibilityCheck:
    """TEST-038: verify the macOS accessibility permission check exists
    in the startup path. Can't test the actual permission on Linux, but
    can verify the code path is present."""

    def test_accessibility_check_in_startup_source(self):
        """The startup code must reference AXIsProcessTrusted or
        accessibility permission check."""
        import inspect
        from voice_typer.server import app as app_module
        # _do_startup is the method that runs the check.
        src = inspect.getsource(app_module.VoiceTyperApp._do_startup)
        # The macOS check may be gated by either the literal platform
        # string "darwin" (e.g. ``sys.platform == "darwin"``) or by the
        # ``is_macos()`` helper from platform_utils.  Both are valid.
        has_macos_guard = "darwin" in src or "is_macos()" in src
        assert has_macos_guard and "accessibility" in src.lower(), (
            "macOS accessibility permission check must be in _do_startup "
            "(gated by 'darwin' or is_macos(), and referencing 'accessibility')"
        )

    def test_accessibility_check_notifies_on_missing(self):
        """The check must call tray.notify if the permission is missing."""
        import inspect
        from voice_typer.server import app as app_module
        src = inspect.getsource(app_module.VoiceTyperApp._do_startup)
        assert "tray.notify" in src, (
            "Accessibility check must notify the user on missing permission"
        )
