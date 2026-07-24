"""Consolidated regression tests for the NEW-DEAD-xxx series (dead-code removal).

Merges:
- tests/test_new_dead_002_scripts.py
- tests/test_new_dead_003_font_svg.py
- tests/test_new_dead_009_diagnose.py
- tests/test_new_dead_010_ptt_wiring.py
- tests/test_new_dead_015_llm_test_connection.py
"""

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
   ``voice_typer.transcription``, and ``voice_typer.tray`` — all of
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
        """The script must import from ``voice_typer.server.*`` not the
        legacy top-level ``voice_typer.*`` paths.
        """
        script_path = SCRIPTS_DIR / "runtime_proof.py"
        source = script_path.read_text()
        # The legacy broken imports would say "from voice_typer.config",
        # "from voice_typer.transcription", "from voice_typer.tray".
        # The fix uses "from voice_typer.server.config", etc.
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
        """The runner must look for ``[TRANSCRIBE] Transcription complete``
        (the actual marker emitted by dictation_pipeline.py:98), not
        the legacy ``_busy reset to False``.
        """
        source = (SCRIPTS_DIR / "runtime_test_runner.py").read_text()
        assert "[TRANSCRIBE] Transcription complete" in source, (
            "runtime_test_runner.py must grep for '[TRANSCRIBE] Transcription complete' (current production marker)"
        )

    def test_does_not_rely_on_legacy_busy_reset_marker(self):
        """The legacy ``_busy reset to False`` marker is no longer
        emitted by the production code; the runner must not depend on
        it as the primary success signal.
        """
        source = (SCRIPTS_DIR / "runtime_test_runner.py").read_text()
        # The old wait_for_log call looked for "_busy reset to False".
        # The new code looks for "[TRANSCRIBE] Transcription complete".
        # We allow the legacy string to appear in comments/docstrings
        # but NOT as the argument to wait_for_log.
        # Easiest check: the wait_for_log call must not pass the legacy
        # string.
        assert 'wait_for_log(LOG_FILE, "_busy reset to False"' not in source, (
            "runtime_test_runner.py still uses the legacy _busy reset to False marker as a wait_for_log argument"
        )

    def test_force_recover_still_checked(self):
        """FORCE RECOVER is still emitted by recording_controller.py:623,
        so the runner should still check for it as a fallback signal.
        """
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
- The new Electron app uses ``@hugeicons/react`` from npm instead.
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
        """The ``[tool.setuptools.package-data]`` section must not
        reference fonts or icons."""
        content = PYPROJECT.read_text()
        # The section may exist for other purposes, but it must not
        # reference the dead font/icons.
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
    """Sanity check: no Python source file should reference the font
    (the issue said zero source files load it)."""

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
        """The HotkeyBackend base class must provide a default
        ``diagnose`` implementation that returns an empty string.
        """
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
        """A new subclass that doesn't override diagnose must be
        instantiable (with the other abstract methods implemented).
        """

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

Previously ``HotkeyBackend.set_on_release`` was half-wired — the
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
        """When recording_mode is 'push_to_talk', the dispatcher must
        call ``set_on_release(app._stop_dictation)``.
        """
        source = inspect.getsource(HotkeyDispatcher.register)
        assert "set_on_release" in source, "HotkeyDispatcher.register must call set_on_release for PTT mode"
        assert "push_to_talk" in source, "HotkeyDispatcher.register must check recording_mode == 'push_to_talk'"
        assert "_stop_dictation" in source, "HotkeyDispatcher.register must wire set_on_release to app._stop_dictation"

    def test_win32_backend_fires_on_release_on_key_up(self):
        """The Win32 polling backend must detect key-up transitions and
        fire ``_on_release_callback``.
        """
        source = inspect.getsource(hotkeys.WindowsNativeHotkey._run_polling_loop)
        assert "_on_release_callback" in source, (
            "WindowsNativeHotkey._run_polling_loop must reference _on_release_callback"
        )
        # The key-up detection logic: "not is_pressed and was_pressed".
        assert "not is_pressed and was_pressed" in source, (
            "WindowsNativeHotkey._run_polling_loop must detect key-up transitions"
        )

    def test_pynput_backend_fires_on_release(self):
        """The pynput backend must fire _on_release_callback in its
        on_release handler.
        """
        # Find the PynputHotkey class's on_release closure.
        source = inspect.getsource(hotkeys.PynputHotkey._start_fallback)
        assert "_on_release_callback" in source, "PynputHotkey._start_fallback must reference _on_release_callback"
        assert "on_release" in source, "PynputHotkey._start_fallback must register an on_release handler"

    def test_set_on_release_stores_callback(self):
        """``HotkeyBackend.set_on_release`` must store the callback in
        ``self._on_release_callback``.
        """
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
        """``set_on_release(None)`` must clear the callback (allowing
        toggle mode to override a previous PTT setting)."""
        backend = hotkeys.PynputHotkey.__new__(hotkeys.PynputHotkey)
        backend._on_release_callback = lambda: None

        backend.set_on_release(None)
        assert backend._on_release_callback is None


