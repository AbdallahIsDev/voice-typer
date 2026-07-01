"""Round 9 E2E tests — verify the #2 extractions and STARTUP-3/5/7 fixes.

Covers:
- #2: ModelManager / RecordingController / HotkeyDispatcher extracted from app.py
- STARTUP-3: prewarm import filtering by active backend
- STARTUP-5: POSIX prewarm scheduler (macOS LaunchAgent + Linux systemd)
- STARTUP-7: Windows autostart uses Task Scheduler logon trigger (with Run-key fallback)
"""
import sys
import os
import json
import tempfile
import inspect
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/home/z/my-project/voice-typer-repo')


@pytest.fixture
def temp_config(tmp_path, monkeypatch):
    """Point voice_typer config to a temp dir."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    return tmp_path


class TestIssue2Extractions:
    """#2: ModelManager / RecordingController / HotkeyDispatcher extracted."""

    def test_model_manager_module_exists(self):
        """ModelManager module exists and is importable."""
        from voice_typer.server import model_manager
        assert hasattr(model_manager, "ModelManager")
        assert callable(model_manager.ModelManager)

    def test_recording_controller_module_exists(self):
        """RecordingController module exists and is importable."""
        from voice_typer.server import recording_controller
        assert hasattr(recording_controller, "RecordingController")
        assert callable(recording_controller.RecordingController)

    def test_hotkey_dispatcher_module_exists(self):
        """HotkeyDispatcher module exists and is importable."""
        from voice_typer.server import hotkey_dispatcher
        assert hasattr(hotkey_dispatcher, "HotkeyDispatcher")
        assert callable(hotkey_dispatcher.HotkeyDispatcher)

    def test_app_py_uses_model_manager(self):
        """app.py source references self.models (ModelManager instance)."""
        from voice_typer.server import app
        src = inspect.getsource(app)
        assert "self.models" in src, "app.py must use self.models (ModelManager)"
        assert "ModelManager" in src

    def test_app_py_uses_recording_controller(self):
        """app.py source references self.recording (RecordingController instance)."""
        from voice_typer.server import app
        src = inspect.getsource(app)
        assert "self.recording" in src, "app.py must use self.recording (RecordingController)"
        assert "RecordingController" in src

    def test_app_py_uses_hotkey_dispatcher(self):
        """app.py source references self.hotkeys (HotkeyDispatcher instance)."""
        from voice_typer.server import app
        src = inspect.getsource(app)
        assert "self.hotkeys" in src, "app.py must use self.hotkeys (HotkeyDispatcher)"
        assert "HotkeyDispatcher" in src

    def test_app_py_size_reduced(self):
        """app.py must stay at a manageable size. Security and platform fixes
        added essential code (DACL, restart token, signal handlers, RDP, etc.)."""
        from voice_typer.server import app as app_module
        src = inspect.getsource(app_module)
        line_count = src.count("\n")
        assert line_count < 2600, (
            f"app.py is {line_count} lines; expected < 2600 after security/platform fixes"
        )

    def test_model_manager_has_lifecycle_methods(self):
        """ModelManager exposes the expected lifecycle methods."""
        from voice_typer.server.model_manager import ModelManager
        for method in (
            "load_background", "start_background_load", "fallback_to_whisper",
            "try_load", "change_model", "active_transcriber",
            "ensure_active_engine_loaded",
        ):
            assert hasattr(ModelManager, method), f"ModelManager must have {method}"

    def test_recording_controller_has_lifecycle_methods(self):
        """RecordingController exposes the expected lifecycle methods."""
        from voice_typer.server.recording_controller import RecordingController
        for method in (
            "toggle", "start", "stop", "cancel",
            "on_recorder_rms", "on_silence_warning", "on_silence_auto_stop",
            "on_max_duration_auto_stop", "on_xrun_threshold",
            "_start_streaming_session_if_enabled", "_cancel_streaming_session",
            "_force_recover_from_stuck_transcription",
        ):
            assert hasattr(RecordingController, method), (
                f"RecordingController must have {method}"
            )

    def test_hotkey_dispatcher_has_lifecycle_methods(self):
        """HotkeyDispatcher exposes the expected lifecycle methods."""
        from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher
        for method in (
            "register", "register_esc", "unregister_esc",
            "register_repaste", "restart", "stop_all",
        ):
            assert hasattr(HotkeyDispatcher, method), (
                f"HotkeyDispatcher must have {method}"
            )

    def test_app_property_delegates_work(self, temp_config, monkeypatch):
        """The @property delegates (transcriber, _hotkey_backend, etc.)
        read/write through to the extracted controllers."""
        monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])
        from voice_typer.server.app import VoiceTyperApp
        app = VoiceTyperApp()
        # transcriber property delegate
        assert app.transcriber is None
        app.transcriber = MagicMock()
        assert app.transcriber is app.models.transcriber
        # _hotkey_backend property delegate
        assert app._hotkey_backend is app.hotkeys._hotkey_backend
        # _streaming_session property delegate
        assert app._streaming_session is app.recording._streaming_session


