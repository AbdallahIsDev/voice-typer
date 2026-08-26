"""E2E tests — verify the #2 extractions and STARTUP-3/7 fixes.

Covers:
- #2: ModelManager / RecordingController / HotkeyDispatcher extracted from app.py
- STARTUP-3: prewarm import filtering by active backend
- STARTUP-7: Windows autostart uses Task Scheduler logon trigger (with Run-key fallback)

(Wave 3, 2026-08-14): STARTUP-5 (POSIX prewarm scheduler) section
was deleted — prewarm became a worker startup phase (master plan
§6.2 P-1), so the macOS LaunchAgent + Linux systemd user-timer
scheduler (``prewarm_scheduler_posix.py``) and the POSIX True-return
branch of ``task_scheduler.is_supported()`` were removed. The 7
``TestPrewarmPosixSchedulerSupportsLaunchagentAndSystemd`` tests
were deleted in lockstep.
"""

import importlib
import importlib.util
import inspect
import json
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def temp_config(tmp_config_dir):
    """Point voice_typer config to a temp dir."""
    return tmp_config_dir


class TestCoreModulesExtractedFromApp:
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
        assert line_count < 2600, f"app.py is {line_count} lines; expected < 2600 after security/platform fixes"

    def test_model_manager_has_lifecycle_methods(self):
        """ModelManager exposes the expected lifecycle methods."""
        from voice_typer.server.model_manager import ModelManager

        for method in (
            "load_background",
            "start_background_load",
            "fallback_to_whisper",
            "try_load",
            "change_model",
            "active_transcriber",
            "ensure_active_engine_loaded",
        ):
            assert hasattr(ModelManager, method), f"ModelManager must have {method}"

    def test_recording_controller_has_lifecycle_methods(self):
        """RecordingController exposes the expected lifecycle methods."""
        from voice_typer.server.recording_controller import RecordingController

        for method in (
            "toggle",
            "start",
            "stop",
            "cancel",
            "on_recorder_rms",
            "on_silence_warning",
            "on_silence_auto_stop",
            "on_max_duration_auto_stop",
            "on_xrun_threshold",
            "_start_streaming_session_if_enabled",
            "_cancel_streaming_session",
            "_force_recover_from_stuck_transcription",
        ):
            assert hasattr(RecordingController, method), f"RecordingController must have {method}"

    def test_hotkey_dispatcher_has_lifecycle_methods(self):
        """HotkeyDispatcher exposes the expected lifecycle methods."""
        from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher

        for method in (
            "register",
            "register_esc",
            "unregister_esc",
            "register_repaste",
            "restart",
            "stop_all",
        ):
            assert hasattr(HotkeyDispatcher, method), f"HotkeyDispatcher must have {method}"

    def test_app_property_delegates_removed(self, temp_config, monkeypatch):
        """ARCH-REFAC-003: the @property delegates (transcriber,
        _hotkey_backend, _streaming_session, etc.) have been removed from
        VoiceTyperApp. Callers must access the extracted modules directly
        via ``app.models``, ``app.hotkeys``, ``app.recording``."""
        monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])
        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()
        # The legacy @property delegates must no longer exist on the app.
        for removed in (
            "transcriber",
            "_qwen_engine",
            "_parakeet_engine",
            "_asr_registry",
            "_model_load_thread",
            "_model_load_attempted",
            "_pending_dictation",
            "_transcription_thread",
            "_streaming_session",
            "_hotkey_backend",
            "_esc_backend",
            "_repaste_backend",
        ):
            assert not hasattr(type(app), removed), (
                f"ARCH-REFAC-003 regression: VoiceTyperApp still has a "
                f"class-level attribute {removed!r} (expected the "
                f"@property delegate to be removed)"
            )
        # The extracted modules expose the same state directly.
        assert app.models.transcriber is None
        assert app.hotkeys._hotkey_backend is None
        assert app.recording._streaming_session is None


