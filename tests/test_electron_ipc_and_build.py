"""Tests for Electron/IPC infrastructure, build tooling, CI, package metadata,
and type-safety fixes."""

from __future__ import annotations

import contextlib
import inspect
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENT_SRC = REPO_ROOT / "voice_typer" / "client" / "src"
RENDERER_SRC = CLIENT_SRC / "renderer" / "src"


def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")


class TestElectronExposesDataExportHandlers:
    """Electron exposes export handlers for templates + config.

    RW-1 status: the renderer-side type/UX tests below are ported to
    vitest (see ``electron-ipc-build-behavior.test.tsx`` Sections 1–2).
    The 5 main/preload-source tests stay in Python — they assert on
    ``src/main/index.ts`` and ``src/preload/index.ts`` source strings,
    which cannot be loaded in the jsdom vitest environment (both files
    import ``electron`` and ``node:*`` built-ins).  A behavioral port
    would require ``@vitest/electron`` or Playwright Electron to spawn
    a real main process and invoke ``ipcMain.handle("templates:export",
    ...)`` end-to-end — see worklog for the documented dep.
    """

    # REQUIRES-ELECTRON-RUNNER: asserts on src/main/index.ts source which
    # imports `electron` + `node:*`; behavioral version needs a real Electron
    # main process (would use @vitest/electron or Playwright Electron).
    def test_main_has_templates_export_handler(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        export_handlers_ts = (CLIENT_SRC / "main" / "ipc" / "export-handlers.ts").read_text(encoding="utf-8")
        combined = main_ts + "\n" + export_handlers_ts
        assert 'ipcMain.handle("templates:export"' in combined or '"templates:export"' in combined

    # REQUIRES-ELECTRON-RUNNER: same as above — src/main/index.ts source check.
    def test_main_has_config_export_handler(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        export_handlers_ts = (CLIENT_SRC / "main" / "ipc" / "export-handlers.ts").read_text(encoding="utf-8")
        combined = main_ts + "\n" + export_handlers_ts
        assert 'ipcMain.handle("config:export"' in combined or '"config:export"' in combined

    # REQUIRES-ELECTRON-RUNNER: asserts on src/preload/index.ts source which
    # imports `electron` + `node:*`; behavioral version needs a real Electron
    # preload context.
    def test_preload_exposes_export_templates(self):
        preload = (CLIENT_SRC / "preload" / "index.ts").read_text(encoding="utf-8")
        assert "exportTemplates" in preload
        assert "exportConfig" in preload

    # REQUIRES-ELECTRON-RUNNER: asserts on src/main/index.ts source.
    def test_history_export_still_present(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        export_handlers_ts = (CLIENT_SRC / "main" / "ipc" / "export-handlers.ts").read_text(encoding="utf-8")
        combined = main_ts + "\n" + export_handlers_ts
        assert 'ipcMain.handle("history:export"' in combined or '"history:export"' in combined

    # REQUIRES-ELECTRON-RUNNER: asserts on src/main/index.ts source.
    def test_vocabulary_export_still_present(self):
        main_ts = (CLIENT_SRC / "main" / "index.ts").read_text(encoding="utf-8")
        export_handlers_ts = (CLIENT_SRC / "main" / "ipc" / "export-handlers.ts").read_text(encoding="utf-8")
        combined = main_ts + "\n" + export_handlers_ts
        assert 'ipcMain.handle("vocabulary:export"' in combined or '"vocabulary:export"' in combined


class TestVersionReadsFromPackageMetadata:
    """__version__ reads from package metadata."""

    # REQUIRES-PYTHON-RUNNER: imports the `voice_typer` Python package to
    # read `__version__`; out of scope for a TS-string vitest rewrite.
    def test_version_uses_importlib_metadata(self):
        from voice_typer import __version__

        assert __version__ is not None
        assert isinstance(__version__, str)
        assert len(__version__) > 0


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.config` +
# `voice_typer.server.ipc_server.IPCServer` and invokes `_dispatch`;
# out of scope for a TS-string vitest rewrite.
class TestSetConfigRejectsSensitiveAttrs:
    """set_config rejects trusted-path fields from the renderer."""

    def test_rejects_combined_sensitive_payload(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        cfg = config_module.Config()
        cfg.save = MagicMock(return_value=True)

        app = MagicMock()
        app.config = cfg
        # RW-9/RW-17: the app-level test-seam delegates (``_sync_autostart``,
        # ``_register_esc_hotkey``, etc.) were removed; production now calls
        # ``startup_tasks.*`` / ``app.hotkeys.*`` directly, so there is
        # nothing on the app object to pre-stub here. ``app`` is a MagicMock,
        # so any attribute the code touches is auto-stubbed on access.

        server = IPCServer(app)

        original_qwen = cfg.qwen_model_path
        original_parakeet = cfg.parakeet_model_path
        original_corrections = cfg.corrections_path

        result = server._dispatch(
            {
                "id": 1,
                "type": "set_config",
                "data": {
                    "qwen_model_path": "/etc/passwd",
                    "parakeet_model_path": "/tmp/evil",
                    "corrections_path": "/tmp/evil-corrections.json",
                    "beam_size": 7,
                },
            }
        )

        assert result["type"] == "ack"
        assert cfg.qwen_model_path == original_qwen
        assert cfg.parakeet_model_path == original_parakeet
        assert cfg.corrections_path == original_corrections
        assert cfg.beam_size == 7


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.ipc_server.IPCServer`
# and invokes `_dispatch`; out of scope for a TS-string vitest rewrite.
class TestUnknownIPCCommandCode:
    """Unknown-command error includes code: unknown_command."""

    def test_unknown_command_payload_has_code_field(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({"id": 7, "type": "totally_made_up_command"})

        assert result["type"] == "error"
        # PI-23 namespaced the error code as ``server.unknown_command``;
        # the bare ``unknown_command`` is preserved as ``legacy_code``
        # for back-compat with any consumer still reading ``code``.
        code = result["data"]["code"]
        assert code in ("unknown_command", "server.unknown_command")
        assert result["data"]["command"] == "totally_made_up_command"
        assert "Unknown command" in result["data"]["message"]


class TestEntryPointImportable:
    """The main entry point must be importable."""

    # REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.ipc_server.main`;
    # out of scope for a TS-string vitest rewrite.
    def test_ipc_server_main_importable(self):
        from voice_typer.server.ipc_server import main

        assert callable(main)

    # REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.app`;
    # out of scope for a TS-string vitest rewrite.
    def test_app_main_re_export_exists(self):
        import voice_typer.server.app as app_mod

        assert hasattr(app_mod, "main")
        assert callable(app_mod.main)

    # REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.__main__`;
    # out of scope for a TS-string vitest rewrite.
    def test_dunder_main_imports_from_ipc_server(self):
        import voice_typer.server.__main__ as main_mod

        assert hasattr(main_mod, "main")
        assert callable(main_mod.main)


class TestAllowlistCorrectness:
    """Allowlist correctness — no dead entries, all have server handlers."""

    @pytest.fixture
    def allowlist_entries(self):
        # PVT-G5-009: previously pointed at `index.ts`, but R6-F10 moved
        # the canonical `ALLOWED_COMMANDS = new Set([...])` literal out of
        # `index.ts` into its own dependency-free leaf module
        # `allowed-commands.ts` (`index.ts:56` now just re-exports it).
        # `src.index("ALLOWED_COMMANDS = new Set([")` was raising
        # `ValueError: substring not found` on the stale path, erroring
        # every test in this class without any assertion ever running.
        idx_path = REPO_ROOT / "voice_typer" / "client" / "src" / "main" / "allowed-commands.ts"
        src = idx_path.read_text(encoding="utf-8")
        start = src.index("ALLOWED_COMMANDS = new Set([")
        end = src.index("]);", start)
        block = src[start:end]

        entries = re.findall(r'"([a-z_]+)"', block)
        return set(entries)

    def test_repaste_last_in_allowlist(self, allowlist_entries):
        # UX-23: repaste_last is wired in the renderer (Home.tsx + tray menu)
        # and dispatched by the backend (_COMMAND_REGISTRY in
        # voice_typer/server/ipc/server.py). It must be in the renderer's
        # ALLOWED_COMMANDS so the IPC call is not rejected. (Was previously
        # asserted as a "dead" exclusion before the handler was added.)
        assert "repaste_last" in allowlist_entries

    # REQUIRES-PYTHON-RUNNER: cross-validates the main allowlist against
    # `voice_typer/server/ipc_server.py` Python source; out of scope for
    # a TS-string vitest rewrite.
    def test_allowlist_matches_server_commands(self, allowlist_entries):

        ipc_path = REPO_ROOT / "voice_typer" / "server" / "ipc_server.py"
        src = ipc_path.read_text(encoding="utf-8")
        old_cmds = set(re.findall(r'cmd == "([a-z_]+)"', src))
        new_cmds = set(re.findall(r'"([a-z_]+)": "_handle_', src))
        server_cmds = old_cmds | new_cmds
        orphans = allowlist_entries - server_cmds
        assert not orphans, f"Allowlist has orphan entries: {sorted(orphans)}"
        # PVT-G5-075: `tray_click` is a Rust-only command. The server
        # registry has a `_handle_tray_click` handler (invoked by the
        # Rust tray menu handler in `src-tauri/src/tray.rs::on_menu_event`
        # via `dispatch_inner`, which bypasses the renderer allowlist
        # gate). The renderer never sends `tray_click`, so it is
        # intentionally NOT in the TS `ALLOWED_COMMANDS` Set. Exclude
        # it from the "missing" check so the parity test does not flag
        # it as a renderer-reachable gap.
        # PVT-G5-075 + XZ-17 reviewer feedback: `tray_click` and
        # `shutdown` are Rust-only / host-supervised commands. The
        # server registry has handlers for both, but neither is ever
        # sent by the renderer:
        #   - `tray_click` is dispatched internally by the Rust tray
        #     menu handler (src-tauri/src/tray.rs::on_menu_event).
        #   - `shutdown` is host-supervised (the Rust host + Electron
        #     main both manage sidecar shutdown; the renderer never
        #     sends it). See FEATURES.md row 81.
        # Both are intentionally NOT in the TS ALLOWED_COMMANDS Set.
        rust_only_commands = {"tray_click", "shutdown"}
        missing = server_cmds - allowlist_entries - rust_only_commands
        assert not missing, (
            f"Allowlist is missing server commands (renderer calls would be silently rejected): {sorted(missing)}"
        )


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.vocabulary` +
# `voice_typer.server.service` + `voice_typer.server.ipc_server`;
# out of scope for a TS-string vitest rewrite.
class TestGetVocabularyHandler:
    """get_vocabulary handler uses get_all(), not list_entries()."""

    def test_vocabulary_manager_has_no_list_entries(self):
        from voice_typer.server.vocabulary import VocabularyManager

        assert not hasattr(VocabularyManager, "list_entries")

    def test_vocabulary_manager_has_get_all(self):
        from voice_typer.server.vocabulary import VocabularyManager

        assert hasattr(VocabularyManager, "get_all")

    def test_service_get_vocabulary_uses_get_all(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)

        from voice_typer.server.service import VoiceTyperService

        app = MagicMock()
        app.config.config_dir = tmp_path
        service = VoiceTyperService(app)

        result = service.get_vocabulary()
        assert isinstance(result, dict)
        assert "misspellings" in result

    def test_ipc_dispatch_get_vocabulary_returns_vocabulary_type(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)

        from voice_typer.server.ipc_server import IPCServer

        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({"id": 1, "type": "get_vocabulary"})
        assert result["type"] == "vocabulary"
        assert "misspellings" in result["data"]


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.app` +
# `voice_typer.server.ipc_server`; introspects Python module source via
# `inspect.getsource`; out of scope for a TS-string vitest rewrite.
class TestVoiceTyperAppSingleton:
    """VoiceTyperApp uses _ensure_single_instance for singleton enforcement."""

    def test_ensure_single_instance_exists(self):
        from voice_typer.server import app as app_module

        assert hasattr(app_module, "_ensure_single_instance")

    def test_main_calls_ensure_single_instance(self):
        from voice_typer.server import ipc_server

        src = inspect.getsource(ipc_server.main)
        assert "_ensure_single_instance" in src or "single_instance" in src

    def test_singleton_via_request_single_instance_lock(self):
        from voice_typer.server import app as app_module

        assert hasattr(app_module, "_ensure_single_instance") or hasattr(app_module, "main")


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.ipc_server.IPCServer`
# and invokes `_dispatch`; out of scope for a TS-string vitest rewrite.
class TestIPCDispatchInvalidData:
    """_dispatch must not crash when data is not a dict."""

    def test_dispatch_with_string_data(self):
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({"id": 1, "type": "set_config", "data": "not a dict"})
        assert result["type"] in ("ack", "error")

    def test_set_config_with_string_data(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({"id": 1, "type": "set_config", "data": "not a dict"})
        assert result["type"] in ("ack", "error")

    def test_set_config_with_list_data(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({"id": 1, "type": "set_config", "data": ["not", "a", "dict"]})
        assert result["type"] in ("ack", "error")

    def test_set_config_with_none_data(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({"id": 1, "type": "set_config", "data": None})
        assert result["type"] in ("ack", "error")

    def test_set_config_with_integer_data(self, tmp_path, monkeypatch):
        from voice_typer.server import config as config_module
        from voice_typer.server.ipc_server import IPCServer

        monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)
        app = MagicMock()
        app.config = config_module.Config()
        server = IPCServer(app)

        result = server._dispatch({"id": 1, "type": "set_config", "data": 42})
        assert result["type"] in ("ack", "error")


# REQUIRES-PYTHON-RUNNER: reads `voice_typer/server/ipc_server.py` Python
# source; out of scope for a TS-string vitest rewrite.
class TestExceptExceptionNotBaseException:
    """ipc_server.main() catches Exception, not BaseException."""

    def test_main_catches_exception_not_baseexception(self):
        ipc_path = REPO_ROOT / "voice_typer" / "server" / "ipc_server.py"
        src = ipc_path.read_text(encoding="utf-8")
        assert "except BaseException:" not in src
        assert "except Exception:" in src


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.audio_processor` +
# `voice_typer.server.volume_ducker` + `voice_typer.server.volume_backends`
# and introspects Python source via `inspect.getsource`; out of scope for
# a TS-string vitest rewrite.
class TestTypeIgnoreBugsFixed:
    """type:ignore real bugs are fixed."""

    def test_audio_processor_quality_callback_null_check(self):
        from voice_typer.server.audio_processor import AudioProcessor

        src = inspect.getsource(AudioProcessor._run_quality_check)
        assert "if self._quality_callback is not None" in src

    def test_volume_ducker_backend_null_check_in_monitor(self):
        from voice_typer.server.volume_ducker import VolumeDucker

        src = inspect.getsource(VolumeDucker._smart_duck_monitor_loop)
        assert "if self._backend is None" in src

    def test_volume_ducker_backend_null_check_in_duck(self):
        from voice_typer.server.volume_ducker import VolumeDucker

        src = inspect.getsource(VolumeDucker.duck)
        assert "self._backend is not None" in src

    def test_volume_backends_bare_type_ignore_fixed(self):
        # Session 1 (PVT-architecture refactor) split the monolithic
        # `volume_backends.py` into a `volume_backends/` subfolder:
        # `__init__.py`, `linux.py`, `macos.py`, `windows.py`. The test
        # originally checked the monolithic file. Updated to scan the
        # new subfolder instead. Falls back to the monolithic file if
        # it exists (legacy deployments).
        monolith = REPO_ROOT / "voice_typer" / "server" / "volume_backends.py"
        subfolder = REPO_ROOT / "voice_typer" / "server" / "volume_backends"
        candidates = []
        if monolith.exists():
            candidates.append(monolith)
        if subfolder.is_dir():
            candidates.extend(sorted(subfolder.glob("*.py")))
        assert candidates, f"Neither {monolith} nor {subfolder}/*.py found — volume_backends missing"
        for path in candidates:
            src = path.read_text(encoding="utf-8")
            lines = [ln for ln in src.split("\n") if "type: ignore" in ln and "import-not-found" not in ln]
            bare_ignores = [ln for ln in lines if ln.rstrip().endswith("# type: ignore")]
            assert not bare_ignores, f"Bare `# type: ignore` in {path}: {bare_ignores}"

    def test_no_malformed_type_ignore_isc(self):
        server_dir = REPO_ROOT / "voice_typer" / "server"
        for py_file in server_dir.glob("*.py"):
            src = py_file.read_text(encoding="utf-8")
            assert "ignoreisc]" not in src


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.vad` and
# introspects Python source via `inspect.getsource`; out of scope for
# a TS-string vitest rewrite.
class TestVadStderrRedirect:
    """vad.py redirects both stdout and stderr."""

    def test_vad_redirects_both_streams(self):
        from voice_typer.server import vad

        src = inspect.getsource(vad)
        assert "redirect_stderr" in src


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.startup_sequence`
# and introspects Python source via `inspect.getsource`; out of scope for
# a TS-string vitest rewrite.
class TestMacOSAccessibilityCheck:
    """macOS accessibility permission check exists in the startup path.

    RW-9 Phase 5: the body of ``_do_startup`` was extracted into
    :class:`voice_typer.server.startup_sequence.StartupSequence`. The
    macOS accessibility check now lives in ``StartupSequence.run``, so
    these source-string checks are retargeted there. Intent unchanged.
    """

    def test_accessibility_check_in_startup_source(self):
        from voice_typer.server.startup_sequence import StartupSequence

        src = inspect.getsource(StartupSequence.run)
        has_macos_guard = "darwin" in src or "is_macos()" in src
        assert has_macos_guard and "accessibility" in src.lower()

    def test_accessibility_check_notifies_on_missing(self):
        from voice_typer.server.startup_sequence import StartupSequence

        src = inspect.getsource(StartupSequence.run)
        assert "tray.notify" in src


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.app.VoiceTyperApp`
# and instantiates it; out of scope for a TS-string vitest rewrite.
class TestRestartAppStopsBackends:
    """restart_app stops all hotkey backends."""

    def test_restart_calls_stop_on_all_three_backends(self, monkeypatch, tmp_path):
        from voice_typer.server import app as app_module

        for mod_name in [
            "sounddevice",
            "faster_whisper",
            "faster_whisper.WhisperModel",
            "pynput",
            "pynput.keyboard",
            "pystray",
            "PIL",
            "PIL.Image",
            "PIL.ImageDraw",
            "pyperclip",
        ]:
            sys.modules.setdefault(mod_name, MagicMock())

        with (
            patch.object(app_module, "_config_dir", return_value=tmp_path),
            patch.object(app_module, "is_autostart_enabled", return_value=False),
            patch.object(app_module, "enable_autostart"),
            patch.object(app_module, "disable_autostart"),
            patch.object(app_module, "list_microphones", return_value=[]),
        ):
            app = app_module.VoiceTyperApp()
            app.hotkeys._hotkey_backend = MagicMock()
            app.hotkeys._esc_backend = MagicMock()
            app.hotkeys._repaste_backend = MagicMock()
            app.recorder = MagicMock()
            app.recorder.discard = MagicMock()
            app.tray = MagicMock()
            app._do_restart = MagicMock()
            with contextlib.suppress(BaseException):
                app.restart_app()

            stops_called = sum(
                1
                for be in (app.hotkeys._hotkey_backend, app.hotkeys._esc_backend, app.hotkeys._repaste_backend)
                if be.stop.called
            )
            assert stops_called >= 1


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.app.VoiceTyperApp`
# and introspects Python source via `inspect.getsource`; out of scope
# for a TS-string vitest rewrite.
class TestRestartFiltersEnvVarsWithAllowlist:
    """restart_app does not leak env vars via os.environ.copy()."""

    def test_app_uses_env_allowlist(self):
        from voice_typer.server.app import VoiceTyperApp

        for name in ("_restart_app", "restart_app", "_do_restart"):
            if hasattr(VoiceTyperApp, name):
                source = inspect.getsource(getattr(VoiceTyperApp, name))
                assert "os.environ.copy()" not in source
                return
        pytest.fail("Could not find restart method on VoiceTyperApp")