class TestStartup3ImportFiltering:
    """STARTUP-3: prewarm import filtering by active backend."""

    def test_warm_imports_skips_torch_for_whisper(self, temp_config, monkeypatch):
        """When asr_backend=whisper, _warm_imports must NOT import torch/transformers."""
        # Write a config with whisper backend
        (temp_config / "config.json").write_text(json.dumps({"asr_backend": "whisper"}))
        # Track which modules get imported
        imported = []
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def tracking_import(name, *args, **kwargs):
            if name in ("torch", "transformers"):
                imported.append(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", tracking_import)
        from voice_typer.server import prewarm
        # Mock _lower_io_priority to skip the platform check
        monkeypatch.setattr(prewarm, "_lower_io_priority", lambda: None)
        # Mock faster_whisper import (it might not be installed in test env)
        with patch.dict(sys.modules, {"faster_whisper": MagicMock()}):
            prewarm._warm_imports()
        # torch and transformers must NOT have been imported
        assert "torch" not in imported, (
            "STARTUP-3 regression: torch was imported for whisper backend "
            "(should be skipped to save ~400s on cold boot)"
        )
        assert "transformers" not in imported, (
            "STARTUP-3 regression: transformers was imported for whisper backend"
        )

    def test_warm_imports_imports_torch_for_parakeet(self, temp_config, monkeypatch):
        """When asr_backend=parakeet, _warm_imports MUST import torch + transformers."""
        (temp_config / "config.json").write_text(json.dumps({"asr_backend": "parakeet"}))
        imported = []
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def tracking_import(name, *args, **kwargs):
            if name in ("torch", "transformers", "faster_whisper"):
                imported.append(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", tracking_import)
        from voice_typer.server import prewarm
        monkeypatch.setattr(prewarm, "_lower_io_priority", lambda: None)
        # Mock the heavy modules so they don't actually load
        fake_modules = {
            "torch": MagicMock(),
            "transformers": MagicMock(),
            "faster_whisper": MagicMock(),
        }
        with patch.dict(sys.modules, fake_modules):
            prewarm._warm_imports()
        assert "torch" in imported, "parakeet backend must import torch"
        assert "transformers" in imported, "parakeet backend must import transformers"


class TestStartup5PrewarmPosix:
    """STARTUP-5: POSIX prewarm scheduler (macOS LaunchAgent + Linux systemd)."""

    def test_posix_scheduler_module_exists(self):
        """prewarm_scheduler_posix module exists and is importable."""
        from voice_typer.server import prewarm_scheduler_posix
        assert hasattr(prewarm_scheduler_posix, "is_supported")
        assert hasattr(prewarm_scheduler_posix, "is_prewarm_registered")
        assert hasattr(prewarm_scheduler_posix, "register_prewarm_task")
        assert hasattr(prewarm_scheduler_posix, "unregister_prewarm_task")

    def test_posix_scheduler_macos_plist_builder(self):
        """_build_macos_plist produces valid plist XML."""
        from voice_typer.server import prewarm_scheduler_posix
        plist = prewarm_scheduler_posix._build_macos_plist()
        assert "<?xml" in plist
        assert "<plist" in plist
        assert "com.voicetyper.prewarm" in plist
        assert "<key>RunAtLoad</key>" in plist
        assert "<true/>" in plist  # RunAtLoad=true
        assert "ProcessType" in plist
        assert "Background" in plist

    def test_posix_scheduler_linux_service_builder(self):
        """_build_linux_service produces a valid systemd unit."""
        from voice_typer.server import prewarm_scheduler_posix
        service = prewarm_scheduler_posix._build_linux_service()
        assert "[Unit]" in service
        assert "[Service]" in service
        assert "Type=oneshot" in service
        assert "ExecStart=" in service
        assert "IOSchedulingClass=idle" in service
        assert "Nice=10" in service

    def test_posix_scheduler_linux_timer_builder(self):
        """_build_linux_timer produces a valid systemd timer unit.

        PREWARM-001 (Issue 2): Linux is now boot-only — OnUnitActiveSec
        was removed so prewarm fires exactly once at boot, matching the
        Windows LogonTrigger-only design.  The previous 4h re-fire caused
        prewarm to run 5+ times per session; after the first run the OS
        file cache is already warm, so subsequent runs were pure wasted
        I/O (and under memory pressure actively harmful).
        """
        from voice_typer.server import prewarm_scheduler_posix
        timer = prewarm_scheduler_posix._build_linux_timer()
        assert "[Timer]" in timer
        assert "OnBootSec=10s" in timer
        assert "OnUnitActiveSec" not in timer, (
            "PREWARM-001 regression: OnUnitActiveSec is back, prewarm will "
            "fire repeatedly instead of once at boot"
        )
        assert "voice-typer-prewarm.service" in timer

    def test_task_scheduler_is_supported_returns_true_on_posix(self, monkeypatch):
        """task_scheduler.is_supported() returns True on macOS/Linux (STARTUP-5)."""
        from voice_typer.server import task_scheduler
        # Test Linux
        monkeypatch.setattr(task_scheduler.sys, "platform", "linux")
        assert task_scheduler.is_supported() is True
        # Test macOS
        monkeypatch.setattr(task_scheduler.sys, "platform", "darwin")
        assert task_scheduler.is_supported() is True

    def test_posix_scheduler_macos_registration_round_trip(self, monkeypatch, tmp_path):
        """LaunchAgent plist is written and removed correctly."""
        from voice_typer.server import prewarm_scheduler_posix
        fake_home = tmp_path
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(
            prewarm_scheduler_posix.subprocess, "run",
            lambda *a, **kw: MagicMock(returncode=0),
        )
        assert prewarm_scheduler_posix._register_prewarm_macos() is True
        assert prewarm_scheduler_posix._is_prewarm_registered_macos() is True
        plist_path = prewarm_scheduler_posix._macos_plist_path()
        assert plist_path.exists()
        assert prewarm_scheduler_posix._unregister_prewarm_macos() is True
        assert not plist_path.exists()

    def test_posix_scheduler_linux_registration_round_trip(self, monkeypatch, tmp_path):
        """systemd user timer units are written and removed correctly."""
        from voice_typer.server import prewarm_scheduler_posix
        monkeypatch.setattr(
            prewarm_scheduler_posix.os, "environ",
            {"XDG_CONFIG_HOME": str(tmp_path)},
        )
        monkeypatch.setattr(
            prewarm_scheduler_posix.subprocess, "run",
            lambda *a, **kw: MagicMock(returncode=0),
        )
        assert prewarm_scheduler_posix._register_prewarm_linux() is True
        assert prewarm_scheduler_posix._is_prewarm_registered_linux() is True
        service_path = prewarm_scheduler_posix._linux_service_path()
        timer_path = prewarm_scheduler_posix._linux_timer_path()
        assert service_path.exists()
        assert timer_path.exists()
        assert prewarm_scheduler_posix._unregister_prewarm_linux() is True
        assert not service_path.exists()
        assert not timer_path.exists()


class TestStartup7AppAutostartTaskScheduler:
    """STARTUP-7: Windows app autostart uses Task Scheduler logon trigger."""

    def test_app_autostart_task_xml_uses_pythonw_directly(self):
        """The app autostart XML uses pythonw.exe directly (no cmd.exe wrapper)."""
        # Force Windows mode for the XML builder
        with patch("sys.platform", "win32"):
            from voice_typer.server import platform as platform_mod
            # PLAT-VENV: _build_app_autostart_task_xml calls
            # _app_autostart_command_and_args which calls shutil.which.
            # On Linux, shutil.which with win32 platform check fails.
            # Patch the command builder to return known values.
            with patch.object(
                platform_mod,
                "_app_autostart_command_and_args",
                return_value=("pythonw.exe", "-m voice_typer.server.ipc_server --hidden --delay 30"),
            ):
                xml = platform_mod._build_app_autostart_task_xml()
        assert "cmd.exe" not in xml
        assert "<LogonTrigger>" in xml
        assert "PT0S" in xml  # fire at logon+0
        assert "--hidden" in xml
        assert "--delay" in xml  # prewarm head start

    def test_enable_autostart_windows_prefers_task_scheduler(self, monkeypatch):
        """_enable_autostart_windows tries Task Scheduler first."""
        from voice_typer.server import platform as platform_mod
        monkeypatch.setattr(platform_mod.sys, "platform", "win32")
        task_calls = []
        runkey_calls = []
        monkeypatch.setattr(platform_mod, "_register_app_autostart_task", lambda: task_calls.append(1) or True)
        monkeypatch.setattr(platform_mod, "_unregister_app_autostart_runkey", lambda: runkey_calls.append(1) or True)
        monkeypatch.setattr(platform_mod, "_register_app_autostart_runkey", lambda: runkey_calls.append(2) or True)
        assert platform_mod._enable_autostart_windows() is True
        assert task_calls == [1], "Task Scheduler registration must be tried first"
        # Run-key cleanup should be called (to remove stale entries)
        assert 1 in runkey_calls, "Stale Run-key entry should be cleaned up"

    def test_enable_autostart_windows_falls_back_to_runkey(self, monkeypatch):
        """_enable_autostart_windows falls back to Run key if Task Scheduler fails."""
        from voice_typer.server import platform as platform_mod
        monkeypatch.setattr(platform_mod.sys, "platform", "win32")
        monkeypatch.setattr(platform_mod, "_register_app_autostart_task", lambda: False)
        monkeypatch.setattr(platform_mod, "_unregister_app_autostart_runkey", lambda: True)
        runkey_called = []
        monkeypatch.setattr(platform_mod, "_register_app_autostart_runkey", lambda: runkey_called.append(1) or True)
        assert platform_mod._enable_autostart_windows() is True
        assert runkey_called == [1], "Must fall back to Run key when Task Scheduler fails"

    def test_disable_autostart_windows_removes_both(self, monkeypatch):
        """_disable_autostart_windows removes from both Task Scheduler and Run key."""
        from voice_typer.server import platform as platform_mod
        task_removed = []
        runkey_removed = []
        monkeypatch.setattr(platform_mod, "_unregister_app_autostart_task", lambda: task_removed.append(1) or True)
        monkeypatch.setattr(platform_mod, "_unregister_app_autostart_runkey", lambda: runkey_removed.append(1) or True)
        assert platform_mod._disable_autostart_windows() is True
        assert task_removed == [1]
        assert runkey_removed == [1]

    def test_is_autostart_windows_checks_both(self, monkeypatch):
        """_is_autostart_windows returns True if EITHER mechanism is registered."""
        from voice_typer.server import platform as platform_mod
        # Only Task Scheduler
        monkeypatch.setattr(platform_mod, "_is_app_autostart_task_registered", lambda: True)
        monkeypatch.setattr(platform_mod, "_is_app_autostart_runkey_registered", lambda: False)
        assert platform_mod._is_autostart_windows() is True
        # Only Run key
        monkeypatch.setattr(platform_mod, "_is_app_autostart_task_registered", lambda: False)
        monkeypatch.setattr(platform_mod, "_is_app_autostart_runkey_registered", lambda: True)
        assert platform_mod._is_autostart_windows() is True
        # Neither
        monkeypatch.setattr(platform_mod, "_is_app_autostart_task_registered", lambda: False)
        monkeypatch.setattr(platform_mod, "_is_app_autostart_runkey_registered", lambda: False)
        assert platform_mod._is_autostart_windows() is False