class TestPrewarmFiltersImportsByActiveBackend:
    """STARTUP-3: worker warm-imports list (master plan §6.2 P-1).

    (Wave 3, 2026-08-14): the original tests pinned backend-specific
    filtering (whisper skipped torch/transformers; parakeet warmed
    them). Prewarm became a worker startup phase and the worker is
    TORCH-FREE (VAD is ONNX, Parakeet is onnx-asr) — the warm list is
    now the fixed ``_WORKER_WARM_PACKAGES`` tuple (``onnxruntime`` +
    ``ctranslate2`` + ``numpy`` + ``scipy`` + ``faster_whisper``)
    regardless of ``asr_backend``. ``torch`` and ``transformers`` are
    NEVER warmed (the worker exe doesn't ship them). The two tests
    below pin the new invariants:
      1. ``_warm_imports`` NEVER calls ``__import__("torch")`` /
         ``__import__("transformers")`` (would defeat the torch-free
         worker contract).
      2. ``_warm_imports`` warms every package in
         ``_WORKER_WARM_PACKAGES`` via ``_warm_package_files`` (no
         backend variation).
    """

    def test_warm_imports_never_imports_torch_or_transformers(self, temp_config, monkeypatch):
        """``_warm_imports`` must NOT call ``__import__("torch")`` or
        ``__import__("transformers")`` — the worker exe is torch-free
        (master plan §6.2 P-1 + Phase 1c ONNX migration).

        Pre-Phase-2 production did ``import torch`` (which executes
        ~5s of CPU + pulls in transformers). The new worker warms the
        torch-FREE runtime pack (``onnxruntime`` + ``ctranslate2`` +
        ``numpy`` + ``scipy`` + ``faster_whisper``) via
        ``_warm_package_files`` (which uses ``importlib.util.find_spec``
        to locate files and reads them into the OS page cache without
        executing the package's code). This test enforces the no-import
        invariant across both backends — a future regression that
        reintroduces ``import torch`` would break the torch-free worker
        bundle (the build's ``--nofollow-import-to=torch`` flag would
        silently drop it, but the runtime import would still execute).
        """
        # The warm list is backend-independent post-§6.2 P-1, but we
        # exercise both backends to guard against a future regression
        # that re-introduces backend-specific torch/transformers warming.
        from voice_typer.server import prewarm
        from voice_typer.server.prewarm import cache_probe

        # Capture the real ``__import__`` ONCE before the loop — the
        # ``tracking_import`` closure inside the loop references this
        # binding, so re-capturing it inside the loop would capture the
        # previous iteration's ``tracking_import`` (recursion).
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        for backend in ("whisper", "parakeet"):
            (temp_config / "config.json").write_text(json.dumps({"asr_backend": backend}))
            imported: list[str] = []

            # Bind ``imported`` as a default arg so the closure captures
            # the CURRENT iteration's list (B023: function definitions
            # inside loops don't bind loop variables by name — they
            # capture the variable itself, which would all refer to the
            # last iteration's list at call time without this binding).
            def tracking_import(name, *args, _imported=imported, **kwargs):
                if name in ("torch", "transformers"):
                    _imported.append(name)
                return real_import(name, *args, **kwargs)

            monkeypatch.setattr("builtins.__import__", tracking_import)

            # Mock every package in _WORKER_WARM_PACKAGES so
            # _warm_package_files's find_spec + iter_modules path doesn't
            # shell out to a missing package on a fresh dev env.
            fake_modules = {}
            for pkg_name in cache_probe._WORKER_WARM_PACKAGES:
                mock = MagicMock()
                mock.__spec__ = importlib.util.spec_from_loader(pkg_name, loader=None)
                fake_modules[pkg_name] = mock
            with patch.dict(sys.modules, fake_modules):
                prewarm._warm_imports()
            # torch and transformers must NOT have been imported (the
            # worker exe is torch-free).
            assert "torch" not in imported, (
                f"STARTUP-3 regression: torch was imported for {backend!r} backend "
                "(the worker exe is torch-free — master plan §6.2 P-1)."
            )
            assert "transformers" not in imported, (
                f"STARTUP-3 regression: transformers was imported for {backend!r} backend "
                "(the worker exe is torch-free — master plan §6.2 P-1)."
            )

    def test_warm_imports_warms_canonical_worker_packages(self, temp_config, monkeypatch):
        """``_warm_imports`` MUST warm every package in
        ``_WORKER_WARM_PACKAGES`` via ``_warm_package_files`` (no
        backend variation).

        XV-19/XV-32: production no longer does ``import torch`` (which
        executes ~5s of CPU). Instead it calls
        ``_warm_package_files(pkg_name)`` for each pkg in
        ``_WORKER_WARM_PACKAGES``, which uses ``importlib.util.find_spec``
        to locate the package files and reads them into the OS page
        cache without executing the package's code. The test verifies
        the file-warming path is taken (not the import path) AND that
        the canonical warm list is honored (a future regression that
        drops a package or re-introduces backend variation would break
        this test).
        """
        # The warm list is backend-independent post-§6.2 P-1 — pin the
        # parakeet path (which previously warmed torch+transformers;
        # the new path warms the same fixed list as whisper).
        (temp_config / "config.json").write_text(json.dumps({"asr_backend": "parakeet"}))
        warmed_packages: list[str] = []
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def tracking_import(name, *args, **kwargs):
            # production should NOT call __import__("torch") —
            # it warms files instead. Track any unexpected imports so
            # we can fail the test if a future regression reintroduces
            # the import path.
            if name in ("torch", "transformers", "faster_whisper"):
                raise AssertionError(
                    f"_warm_imports should NOT call __import__({name!r}) — "
                    f"it should use _warm_package_files instead (XV-19)."
                )
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", tracking_import)
        from voice_typer.server import prewarm
        from voice_typer.server.prewarm import cache_probe

        # Intercept _warm_package_files to track which packages were warmed.
        real_warm = cache_probe._warm_package_files

        def tracking_warm(pkg_name):
            warmed_packages.append(pkg_name)
            return real_warm(pkg_name)

        monkeypatch.setattr(cache_probe, "_warm_package_files", tracking_warm)
        # Mock the heavy modules so find_spec succeeds without actually
        # locating the real packages on this machine.
        fake_modules = {}
        for pkg_name in cache_probe._WORKER_WARM_PACKAGES:
            mock = MagicMock()
            mock.__spec__ = importlib.util.spec_from_loader(pkg_name, loader=None)
            fake_modules[pkg_name] = mock
        with patch.dict(sys.modules, fake_modules):
            prewarm._warm_imports()
        # Every package in the canonical warm list MUST have been warmed.
        for pkg in cache_probe._WORKER_WARM_PACKAGES:
            assert pkg in warmed_packages, (
                f"_warm_imports must warm {pkg!r} via _warm_package_files "
                f"(it is in _WORKER_WARM_PACKAGES). warmed_packages={warmed_packages!r}"
            )
        # torch / transformers MUST NOT have been warmed (worker is
        # torch-free).
        assert "torch" not in warmed_packages, (
            f"_warm_imports must NOT warm torch (worker is torch-free). warmed_packages={warmed_packages!r}"
        )
        assert "transformers" not in warmed_packages, (
            f"_warm_imports must NOT warm transformers (worker is torch-free). warmed_packages={warmed_packages!r}"
        )


