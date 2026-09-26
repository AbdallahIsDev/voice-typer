"""Consolidated regression tests for the NEW-DEAD-xxx series (dead-code removal)."""

# NOTE: search.py is now LIVE (history_db.py delegates to it). Only recovery.py remains deleted.

# === Common imports (deduplicated from all source files) ===

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import hotkeys
from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher
from voice_typer.server.hotkeys import HotkeyBackend
from voice_typer.server.ipc_server import IPCServer

# === Common module-level constants (identical across files) ===

SCRIPTS_DIR = Path(__file__).resolve().parent / "manual"

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

# === Common helpers / fixtures (identical across files) ===


@pytest.fixture
def server_with_mock_app():
    app = MagicMock()
    return IPCServer(app)


# === Source: tests/test_new_dead_002_scripts.py ===

"""Regression tests for NEW-DEAD-002: scripts/diagnostics/* were broken
against the refactored code.

Previously:
1. ``runtime_proof.py`` imported from ``voice_typer.config``,
   ``voice_typer.transcription``, and ``voice_typer.tray``, all of
   which were moved to ``voice_typer.server.*`` during the package
   reorganization.  The script would crash on import.
2. ``runtime_test_runner.py`` grepped the production log for Flet-era
   markers (``_busy reset to False``, ``FORCE RECOVER``, ``HOTKEY
   FIRED``) that no longer exist.  The script would always report
   failure even when the production code worked correctly.

The fix:
- Updated imports in ``runtime_proof.py`` to use the canonical paths.
- Updated grep patterns in ``runtime_test_runner.py`` to use the
  current production markers (``[TRANSCRIBE] Transcription complete``,
  ``Audio too short, skipping transcription``, ``FORCE RECOVER``).
"""


class TestRuntimeProofImports:
    """NEW-DEAD-002: runtime_proof.py must import from the right paths."""

    def test_script_parses_without_syntax_error(self):
        """The script must parse cleanly (no syntax errors)."""
        script_path = SCRIPTS_DIR / "runtime_proof.py"
        source = script_path.read_text()
        ast.parse(source)

    def test_script_imports_from_server_package(self):
        """legacy top-level ``voice_typer.*`` paths."""
        script_path = SCRIPTS_DIR / "runtime_proof.py"
        source = script_path.read_text()
        # The legacy broken imports would say "from voice_typer.config",
        assert "from voice_typer.server.config import" in source, (
            "runtime_proof.py must import Config from voice_typer.server.config"
        )
        assert "from voice_typer.server.transcription import" in source, (
            "runtime_proof.py must import TranscriptionEngine from voice_typer.server.transcription"
        )
        assert "from voice_typer.server.tray_types import" in source, (
            "runtime_proof.py must import AppState from voice_typer.server.tray_types"
        )
        # The legacy paths must NOT appear.
        assert "from voice_typer.config import" not in source, (
            "runtime_proof.py still imports from the legacy voice_typer.config path"
        )
        assert "from voice_typer.transcription import" not in source, (
            "runtime_proof.py still imports from the legacy voice_typer.transcription path"
        )
        assert "from voice_typer.tray import" not in source, (
            "runtime_proof.py still imports from the legacy voice_typer.tray path"
        )


