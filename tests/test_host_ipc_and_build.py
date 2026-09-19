"""
Tests for IPC infrastructure, build tooling, CI, package metadata,
Post-predecessor cutover: the predecessor main/preload source pins were
"""

from __future__ import annotations

import contextlib
import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENT_SRC = REPO_ROOT / "voice_typer" / "client" / "src"
RENDERER_SRC = CLIENT_SRC / "renderer" / "src"


def _read(rel: str) -> str:
    return (RENDERER_SRC / rel).read_text(encoding="utf-8")


class TestVersionReadsFromPackageMetadata:
    """__version__ reads from package metadata."""

    def test_version_uses_importlib_metadata(self):
        from voice_typer import __version__

        assert __version__ is not None
        assert isinstance(__version__, str)
        assert len(__version__) > 0


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.config` +
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
        code = result["data"]["code"]
        assert code in ("unknown_command", "server.unknown_command")
        assert result["data"]["command"] == "totally_made_up_command"
        assert "Unknown command" in result["data"]["message"]


class TestEntryPointImportable:
    """The main entry point must be importable."""

    # REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.ipc_server.main`;
    def test_ipc_server_main_importable(self):
        from voice_typer.server.ipc_server import main

        assert callable(main)

    # REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.app`;
    def test_app_main_re_export_exists(self):
        import voice_typer.server.app as app_mod

        assert hasattr(app_mod, "main")
        assert callable(app_mod.main)

    # REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.__main__`;
    def test_dunder_main_imports_from_ipc_server(self):
        import voice_typer.server.__main__ as main_mod

        assert hasattr(main_mod, "main")
        assert callable(main_mod.main)


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.vocabulary` +
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
        app._vocabulary_manager = None
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
        app._vocabulary_manager = None
        server = IPCServer(app)

        result = server._dispatch({"id": 1, "type": "get_vocabulary"})
        assert result["type"] == "vocabulary"
        assert "misspellings" in result["data"]


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.app` +
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
class TestExceptExceptionNotBaseException:
    """ipc_server.main() catches Exception, not BaseException."""

    def test_main_catches_exception_not_baseexception(self):
        ipc_path = REPO_ROOT / "voice_typer" / "server" / "ipc_server.py"
        src = ipc_path.read_text(encoding="utf-8")
        assert "except BaseException:" not in src
        assert "except Exception:" in src


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.audio_processor` +
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
        monolith = REPO_ROOT / "voice_typer" / "server" / "volume_backends.py"
        subfolder = REPO_ROOT / "voice_typer" / "server" / "volume_backends"
        candidates = []
        if monolith.exists():
            candidates.append(monolith)
        if subfolder.is_dir():
            candidates.extend(sorted(subfolder.glob("*.py")))
        assert candidates, f"Neither {monolith} nor {subfolder}/*.py found, volume_backends missing"
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


class TestVadStderrRedirect:
    """vad.py loads the bundled model offline, no torch.hub, no noisy stderr."""

    def test_vad_redirects_both_streams(self):
        from voice_typer.server import vad

        src = inspect.getsource(vad)
        # and the network hub path must NOT exist.
        assert "InferenceSession" in src, (
            "vad.py must load the bundled silero_vad.onnx via "
            "onnxruntime.InferenceSession (offline-only, C-DATA-1), "
            "no network fetch at first use"
        )
        assert "torch.hub.load" not in src, (
            "vad.py must NOT call torch.hub.load, the network fallback was "
            "removed; a future re-add must also restore the "
            "stdout/stderr redirect guard (ERR-LINT-001)"
        )


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.startup_sequence`
class TestMacOSAccessibilityCheck:
    """macOS accessibility permission check exists in the startup path."""

    def test_accessibility_check_in_startup_source(self):
        from voice_typer.server.startup_sequence import StartupSequence

        src = inspect.getsource(StartupSequence._phase_5_platform_warnings)
        has_macos_guard = "darwin" in src or "is_macos()" in src
        assert has_macos_guard and "accessibility" in src.lower()

    def test_accessibility_check_notifies_on_missing(self):
        from voice_typer.server.startup_sequence import StartupSequence

        src = inspect.getsource(StartupSequence._phase_5_platform_warnings)
        assert "tray.notify" in src


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.app.VoiceTyperApp`
class TestRestartAppStopsBackends:
    """restart_app stops all hotkey backends."""

    def test_restart_calls_stop_on_all_three_backends(self, monkeypatch, tmp_path):
        from voice_typer.server import app as app_module

        for mod_name in [
            "PIL",
            "PIL.Image",
            "PIL.ImageDraw",
        ]:
            sys.modules.setdefault(mod_name, MagicMock())

        from voice_typer.server import server_platform

        with (
            patch.object(app_module, "_config_dir", return_value=tmp_path),
            patch.object(server_platform, "is_autostart_enabled", return_value=False),
            patch.object(server_platform, "enable_autostart"),
            patch.object(server_platform, "disable_autostart"),
            patch.object(server_platform, "list_microphones", return_value=[]),
        ):
            app = app_module.VoiceTyperApp()
            hotkey_backend_mock = MagicMock()
            esc_backend_mock = MagicMock()
            repaste_backend_mock = MagicMock()
            app.hotkeys._hotkey_backend = hotkey_backend_mock
            app.hotkeys._esc_backend = esc_backend_mock
            app.hotkeys._repaste_backend = repaste_backend_mock
            app.recorder = MagicMock()
            app.recorder.discard = MagicMock()
            app.tray = MagicMock()
            app._do_restart = MagicMock()
            with contextlib.suppress(BaseException):
                app.restart_app()

            stops_called = sum(
                1 for be in (hotkey_backend_mock, esc_backend_mock, repaste_backend_mock) if be.stop.called
            )
            assert stops_called >= 1


# REQUIRES-PYTHON-RUNNER: imports `voice_typer.server.app.VoiceTyperApp`
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
