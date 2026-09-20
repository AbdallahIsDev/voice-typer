"""VoiceTyperApp composition root: wires mixins, construction order, delegates."""

from __future__ import annotations

import atexit
import logging
import logging.handlers
import os  # noqa: F401 (stdlib re-export: voice_typer.server.app.time/os are the documented test-patch seams, see app_lifecycle/app_undo)
import sys
import threading
import time  # noqa: F401 (stdlib re-export: see os above)
from typing import Any

from voice_typer.server import i18n

# Re-exported for monkeypatch seams (voice_typer.server.app.X) and for
from voice_typer.server._busyness import BusynessCoordinator
from voice_typer.server._microphone_registry import MicrophoneRegistry

# Win32 SECURITY_ATTRIBUTES builder extracted to _security_attributes;
from voice_typer.server._security_attributes import (  # noqa: F401
    _create_restrictive_security_attributes,
)

# extraction, the lazy-@property hub (accessors, _busy_event/_lock/
from voice_typer.server.app_admin import AppAdmin

# ``_register_startup_i18n_fallbacks`` is re-exported so
from voice_typer.server.app_construction import (  # noqa: F401
    AppConstruction,
    _register_startup_i18n_fallbacks,
)
from voice_typer.server.app_dictation import AppDictation
from voice_typer.server.app_lazy_hub import (  # noqa: F401
    _LAZY_FAILED,
    _RECORDER_MISSING,
    RETRY_TTL_SECONDS,
    AppLazyHub,
    _LazyAudioProcessorProxy,
)
from voice_typer.server.app_recording_init import AppRecordingInit

# banner / tray surfaces read it from this module (C-BRAND-1).
from voice_typer.server.branding import APP_NAME  # noqa: F401

# Lazy-heavy classes (AudioProcessor / DuckCrashRecovery / VolumeDucker /
from voice_typer.server.config import _config_dir  # noqa: F401
from voice_typer.server.history_db import HistoryDB  # noqa: F401

# Migrated test-seam re-exports (TranscriptionEngine, create_hotkey_backend,
from voice_typer.server.tray import AppState, TrayIcon  # noqa: F401

log = logging.getLogger(__name__)


def _resolve_config_dir():
    """Call-time indirection so patches on config._config_dir propagate."""
    from voice_typer.server import config as _config_module

    return _config_module._config_dir()


# sites. warn_if_in_container() is called at startup (inside logging_setup
from voice_typer.server.logging_setup import _emit_startup_banner, _setup_logging  # noqa: F401, E402