class TestRuntimeTestRunnerMarkers:
    """NEW-DEAD-002: runtime_test_runner.py must grep for current markers."""

    def test_script_parses_without_syntax_error(self):
        script_path = SCRIPTS_DIR / "runtime_test_runner.py"
        source = script_path.read_text()
        ast.parse(source)

    def test_uses_current_transcription_complete_marker(self):
        """The runner must look for ``[TRANSCRIBE] Transcription complete``"""
        source = (SCRIPTS_DIR / "runtime_test_runner.py").read_text()
        assert "[TRANSCRIBE] Transcription complete" in source, (
            "runtime_test_runner.py must grep for '[TRANSCRIBE] Transcription complete' (current production marker)"
        )

    def test_does_not_rely_on_legacy_busy_reset_marker(self):
        """The legacy ``_busy reset to False`` marker is no longer"""
        source = (SCRIPTS_DIR / "runtime_test_runner.py").read_text()
        # Easiest check: the wait_for_log call must not pass the legacy
        assert 'wait_for_log(LOG_FILE, "_busy reset to False"' not in source, (
            "runtime_test_runner.py still uses the legacy _busy reset to False marker as a wait_for_log argument"
        )

    def test_force_recover_still_checked(self):
        """FORCE RECOVER is still emitted by recording_controller.py:623,"""
        source = (SCRIPTS_DIR / "runtime_test_runner.py").read_text()
        assert "FORCE RECOVER" in source, (
            "runtime_test_runner.py should still check for FORCE RECOVER (still emitted by recording_controller.py:623)"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# === Source: tests/test_new_dead_003_font_svg.py ===

"""Regression tests for NEW-DEAD-003: dead font + SVG references in
pyproject.toml.

The ``pyproject.toml`` previously declared
``"voice_typer.server" = ["assets/fonts/hgi-stroke-rounded.ttf"]`` as
package-data, but:
- The file (1.9 MB) was never loaded by any Python source file.
- The new predecessor app uses ``@hugeicons/react`` from npm instead.
- The ``assets/icons/`` directory referenced 19 SVG files that were
  also unused.

The fix removes the package-data reference, saving 1.9 MB in the
built wheel and installer.
"""


class TestPyprojectNoDeadFontReference:
    """NEW-DEAD-003: pyproject.toml must not reference the dead font."""

    def test_no_hgi_font_reference(self):
        """The 1.9 MB font file must not be referenced as package-data."""
        content = PYPROJECT.read_text()
        assert "hgi-stroke-rounded" not in content, (
            "pyproject.toml still references the dead hgi-stroke-rounded.ttf "
            "font (1.9 MB, never loaded by any Python source file)"
        )

    def test_no_package_data_section_for_fonts(self):
        """The ``[tool.setuptools.package-data]`` section must not"""
        content = PYPROJECT.read_text()
        # The section may exist for other purposes, but it must not
        if "[tool.setuptools.package-data]" in content:
            # Find the section and check its contents.
            start = content.index("[tool.setuptools.package-data]")
            # Find the next section header.
            end = len(content)
            for marker in ("\n[",):
                idx = content.find(marker, start + 1)
                if idx != -1:
                    end = min(end, idx)
            section = content[start:end]
            assert "hgi" not in section.lower(), f"package-data section still references hgi font: {section}"
            assert "fonts/" not in section, f"package-data section still references fonts/ directory: {section}"
            assert "icons/" not in section, f"package-data section still references icons/ directory: {section}"


class TestNoPythonCodeLoadsTheFont:
    """Sanity check: no Python source file should reference the font"""

    def test_no_python_imports_hgi_font(self):
        """No Python file in voice_typer/ should reference the font."""
        root = Path(__file__).resolve().parent.parent / "voice_typer"
        offenders = []
        for py_file in root.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if "hgi-stroke-rounded" in content or "hgi_stroke" in content:
                offenders.append(str(py_file))
        assert not offenders, f"Python files still reference the dead font: {offenders}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# === Source: tests/test_new_dead_009_diagnose.py ===

"""Regression tests for NEW-DEAD-009: HotkeyBackend.diagnose no longer @abstractmethod.

Previously ``diagnose`` was declared ``@abstractmethod``, forcing every
subclass to implement a debug string even though only test callers
invoke it.  The fix provides a default no-op implementation so new
backends don't have to implement it just to satisfy the Protocol.
"""


class TestDiagnoseNotAbstract:
    """NEW-DEAD-009: diagnose must not be @abstractmethod."""

    def test_diagnose_has_default_implementation(self):
        """The HotkeyBackend base class must provide a default"""
        assert hasattr(HotkeyBackend, "diagnose"), "HotkeyBackend must have a diagnose method"
        source = inspect.getsource(HotkeyBackend.diagnose)
        assert 'return ""' in source, (
            "HotkeyBackend.diagnose must have a default implementation that returns an empty string"
        )

    def test_diagnose_not_marked_abstract(self):
        """The method must not be decorated with @abstractmethod."""
        source = inspect.getsource(HotkeyBackend)
        diag_start = source.find("def diagnose")
        assert diag_start != -1, "diagnose method not found in HotkeyBackend source"
        # Look at the 5 lines before the def to check for @abstractmethod.
        lines_before = source[:diag_start].splitlines()[-5:]
        for line in lines_before:
            assert "@abstractmethod" not in line, (
                "HotkeyBackend.diagnose must not be decorated with @abstractmethod "
                "(NEW-DEAD-009: should have a default implementation so new "
                "backends don't have to implement it)"
            )

    def test_subclasses_can_skip_diagnose_override(self):
        """A new subclass that doesn't override diagnose must be"""

        class MinimalBackend(HotkeyBackend):
            def start(self, callback):
                pass

            def register(self, hotkey, callback, on_release=None):
                pass

            def unregister(self, hotkey):
                pass

            def stop(self):
                pass

            def is_alive(self):
                return False

            # Note: NO diagnose override.

        backend = MinimalBackend("<f2>")
        assert backend.diagnose() == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# === Source: tests/test_new_dead_010_ptt_wiring.py ===

"""Regression tests for NEW-DEAD-010: PTT (push-to-talk) mode must be
fully wired.

Previously ``HotkeyBackend.set_on_release`` was half-wired, the
config UI exposed PTT mode but key-release did not stop recording.
NEW-CQ-029 fixed this by:
1. Adding key-up transition detection to the Win32 polling backend.
2. Wiring ``hotkey_dispatcher._register()`` to call
   ``set_on_release(app._stop_dictation)`` when
   ``config.recording_mode == "push_to_talk"``.

These tests verify the wiring is in place and the key-up callback
fires the stop-dictation path.
"""


class TestPttWiring:
    """NEW-DEAD-010: PTT mode must wire set_on_release to _stop_dictation."""

    def test_dispatcher_sets_on_release_in_ptt_mode(self):
        """When recording_mode is 'push_to_talk', the dispatcher must"""
        register_src = inspect.getsource(HotkeyDispatcher.register)
        helper_src = inspect.getsource(HotkeyDispatcher._create_and_start_main_backend)
        combined_src = register_src + "\n" + helper_src
        assert "set_on_release" in combined_src, (
            "HotkeyDispatcher must call set_on_release for PTT mode (in register or _create_and_start_main_backend)"
        )
        assert "push_to_talk" in combined_src, (
            "HotkeyDispatcher must check recording_mode == 'push_to_talk' "
            "(in register or _create_and_start_main_backend)"
        )
        assert "_stop_dictation" in combined_src, (
            "HotkeyDispatcher must wire set_on_release to app._stop_dictation "
            "(in register or _create_and_start_main_backend)"
        )

    def test_win32_backend_fires_on_release_on_key_up(self):
        """fire ``_on_release_callback``."""
        source = inspect.getsource(hotkeys.WindowsNativeHotkey._run_polling_loop)
        assert "_on_release_callback" in source, (
            "WindowsNativeHotkey._run_polling_loop must reference _on_release_callback"
        )
        # The key-up detection logic: "not is_pressed and was_pressed".
        assert "not is_pressed and was_pressed" in source, (
            "WindowsNativeHotkey._run_polling_loop must detect key-up transitions"
        )

    def test_pynput_backend_fires_on_release(self):
        """The pynput backend must fire _on_release_callback in its"""
        # Find the PynputHotkey class's on_release closure.
        source = inspect.getsource(hotkeys.PynputHotkey._start_fallback)
        assert "_on_release_callback" in source, "PynputHotkey._start_fallback must reference _on_release_callback"
        assert "on_release" in source, "PynputHotkey._start_fallback must register an on_release handler"

    def test_set_on_release_stores_callback(self):
        """``self._on_release_callback``."""
        # Create a minimal backend instance to test set_on_release.
        backend = hotkeys.PynputHotkey.__new__(hotkeys.PynputHotkey)
        backend._on_release_callback = None

        callback_called = []

        def my_callback():
            callback_called.append(True)

        backend.set_on_release(my_callback)
        assert backend._on_release_callback is my_callback

        # Fire it.
        backend._on_release_callback()
        assert callback_called == [True]

    def test_set_on_release_accepts_none(self):
        """``set_on_release(None)`` must clear the callback (allowing"""
        backend = hotkeys.PynputHotkey.__new__(hotkeys.PynputHotkey)
        backend._on_release_callback = lambda: None

        backend.set_on_release(None)
        assert backend._on_release_callback is None


class TestPttFunctionalFlow:
    """Functional test: simulate the PTT wiring end-to-end."""

    def test_ptt_wiring_calls_stop_dictation_on_key_release(self):
        """``app._stop_dictation``."""
        app = MagicMock()
        app.config.hotkey = "<f2>"
        app.config.recording_mode = "push_to_talk"
        app.config.esc_cancel_enabled = False
        app.config.repaste_hotkey = ""
        app.toggle_dictation = MagicMock()
        app._stop_dictation = MagicMock()

        dispatcher = HotkeyDispatcher(app)

        # Mock the backend so we can capture the set_on_release call.
        fake_backend = MagicMock()
        fake_backend.is_alive.return_value = True

        with patch(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            return_value=fake_backend,
        ):
            dispatcher.register()

        fake_backend.set_on_release.assert_called_once_with(app._stop_dictation)

    def test_toggle_mode_does_not_set_on_release(self):
        """In toggle mode (not push_to_talk), set_on_release must NOT"""
        app = MagicMock()
        app.config.hotkey = "<f2>"
        app.config.recording_mode = "toggle"
        app.config.esc_cancel_enabled = False
        app.config.repaste_hotkey = ""
        app.toggle_dictation = MagicMock()
        app._stop_dictation = MagicMock()

        dispatcher = HotkeyDispatcher(app)

        fake_backend = MagicMock()
        fake_backend.is_alive.return_value = True

        with patch(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            return_value=fake_backend,
        ):
            dispatcher.register()

        # set_on_release must NOT have been called.
        fake_backend.set_on_release.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# === Source: tests/test_new_dead_015_llm_test_connection.py ===

"""Regression tests for NEW-DEAD-015: LLMPolisher.test_connection wired up.

Previously ``LLMPolisher.test_connection()`` was dead, defined but
never invoked by any IPC route or UI button.  The fix:

1. Adds ``LausuService.test_llm_connection()`` that constructs an
   LLMPolisher from the live config and calls ``test_connection()``.
2. Adds an IPC route ``test_llm_connection`` that delegates to the
   service method.
3. Adds ``test_llm_connection`` to the renderer's IPC command
   allowlist so the predecessor main process will forward it.
"""


class TestServiceTestMethod:
    """NEW-DEAD-015: LausuService must expose test_llm_connection()."""

    def test_service_has_test_llm_connection_method(self):
        from voice_typer.server.service import LausuService

        assert hasattr(LausuService, "test_llm_connection"), (
            "LausuService must have a test_llm_connection method so the renderer can test the LLM polish API connection"
        )

    def test_service_returns_failure_when_no_api_key(self, server_with_mock_app):
        """When the config has no llm_api_key, the service must return"""
        srv = server_with_mock_app
        # Mock config with empty key.
        srv.app.config = MagicMock()
        srv.app.config.llm_api_key = ""
        srv.app.config.llm_api_url = ""
        srv.app.config.llm_model = ""
        srv.app.config.llm_preset = "professional"

        result = srv.service.test_llm_connection()
        assert result["success"] is False
        assert "key" in result["message"].lower()

    def test_service_constructs_polisher_and_calls_test(self, server_with_mock_app):
        """LLMPolisher and call its test_connection() method."""
        srv = server_with_mock_app
        srv.app.config = MagicMock()
        srv.app.config.llm_api_key = "sk-test-key"
        srv.app.config.llm_api_url = "https://api.openai.com/v1"
        srv.app.config.llm_model = "gpt-4"
        srv.app.config.llm_preset = "professional"

        # Mock the LLMPolisher constructor + test_connection.
        fake_polisher = MagicMock()
        fake_polisher.test_connection.return_value = (True, "Connected (model: gpt-4)")

        with patch(
            "voice_typer.server.llm_polish.LLMPolisher",
            return_value=fake_polisher,
        ) as mock_ctor:
            result = srv.service.test_llm_connection()

        assert result["success"] is True
        assert "Connected" in result["message"]
        # Constructor was called with the config values.
        mock_ctor.assert_called_once()
        _, kwargs = mock_ctor.call_args
        assert kwargs["api_key"] == "sk-test-key"
        assert kwargs["api_url"] == "https://api.openai.com/v1"
        assert kwargs["model"] == "gpt-4"


class TestDispatchesTestLlmConnection:
    """dispatcher routed ``test_llm_connection`` to the service-layer"""

    def test_ipc_does_not_dispatch_test_llm_connection(self, server_with_mock_app):
        """``_dispatch({'type': 'test_llm_connection'})`` must NOT call"""
        srv = server_with_mock_app
        srv.service.test_llm_connection = MagicMock(return_value={"success": True, "message": "Connected"})

        result = srv._dispatch({"id": 1, "type": "test_llm_connection"})

        srv.service.test_llm_connection.assert_not_called()
        assert result["type"] == "error", (
            "ZR-45: `test_llm_connection` was removed from _COMMAND_REGISTRY; "
            "dispatch must return an error envelope, not a result. If the "
            "command was intentionally re-added, update _COMMAND_REGISTRY + "
            "the renderer allowlist + this test together."
        )
        assert result["data"].get("code") == "server.unknown_command", (
            "ZR-45: the error envelope should carry the `server.unknown_command` "
            f"code; got {result['data'].get('code')!r}"
        )

    def test_ipc_handles_service_exception_when_command_not_registered(self, server_with_mock_app):
        """the prior test_ipc_handles_service_exception asserted"""
        srv = server_with_mock_app
        # Even if the service method WOULD raise, the dispatch must not
        srv.service.test_llm_connection = MagicMock(side_effect=RuntimeError("boom"))

        result = srv._dispatch({"id": 1, "type": "test_llm_connection"})

        srv.service.test_llm_connection.assert_not_called()
        assert result["type"] == "error"
        # The error is the unknown-command envelope, NOT the "boom"
        assert "boom" not in result["data"].get("message", "")
        assert result["data"].get("code") == "server.unknown_command"


class TestRendererAllowlist:
    """the Rust renderer allowlist must NOT include"""

    def test_rust_allowlist_does_not_include_test_llm_connection(self):
        from tests.test_security_doc_command_count import _allowed_commands_rust

        assert "test_llm_connection" not in _allowed_commands_rust(), (
            "ZR-45: `test_llm_connection` was removed from _COMMAND_REGISTRY; "
            "the Rust allowed_commands() set must NOT include it. If the "
            "command was intentionally re-added to _COMMAND_REGISTRY, update "
            "the Rust allowlist + this test together."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# === Source:, ipc/ subpackage dead-code removal ===

"""Regression tests for ``voice_typer/server/ipc/`` subpackage is
NOT a parallel implementation of ``ipc_server.py``.

Phase 4.5 / ARCH-045 began a split of the original ``ipc_server.py``
god-module into a per-concern package.  The split was abandoned
mid-way: the shim ``ipc_server.py`` retained the full implementation
AND the parallel ``ipc/server.py`` (1,764 lines), ``ipc/main.py``
(389), ``ipc/process_meta.py`` (25), and ``ipc/push_events.py`` (60)
existed as unreachable duplicates (~2,238+ lines of dead code).

The fix deletes the four dead modules and keeps only the leaf
submodules that the handler mixins actually import (``validation``,
``history_bounds``, ``rate_limiter``, ``transport``).
"""


class TestIpcDeadCodeStaysRemoved:
    """the four dead parallel-implementation modules must stay gone."""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "voice_typer/server/ipc/server.py",
            "voice_typer/server/ipc/main.py",
            "voice_typer/server/ipc/process_meta.py",
            "voice_typer/server/ipc/push_events.py",
        ],
    )
    def test_dead_ipc_module_does_not_exist(self, rel_path):
        """Each deleted parallel-implementation module must not exist on disk."""
        repo_root = Path(__file__).resolve().parent.parent
        assert not (repo_root / rel_path).exists(), (
            f" regression: {rel_path} was deleted as dead-code parallel of "
            "ipc_server.py, do NOT re-create it. The canonical implementation "
            "lives in voice_typer/server/ipc_server.py."
        )

    @pytest.mark.parametrize(
        "mod_path",
        [
            "voice_typer.server.ipc.server",
            "voice_typer.server.ipc.main",
            "voice_typer.server.ipc.process_meta",
            "voice_typer.server.ipc.push_events",
        ],
    )
    def test_dead_ipc_module_is_not_importable(self, mod_path):
        """Each deleted module must raise ModuleNotFoundError on import."""
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod_path)

    def test_ipc_init_does_not_re_export_ipcserver_or_main(self):
        """``main``, those names live only in ``ipc_server.py`` (the shim"""
        import voice_typer.server.ipc as ipc_pkg

        assert not hasattr(ipc_pkg, "IPCServer"), (
            "ipc/__init__.py must not re-export IPCServer, it lives in "
            "voice_typer.server.ipc_server. Re-exporting it re-creates the "
            "parallel-system surface that  removed."
        )
        assert not hasattr(ipc_pkg, "main"), (
            "ipc/__init__.py must not re-export main, it lives in "
            "voice_typer.server.ipc_server. Re-exporting it re-creates the "
            "parallel-system surface that  removed."
        )

    def test_ipc_init_does_not_re_export_push_event_now_or_process_meta(self):
        """The ``ipc`` package __init__ must NOT re-export"""
        import voice_typer.server.ipc as ipc_pkg

        assert not hasattr(ipc_pkg, "_push_event_now"), (
            "ipc/__init__.py must not re-export _push_event_now, its "
            "source module ipc/push_events.py was deleted as dead code."
        )
        assert not hasattr(ipc_pkg, "_set_process_metadata"), (
            "ipc/__init__.py must not re-export _set_process_metadata, "
            "its source module ipc/process_meta.py was deleted as dead code."
        )

    def test_leaf_submodules_still_importable(self):
        """The surviving leaf submodules must still be importable."""
        import importlib

        for mod_path in [
            "voice_typer.server.ipc.validation",
            "voice_typer.server.ipc.history_bounds",
            "voice_typer.server.ipc.rate_limiter",
            "voice_typer.server.ipc.transport",
        ]:
            importlib.import_module(mod_path)

    def test_ipc_server_imports_TCPLineIO_from_transport(self):  # noqa: N802
        """the deadlock fix in ``close``), not define a parallel copy."""
        import inspect

        from voice_typer.server import ipc_server
        from voice_typer.server.ipc import transport as ipc_transport

        # The class object identity must match, no parallel copy.
        assert ipc_server._TCPLineIO is ipc_transport._TCPLineIO, (
            "ipc_server._TCPLineIO must be the SAME class object as "
            "ipc.transport._TCPLineIO (single source of truth for the "
            " close()-deadlock fix). A parallel copy would let the "
            "bugged close() resurface."
        )
        # The source file must be transport.py, not ipc_server.py.
        src_file = inspect.getsourcefile(ipc_server._TCPLineIO)
        assert src_file is not None and src_file.endswith("transport.py"), (
            f"_TCPLineIO source must be transport.py; got {src_file!r}."
        )

    def test_ipc_server_TCPLineIO_close_uses_shutdown(self):  # noqa: N802
        """``_TCPLineIO.close`` must call ``shutdown(SHUT_RDWR)``"""
        import inspect

        from voice_typer.server.ipc_server import _TCPLineIO

        src = inspect.getsource(_TCPLineIO.close)
        assert "shutdown" in src, (
            "_TCPLineIO.close must call self.conn.shutdown(SHUT_RDWR) to interrupt in-progress reads ( deadlock fix)."
        )
        assert "SHUT_RDWR" in src, (
            "_TCPLineIO.close must use socket.SHUT_RDWR (full duplex shutdown) to interrupt both reads and writes."
        )