# (Wave 3, 2026-08-14): ``TestPrewarmPosixSchedulerSupportsLaunchagentAndSystemd``
# (7 tests) was DELETED — the entire ``prewarm_scheduler_posix`` module
# was removed (prewarm became a worker startup phase — master plan §6.2
# P-1). The deleted tests pinned:
#   - ``prewarm_scheduler_posix.is_supported`` / ``is_prewarm_registered`` /
#     ``register_prewarm_task`` / ``unregister_prewarm_task`` (module
#     existence + public API)
#   - ``_build_macos_plist`` / ``_build_linux_service`` / ``_build_linux_timer``
#     (POSIX scheduler unit builders)
#   - ``task_scheduler.is_supported()`` returns True on POSIX (the OLD
#     behavior — ``is_supported`` is now Windows-only since the POSIX
#     prewarm scheduling path was deleted; the autostart code paths on
#     POSIX use LaunchAgent / systemd directly via
#     ``server_platform/autostart_macos.py`` / ``autostart_linux.py``)
#   - macOS / Linux LaunchAgent / systemd registration round-trip
# All tested functions / modules no longer exist, so the tests were
# deleted (per task B decision tree: DELETE tests that test deleted
# features). The new architecture has no OS-level prewarm scheduler,
# so there is no equivalent behavior to re-pin.