class TestPttFunctionalFlow:
    """Functional test: simulate the PTT wiring end-to-end."""

    def test_ptt_wiring_calls_stop_dictation_on_key_release(self):
        """When the dispatcher registers a hotkey in PTT mode, the
        backend's ``_on_release_callback`` must point to
        ``app._stop_dictation``.
        """
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

        # set_on_release must have been called with app._stop_dictation.
        fake_backend.set_on_release.assert_called_once_with(app._stop_dictation)

    def test_toggle_mode_does_not_set_on_release(self):
        """In toggle mode (not push_to_talk), set_on_release must NOT
        be called — the hotkey press toggles recording on/off.
        """
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

Previously ``LLMPolisher.test_connection()`` was dead — defined but
never invoked by any IPC route or UI button.  The fix:

1. Adds ``VoiceTyperService.test_llm_connection()`` that constructs an
   LLMPolisher from the live config and calls ``test_connection()``.
2. Adds an IPC route ``test_llm_connection`` that delegates to the
   service method.
3. Adds ``test_llm_connection`` to the renderer's IPC command
   allowlist so the Electron main process will forward it.
"""


class TestServiceTestMethod:
    """NEW-DEAD-015: VoiceTyperService must expose test_llm_connection()."""

    def test_service_has_test_llm_connection_method(self):
        from voice_typer.server.service import VoiceTyperService

        assert hasattr(VoiceTyperService, "test_llm_connection"), (
            "VoiceTyperService must have a test_llm_connection method "
            "so the renderer can test the LLM polish API connection"
        )

    def test_service_returns_failure_when_no_api_key(self, server_with_mock_app):
        """When the config has no llm_api_key, the service must return
        success=False with a helpful message."""
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
        """When the config has an API key, the service must construct an
        LLMPolisher and call its test_connection() method.
        """
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
    """NEW-DEAD-015: the IPC dispatcher must route test_llm_connection."""

    def test_ipc_dispatches_test_llm_connection(self, server_with_mock_app):
        """``_dispatch({'type': 'test_llm_connection'})`` must call
        ``service.test_llm_connection()`` and return the result.
        """
        srv = server_with_mock_app
        srv.service.test_llm_connection = MagicMock(return_value={"success": True, "message": "Connected"})

        result = srv._dispatch({"id": 1, "type": "test_llm_connection"})

        srv.service.test_llm_connection.assert_called_once()
        assert result["type"] == "test_llm_connection_result"
        assert result["data"] == {"success": True, "message": "Connected"}

    def test_ipc_handles_service_exception(self, server_with_mock_app):
        """When the service raises, the IPC route must return an error."""
        srv = server_with_mock_app
        srv.service.test_llm_connection = MagicMock(side_effect=RuntimeError("boom"))

        result = srv._dispatch({"id": 1, "type": "test_llm_connection"})

        assert result["type"] == "error"
        assert "boom" in result["data"]["message"]


class TestRendererAllowlist:
    """NEW-DEAD-015: the Electron main process allowlist must include
    test_llm_connection so the renderer can invoke it."""

    def test_allowlist_includes_test_llm_connection(self):
        from pathlib import Path

        # WR-14: allowlist moved from index.ts to allowed-commands.ts per CR-063.
        main_ts = (
            Path(__file__).resolve().parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "main"
            / "allowed-commands.ts"
        )
        source = main_ts.read_text(encoding="utf-8")
        assert '"test_llm_connection"' in source, (
            "Electron main process ALLOWED_COMMANDS must include 'test_llm_connection' so the renderer can invoke it"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# === Source: CR-1 — ipc/ subpackage dead-code removal ===

"""Regression tests for CR-1: ``voice_typer/server/ipc/`` subpackage is
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
    """CR-1: the four dead parallel-implementation modules must stay gone."""

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
            f"CR-1 regression: {rel_path} was deleted as dead-code parallel of "
            "ipc_server.py — do NOT re-create it. The canonical implementation "
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
        """The ``ipc`` package __init__ must NOT re-export ``IPCServer`` or
        ``main`` — those names live only in ``ipc_server.py`` (the shim
        that retains the full implementation).  Re-exporting them from
        ``ipc`` would re-create the parallel-system surface.
        """
        import voice_typer.server.ipc as ipc_pkg

        assert not hasattr(ipc_pkg, "IPCServer"), (
            "ipc/__init__.py must not re-export IPCServer — it lives in "
            "voice_typer.server.ipc_server. Re-exporting it re-creates the "
            "parallel-system surface that CR-1 removed."
        )
        assert not hasattr(ipc_pkg, "main"), (
            "ipc/__init__.py must not re-export main — it lives in "
            "voice_typer.server.ipc_server. Re-exporting it re-creates the "
            "parallel-system surface that CR-1 removed."
        )

    def test_ipc_init_does_not_re_export_push_event_now_or_process_meta(self):
        """The ``ipc`` package __init__ must NOT re-export
        ``_push_event_now`` or ``_set_process_metadata`` — they live in
        ``ipc_server.py`` and were previously re-exported from the (now
        deleted) ``ipc/push_events.py`` and ``ipc/process_meta.py``.
        """
        import voice_typer.server.ipc as ipc_pkg

        assert not hasattr(ipc_pkg, "_push_event_now"), (
            "ipc/__init__.py must not re-export _push_event_now — its "
            "source module ipc/push_events.py was deleted as dead code."
        )
        assert not hasattr(ipc_pkg, "_set_process_metadata"), (
            "ipc/__init__.py must not re-export _set_process_metadata — "
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
        """CR-2: ``ipc_server.py`` must import ``_TCPLineIO`` from
        ``voice_typer.server.ipc.transport`` (the canonical location with
        the deadlock fix in ``close``), not define a parallel copy.
        """
        import inspect

        from voice_typer.server import ipc_server
        from voice_typer.server.ipc import transport as ipc_transport

        # The class object identity must match — no parallel copy.
        assert ipc_server._TCPLineIO is ipc_transport._TCPLineIO, (
            "ipc_server._TCPLineIO must be the SAME class object as "
            "ipc.transport._TCPLineIO (single source of truth for the "
            "CR-2 close()-deadlock fix). A parallel copy would let the "
            "bugged close() resurface."
        )
        # The source file must be transport.py, not ipc_server.py.
        src_file = inspect.getsourcefile(ipc_server._TCPLineIO)
        assert src_file is not None and src_file.endswith("transport.py"), (
            f"_TCPLineIO source must be transport.py; got {src_file!r}."
        )

    def test_ipc_server_TCPLineIO_close_uses_shutdown(self):  # noqa: N802
        """CR-2: ``_TCPLineIO.close`` must call ``shutdown(SHUT_RDWR)``
        BEFORE ``close()`` so an in-progress ``recv`` on another thread
        is interrupted and the ``BufferedReader.close()`` doesn't
        deadlock.
        """
        import inspect

        from voice_typer.server.ipc_server import _TCPLineIO

        src = inspect.getsource(_TCPLineIO.close)
        assert "shutdown" in src, (
            "_TCPLineIO.close must call self.conn.shutdown(SHUT_RDWR) to "
            "interrupt in-progress reads (CR-2 deadlock fix)."
        )
        assert "SHUT_RDWR" in src, (
            "_TCPLineIO.close must use socket.SHUT_RDWR (full duplex shutdown) to interrupt both reads and writes."
        )