class TestExtendUrlAllowlistIsWired:
    """
    ``voice_typer/server/_secrets.py`` is LIVE production code.
    The XZ-SEC-05 wiring landed 2026-08-02: the env-var bootstrap
    """

    # XZ-SEC-05 wiring drifted and must be reviewed.
    _EXPECTED_CALLERS: frozenset[str] = frozenset(
        {
            # Config.load re-applies persisted trusted_extra_hosts.
            "voice_typer/server/config/loader.py",
            # ConfigHandlersMixin: set_config trusted_extra_hosts re-apply
            "voice_typer/server/handlers/config_handlers.py",
            # Pack downloads: service/offline_pack/gates.py filters model-pack URLs
            "voice_typer/server/service/offline_pack/gates.py",
        }
    )

    def test_production_callers_of_extend_url_allowlist_are_expected(self) -> None:
        """AST-walk every ``.py`` file under ``voice_typer/`` (excluding"""
        import ast
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        voice_typer_dir = repo_root / "voice_typer"
        offender_files: set[str] = set()

        for py_file in voice_typer_dir.rglob("*.py"):
            # Skip the function's own definition file(s): the former
            if py_file.name == "_secrets.py" or py_file.name == "url_allowlist.py":
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    # Direct name call: extend_url_allowlist(...)
                    if (
                        isinstance(func, ast.Name)
                        and func.id == "extend_url_allowlist"
                        or (isinstance(func, ast.Attribute) and func.attr == "extend_url_allowlist")
                    ):
                        # Normalize to forward slashes so the comparison
                        offender_files.add(str(py_file.relative_to(repo_root)).replace("\\", "/"))

        unexpected = offender_files - self._EXPECTED_CALLERS
        missing = self._EXPECTED_CALLERS - offender_files
        assert not unexpected and not missing, (
            "XZ-SEC-05 regression: `extend_url_allowlist` production "
            f"callers drifted. Unexpected callers: {sorted(unexpected)!r}; "
            f"expected callers missing: {sorted(missing)!r}. Expected set: "
            f"{sorted(self._EXPECTED_CALLERS)!r}. Review the wiring and "
            "update _EXPECTED_CALLERS deliberately (never silently)."
        )

    def test_dead_code_marker_removed_from_secrets_module(self) -> None:
        """function is live (XZ-SEC-05 wired), so the marker would now be"""
        from voice_typer.server import _secrets

        src = inspect.getsource(_secrets)
        assert "DEAD-CODE" not in src, (
            "XZ-SEC-05 regression: the DEAD-CODE marker for "
            "`extend_url_allowlist` is back in `_secrets.py`, but the "
            "function is live (Config.load + add_trusted_endpoint IPC + "
            "env-var bootstrap). Remove the marker again."
        )