class TestAppAutostartUsesTaskSchedulerLogonTrigger:
    """STARTUP-7: Windows app autostart uses Task Scheduler logon trigger."""

    def test_app_autostart_task_xml_uses_pythonw_directly(self):
        """The app autostart XML uses pythonw.exe directly (no cmd.exe wrapper)."""
        # Force Windows mode for the XML builder
        with patch("sys.platform", "win32"):
            from voice_typer.server.server_platform import autostart_windows as platform_mod

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
        """_enable_autostart_windows tries Task Scheduler FIRST (AUTOSTART-ORDER-FIX)."""
        from voice_typer.server.server_platform import autostart_windows as awin

        monkeypatch.setattr(awin.sys, "platform", "win32")
        task_calls = []
        runkey_calls = []
        startup_calls = []
        monkeypatch.setattr(awin, "_register_app_autostart_task", lambda: task_calls.append(1) or True)
        monkeypatch.setattr(awin, "_unregister_app_autostart_runkey", lambda: runkey_calls.append(1) or True)
        monkeypatch.setattr(awin, "_register_app_autostart_runkey", lambda: runkey_calls.append(2) or True)
        monkeypatch.setattr(awin, "_register_app_autostart_startup", lambda: startup_calls.append(1) or True)
        monkeypatch.setattr(awin, "_unregister_app_autostart_startup", lambda: True)
        assert awin._enable_autostart_windows() is True
        assert task_calls == [1], "Task Scheduler registration must be tried first"
        # Stale Run key / Startup .bat entries should be cleaned up.
        assert 1 in runkey_calls, "Stale HKCU Run key entry should be cleaned up"
        assert len(startup_calls) == 0, "Startup .bat must NOT be registered when Task Scheduler succeeds"

    def test_enable_autostart_windows_falls_back_to_startup_bat_then_runkey(self, monkeypatch):
        """_enable_autostart_windows falls back to the Startup .bat, then the
        HKCU Run key, when Task Scheduler registration fails (AUTOSTART-ORDER-FIX)."""
        from voice_typer.server.server_platform import autostart_windows as awin

        monkeypatch.setattr(awin.sys, "platform", "win32")
        monkeypatch.setattr(awin, "_register_app_autostart_task", lambda: False)
        monkeypatch.setattr(awin, "_unregister_app_autostart_task", lambda: True)
        startup_called = []
        runkey_called = []
        monkeypatch.setattr(awin, "_register_app_autostart_startup", lambda: startup_called.append(1) or True)
        monkeypatch.setattr(awin, "_unregister_app_autostart_runkey", lambda: True)
        monkeypatch.setattr(awin, "_register_app_autostart_runkey", lambda: runkey_called.append(1) or True)
        assert awin._enable_autostart_windows() is True
        assert startup_called == [1], "Must fall back to Startup .bat when Task Scheduler fails"
        assert len(runkey_called) == 0, "Run key must NOT be tried when the .bat succeeds"

    def test_disable_autostart_windows_removes_both(self, monkeypatch):
        """_disable_autostart_windows removes from both Task Scheduler and Run key."""
        from voice_typer.server.server_platform import autostart_windows as awin

        task_removed = []
        runkey_removed = []
        monkeypatch.setattr(awin, "_unregister_app_autostart_task", lambda: task_removed.append(1) or True)
        monkeypatch.setattr(awin, "_unregister_app_autostart_runkey", lambda: runkey_removed.append(1) or True)
        assert awin._disable_autostart_windows() is True
        assert task_removed == [1]
        assert runkey_removed == [1]

    def test_is_autostart_windows_checks_both(self, monkeypatch):
        """_is_autostart_windows returns True if EITHER mechanism is registered."""
        from voice_typer.server.server_platform import autostart_windows as awin

        # Only Task Scheduler
        monkeypatch.setattr(awin, "_is_app_autostart_task_registered", lambda: True)
        monkeypatch.setattr(awin, "_is_app_autostart_runkey_registered", lambda: False)
        assert awin._is_autostart_windows() is True
        # Only Run key
        monkeypatch.setattr(awin, "_is_app_autostart_task_registered", lambda: False)
        monkeypatch.setattr(awin, "_is_app_autostart_runkey_registered", lambda: True)
        assert awin._is_autostart_windows() is True
        # Neither
        monkeypatch.setattr(awin, "_is_app_autostart_task_registered", lambda: False)
        monkeypatch.setattr(awin, "_is_app_autostart_runkey_registered", lambda: False)
        assert awin._is_autostart_windows() is False