class VoiceTyperApp(AppLazyHub, AppDictation, AppAdmin, AppRecordingInit, AppConstruction):
    """The main application facade."""

    # Declared at class level (not only dynamically injected by
    _vocabulary_automation: Any = None

    def __init__(self):
        """Run the subsystem builders in the historical order."""
        # English i18n fallbacks BEFORE any builder, the config-load-failure
        _register_startup_i18n_fallbacks()
        self._init_config()
        self._init_threading_and_crash()
        self._log_startup_banner()
        self._init_audio()
        self._init_recording()
        # _init_models builds ModelManager as self.models (AppConstruction).
        self._init_models()
        self._init_tray()
        self._init_controllers()
        self._init_hotkeys_and_locks()
        self._init_state_flags()
        self._init_history_crash_volume()
        self._init_misc_backings()

    # _init_threading_and_crash / _log_startup_banner / _init_audio /

    # - hotkey backends → HotkeyDispatcher (`self.hotkeys`)

    def _init_hotkeys_and_locks(self) -> None:
        """Construct HotkeyDispatcher, busyness/mic coordinators, and the
        config-mutation lock (wired into Config)."""
        # callers use self.hotkeys.<field> directly. _streaming_session /
        from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher

        self.hotkeys: HotkeyDispatcher = HotkeyDispatcher(self)
        # in MicrophoneRegistry. The legacy _busy_event/_lock/_microphones
        self._busyness = BusynessCoordinator()
        self._microphone_registry = MicrophoneRegistry()
        # Serialize Config mutations between concurrent IPC set_config
        self._config_mutation_lock = threading.RLock()

        # Wire the lock into the Config instance so every config.save() caller
        self.config.set_mutation_lock(self._config_mutation_lock)

    def _init_state_flags(self) -> None:
        """Declare shutdown/host/restart/esc flags + timer wiring."""
        # _model_load_attempted / _model_load_thread / _pending_dictation live
        self._shutting_down = False  # True once quit() starts
        # Bool gate: the getattr(self.app, "_shutting_down", False) is True
        if not isinstance(self._shutting_down, bool):
            raise TypeError(f"VoiceTyperApp._shutting_down must be bool, got {type(self._shutting_down).__name__}")
        # Event version of _shutting_down so executor tasks can check it
        self._shutting_down_event = threading.Event()
        # Incremented by startup_sequence.py on persistent onboarding check
        self._onboarding_fail_count: int = 0
        # Idempotency guard for _do_cleanup(): quit(), restart_app(), and
        self._cleanup_done: bool = False
        # PID of the host process we launched in standalone mode (None when
        self._host_pid: int | None = None
        # True when restart_app() runs in standalone mode and the process must
        self._in_place_restart: bool = False
        # Gates the global ESC cancel hotkey: set by the set_esc_cancel_paused
        self._esc_cancel_paused: bool = False
        # Timer lifecycle lives on TimerCoordinator; the app keeps thin
        from voice_typer.server.timer_coordinator import TimerCoordinator

        self.timers: TimerCoordinator = TimerCoordinator(self)
        self._pending_timers_lock = self.timers._pending_timers_lock
        self._cycle_counter = 0  # monotonic dictation-cycle counter
        self._cycle_id: str = ""  # human-readable cycle id for log correlation

    # Ownership: ``TimerCoordinator`` (``self.timers``) owns

    @property
    def _pending_timers(self) -> list[threading.Timer]:
        """Same list object ``TimerCoordinator`` mutates (never a copy)."""
        return self.timers._pending_timers

    @_pending_timers.setter
    def _pending_timers(self, value: list[threading.Timer]) -> None:
        """Rebind the coordinator's list so both paths stay one object."""
        self.timers._pending_timers = value

    @property
    def _timer_generation(self) -> int:
        """Same int ``TimerCoordinator`` bumps on cancel."""
        return self.timers._timer_generation

    @_timer_generation.setter
    def _timer_generation(self, value: int) -> None:
        """Rebind the coordinator's generation counter."""
        self.timers._timer_generation = value

    def _schedule_timer(self, delay: float, func) -> threading.Thread:
        """Delegate to TimerCoordinator."""
        return self.timers._schedule_timer(delay, func)

    def _cancel_pending_timers(self):
        """Delegate to TimerCoordinator."""
        return self.timers._cancel_pending_timers()

    def _wire_waveform_bubble(self) -> None:
        """The bubble window is owned by the predecessor main process; we emit push
        events via the ipc_server module-level hook (no closure capture).
        """
        self.waveform_wiring._wire_waveform_bubble()

    def start(self):
        """Initialize and run the application."""
        self.tray.set_notifications_enabled(self.config.show_notifications)
        # Localized via i18n.t (English fallback registered in __init__).
        self.tray.set_state(AppState.LOADING, i18n.t("state.app.starting"))
        # Wire the waveform bubble on the main thread BEFORE the bg
        try:
            self._wire_waveform_bubble()
        except Exception:
            log.warning("[START] waveform bubble wiring failed", exc_info=True)
        self.tray.start(bg_work=self._do_startup)
        self._install_win32_console_handler()
        self._install_signal_handlers()
        # atexit safety net: daemon threads can be killed without running
        atexit.register(self._atexit_log)
        atexit.register(self._atexit_cleanup)
        # Enter the pystray event loop. MUST be on the main thread (run()
        self.tray.run()

    def _do_startup(self) -> None:
        """The body was extracted into StartupSequence.run, phase ordering,
        shutdown gates, parallel executor semantics, and onboarding auto-heal
        """
        from voice_typer.server.startup_sequence import StartupSequence

        StartupSequence(self).run()

    # self.models.<field> (model_manager.py); _transcription_thread /

    def _do_cleanup(self) -> None:
        """The indirection keeps monkeypatch.setattr(app, "_do_cleanup", spy)
        intercepting, pinned by
        """
        return self.shutdown._do_cleanup()

    def quit(self):
        """Delegate to ShutdownController."""
        return self.shutdown.quit()

    def _atexit_log(self) -> None:
        """Delegate to ShutdownController."""
        return self.shutdown._atexit_log()

    def _atexit_cleanup(self) -> None:
        """Delegate to ShutdownController."""
        return self.shutdown._atexit_cleanup()

    def _install_signal_handlers(self):
        """Delegate to ShutdownController."""
        return self.shutdown._install_signal_handlers()

    def _install_win32_console_handler(self):
        """Delegate to ShutdownController."""
        return self.shutdown._install_win32_console_handler()

    def _win32_console_handler(self, ctrl_type):
        """Delegate to ShutdownController."""
        return self.shutdown._win32_console_handler(ctrl_type)


# "Local\\VoiceTyperSingleInstance" and _create_restrictive_security_attributes
from voice_typer.server.single_instance import (  # noqa: E402,F401
    _backend_pid_file,
    _clear_backend_pid_file,
    _ensure_single_instance,
    _ensure_windows_single_instance,
    _is_pid_alive,
    _read_stale_backend_pid,
    _write_backend_pid_file,
)


def main() -> None:
    """Not a bare re-export of ipc_server.main: (1) enables faulthandler for
    crash thread-dumps (SIGSEGV/SIGABRT, logged at WARNING if unavailable
    """
    try:
        import faulthandler

        faulthandler.enable()
    except Exception:
        log.warning(
            "[IPC] faulthandler not available, crash thread-dumps will not be generated",
            exc_info=True,
        )

    from voice_typer.server.ipc_server import main as ipc_main

    try:
        ipc_main()
    except Exception:
        log.exception("[FATAL] backend crashed")
        sys.exit(1)


# Windows editor-launch helpers re-exported for the app-admin config-editor
from voice_typer.server.platform_launch import (  # noqa: E402,F401
    _systemroot_notepad_path,
    _windows_close_process_handle,
    _windows_open_with_default_app,
    _windows_wait_for_process_exit,
)