# === Source: dead history_db_internals/recovery.py ===


class TestHistoryDbInternalsRecoveryModuleStaysRemoved:
    """The ``HistoryDB`` class methods that handle corruption recovery"""

    _DEAD_MODULES = ("voice_typer.server.history_db_internals.recovery",)

    def test_recovery_module_stays_removed(self) -> None:
        """``voice_typer.server.history_db_internals.recovery`` must"""
        import importlib

        try:
            importlib.import_module("voice_typer.server.history_db_internals.recovery")
        except ModuleNotFoundError:
            return  # expected, the module is gone
        raise AssertionError(
            "regression: voice_typer.server.history_db_internals.recovery "
            "is importable again. This module was deleted as 519 LOC of dead code "
            "(0 importers, 0% coverage). The HistoryDB corruption-recovery methods "
            "(_maybe_recover_from_corruption, _backup_before_migration, "
            "_try_iterdump_recovery) all live in voice_typer.server.history_db "
            "itself, recovery.py was an unused extraction. Re-delete the file."
        )

    def test_recovery_file_does_not_exist_on_disk(self) -> None:
        """disk. The import-not-importable test above is the primary"""
        repo_root = Path(__file__).resolve().parent.parent
        pkg_dir = repo_root / "voice_typer" / "server" / "history_db_internals"
        recovery_py = pkg_dir / "recovery.py"
        assert not recovery_py.exists(), (
            "regression: voice_typer/server/history_db_internals/"
            "recovery.py exists on disk. This file was deleted as 519 LOC "
            "of dead code. Re-delete it (and check the archive/"
            "deleted_files.txt entry)."
        )

    def test_no_source_file_imports_the_recovery_module(self) -> None:
        """No ``.py`` file under ``voice_typer/`` or ``tests/`` may"""
        repo_root = Path(__file__).resolve().parent.parent
        offender_files: set[str] = set()
        for sub in ("voice_typer", "tests"):
            root = repo_root / sub
            if not root.is_dir():
                continue
            for py_file in root.rglob("*.py"):
                try:
                    source = py_file.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                try:
                    tree = ast.parse(source, filename=str(py_file))
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    # ``from voice_typer.server.history_db_internals.recovery import ...``
                    if (
                        isinstance(node, ast.ImportFrom)
                        and node.module
                        and (node.module == "voice_typer.server.history_db_internals.recovery")
                    ):
                        offender_files.add(str(py_file.relative_to(repo_root)))
                    # ``import voice_typer.server.history_db_internals.recovery``
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name in ("voice_typer.server.history_db_internals.recovery",):
                                offender_files.add(str(py_file.relative_to(repo_root)))

        assert not offender_files, (
            "regression: the following files import the dead "
            "module `history_db_internals.recovery`. "
            f"Offenders: {sorted(offender_files)!r}. This module was deleted "
            "as dead code; restore the deletion (and re-route any caller to the "
            "live `HistoryDB` methods on `voice_typer.server.history_db`)."
        )


REPO_ROOT = Path(__file__).resolve().parent.parent
# ``voice_typer/server/config.py`` was split into a package
CONFIG_MODULE_PATHS = [
    *(
        p
        for d in (
            REPO_ROOT / "voice_typer" / "server" / "config",
            REPO_ROOT / "voice_typer" / "server" / "config_internals",
        )
        for p in sorted(d.glob("*.py"))
        if d.is_dir()
    ),
    *sorted((REPO_ROOT / "voice_typer" / "server").glob("config*.py")),
]


class TestLegacyConfigDirRemoved:
    """``_legacy_config_dir`` was dead and is now deleted."""

    def test_config_py_does_not_define_legacy_config_dir(self):
        """The function definition must be gone from all config module sources."""
        assert CONFIG_MODULE_PATHS, (
            "Expected at least one config module file under "
            "voice_typer/server/config/ and voice_typer/server/config*.py, "
            "module layout may have changed again; update CONFIG_MODULE_PATHS."
        )
        offenders: list[str] = []
        for path in CONFIG_MODULE_PATHS:
            try:
                source = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "def _legacy_config_dir" in source:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        assert not offenders, (
            "Config modules still define _legacy_config_dir, the dead-code removal "
            "should have deleted it (no callers in the repo, no entry "
            f"points in pyproject.toml, no setup.py). Offenders: {offenders}"
        )

    def test_legacy_config_dir_not_importable(self):
        """``from voice_typer.server.config import _legacy_config_dir``"""
        from voice_typer.server import config as config_mod

        assert not hasattr(config_mod, "_legacy_config_dir"), (
            "voice_typer.server.config still exposes _legacy_config_dir"
        )

    def test_no_callers_of_legacy_config_dir_anywhere_in_repo(self):
        """Sanity: no Python file in the repo should still reference"""
        offenders: list[str] = []
        for py_file in (REPO_ROOT / "voice_typer").rglob("*.py"):
            try:
                text = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "_legacy_config_dir" in text:
                offenders.append(str(py_file.relative_to(REPO_ROOT)))
        assert not offenders, f"Files still reference the deleted _legacy_config_dir: {offenders}"

    def test_config_module_still_imports_cleanly(self):
        """Deleting the function must not break the module's imports."""
        from voice_typer.server import config  # noqa: F401

        # Sanity: the module exposes the functions that DO have callers.
        assert hasattr(config, "_config_dir"), (
            "config._config_dir must still be importable (the dead-code removal "
            "only deleted the dead _legacy_config_dir, not _config_dir)"
        )
