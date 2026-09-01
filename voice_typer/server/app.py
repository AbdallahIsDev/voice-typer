"""Main application orchestrator."""

from __future__ import annotations

import atexit
import logging
import logging.handlers
import os  # noqa: F401 (stdlib re-export: voice_typer.server.app.time/os are the documented test-patch seams — see app_lifecycle/app_undo)
import sys
import threading
import time  # noqa: F401 (stdlib re-export — see os above)
from typing import Any

from voice_typer.server import i18n

# Re-exported for monkeypatch seams (voice_typer.server.app.X) and for
# runtime call-time lookups from startup_tasks / single_instance / shutdown /
# startup_sequence (numpy was eagerly imported here once; the unused
# ``np = lazy_module`` binding is gone — ``from __future__ import annotations``
# keeps any future ``np.ndarray`` annotation unevaluated per PEP 563).
from voice_typer.server._busyness import BusynessCoordinator
from voice_typer.server._microphone_registry import MicrophoneRegistry

# Win32 SECURITY_ATTRIBUTES builder extracted to _security_attributes;
# re-exported so callers (security/win32_dacl.py) and the source pin in
# tests/regressions/test_security.py keep working.
from voice_typer.server._security_attributes import (  # noqa: F401
    _create_restrictive_security_attributes,
)

# extraction — the lazy-@property hub (accessors, _busy_event/_lock/
# _microphones delegates, lazy-failure sentinels, _LazyAudioProcessorProxy)
# moved to app_lazy_hub; the re-export binds the SAME sentinel objects so
# ``backing is _LAZY_FAILED`` identity checks and every
# ``from voice_typer.server.app import _LAZY_FAILED`` keep working.
# extraction — mic/model/restart/settings (AppAdmin), dictation control
# (AppDictation), recorder build + VAD preload (AppRecordingInit) and the
# eager subsystem builders (app_construction.AppConstruction) moved to their
# owner mixins; inherited through the MRO.
from voice_typer.server.app_admin import AppAdmin

# ``_register_startup_i18n_fallbacks`` is re-exported so
# ``hasattr(app_module, ...)`` and direct test calls keep working.
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

# APP_NAME stays re-exported: tests/test_e2e_smoke.py binds app.APP_NAME to
# branding's namespace behaviorally (mutation + reload), and the startup
# banner / tray surfaces read it from this module (C-BRAND-1).
from voice_typer.server.branding import APP_NAME  # noqa: F401

# Lazy-heavy classes (AudioProcessor / DuckCrashRecovery / VolumeDucker /
# WaveformBubble / ClipboardManager): their getters live in app_lazy_hub and
# import lazily inside their bodies (scipy / pyobjc / numpy deferred to first
# attribute access). HistoryDB is NOT used by app.py code any more but MUST
# stay a module-top binding: the hub's getter resolves it through THIS module
# at call time so the documented monkeypatch seam
# (voice_typer.server.app.HistoryDB) keeps intercepting construction.
# Recorder is imported inside the recording-init builder (recording/__init__
# eagerly loads numpy via 7+ submodules) — see
# tests/test_recorder_lazy_import_and_vad_cache_gates.py, which pins that
# "Recorder" is NOT in this module's __dict__.
from voice_typer.server.config import Config, _config_dir  # noqa: F401
from voice_typer.server.history_db import HistoryDB  # noqa: F401

# Migrated test-seam re-exports (TranscriptionEngine, create_hotkey_backend,
# platform_utils flags, StreamingTranscriptionSession, clean_transcribed_text,
# configure_corrections, autostart/microphone helpers): every patch site moved
# to the canonical owning modules; no app-module re-export remains for them.
# TrayIcon stays: tests patch the class through app_module.TrayIcon (same
# class object app_construction constructs).
from voice_typer.server.tray import AppState, TrayIcon  # noqa: F401

log = logging.getLogger(__name__)


def _resolve_config_dir():
    """Call-time indirection so patches on config._config_dir propagate."""
    from voice_typer.server import config as _config_module

    return _config_module._config_dir()


# _setup_logging / _emit_startup_banner re-exported for the ipc_server.main +
# prewarm.run callers and the voice_typer.server.app._setup_logging patch
# sites. warn_if_in_container() is called at startup (inside logging_setup
# now); the source pin tests/regressions/test_platform_misc.py reads THIS
# module, so the symbol name stays present here.
from voice_typer.server.logging_setup import _emit_startup_banner, _setup_logging  # noqa: F401, E402


class VoiceTyperApp(AppLazyHub, AppDictation, AppAdmin, AppRecordingInit, AppConstruction):
    """The main application facade.

    ``__init__`` runs the ``_init_*`` builders (one subsystem slice each,
    defined on the AppConstruction / AppRecordingInit mixins) in the exact
    historical order — construction order is behavior (thread registry before
    any spawned thread, ``self.config`` before the tray, the mutation lock
    before it is shared with Config). Lazy accessors live on ``AppLazyHub``,
    dictation control on ``AppDictation``, mic/model/restart/settings on
    ``AppAdmin``.
    """

    # Declared at class level (not only dynamically injected by
    # DictationPipeline._maybe_init_vocabulary_automation) so
    # build_ipc_server's AppProtocol isinstance check passes from construction
    # time; the pipeline overwrites it with the real controller.
    _vocabulary_automation: Any = None

    def __init__(self):
        """Run the subsystem builders in the historical order."""
        # English i18n fallbacks BEFORE any builder — the config-load-failure
        # notification in _init_config resolves error.config_load_failed.*.
        _register_startup_i18n_fallbacks()
        self._init_config()
        self._init_threading_and_crash()
        self._log_startup_banner()
        self._init_audio()
        self._init_recording()
        self._init_models()
        self._init_tray()
        self._init_controllers()
        self._init_hotkeys_and_locks()
        self._init_state_flags()
        self._init_history_crash_volume()
        self._init_misc_backings()

    # Moved builders, inherited via the MRO: _init_config /
    # _init_threading_and_crash / _log_startup_banner / _init_audio /
    # _init_models / _init_tray / _init_controllers /
    # _init_history_crash_volume / _init_misc_backings live in
    # app_construction.AppConstruction; _init_recording / _preload_vad_model
    # in app_recording_init.AppRecordingInit. The two builders below stay in
    # app.py: their lock/event declarations are pinned to THIS module's
    # source (comments stripped) by
    # tests/test_lock_order_contract.py::TestLockInventory.

    def _init_hotkeys_and_locks(self) -> None:
        """Construct HotkeyDispatcher, busyness/mic coordinators, and the
        config-mutation lock (wired into Config)."""
        # HotkeyDispatcher owns the 3 hotkey backends (dictation/ESC/repaste)
        # + register/restart logic; the legacy field mirrors were removed —
        # callers use self.hotkeys.<field> directly. _streaming_session /
        # _transcription_thread live in RecordingController.
        from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher

        self.hotkeys: HotkeyDispatcher = HotkeyDispatcher(self)
        # Pipeline busy flag lives in BusynessCoordinator; the cached mic list
        # in MicrophoneRegistry. The legacy _busy_event/_lock/_microphones
        # attributes remain reachable via the AppLazyHub back-compat
        # properties delegating to these coordinators (recording_lifecycle,
        # transcription_watchdog, dictation_pipeline/*, model_manager,
        # startup_tasks.load_microphones, service/microphone_test, tray_menu).
        # NOTE: legacy _busy_event semantics are INVERTED (SET == not busy —
        # the event doubles as a ready signal); the inversion is internal to
        # the coordinator (is_busy/set_busy/set_idle/wait_idle).
        self._busyness = BusynessCoordinator()
        self._microphone_registry = MicrophoneRegistry()
        # Serialize Config mutations between concurrent IPC set_config
        # threads: without it two simultaneous set_config calls interleave
        # attribute writes into a torn config (half the fields from each).
        # Held for the full read-modify-save sequence. RLock so nested
        # same-thread acquisition (apply_config -> save()) is safe.
        self._config_mutation_lock = threading.RLock()

        # Wire the lock into the Config instance so every config.save() caller
        # (settings_controller, hotkey_dispatcher, model_manager,
        # recorder._persist_mic, startup_sequence, service.apply_config,
        # onboarding_apply, ...) acquires it via
        # Config._save_with_mutation_lock. Previously only the IPC set_config
        # path was serialized — a background mic-fallback save could interleave
        # with an in-flight apply_config and persist a torn snapshot. MUST run
        # after both self.config and self._config_mutation_lock exist — the
        # lock has to be created before it is shared.
        self.config.set_mutation_lock(self._config_mutation_lock)

    def _init_state_flags(self) -> None:
        """Declare shutdown/electron/restart/esc flags + timer wiring."""
        # _model_load_attempted / _model_load_thread / _pending_dictation live
        # in ModelManager — callers use self.models.<field> directly.
        self._shutting_down = False  # True once quit() starts
        # Bool gate: the getattr(self.app, "_shutting_down", False) is True
        # idiom in ipc_server/sidecar_ws accommodates test MagicMock
        # auto-vivification — a truthy int assignment must NOT bypass the
        # shutdown gate; fail loudly at __init__ instead.
        if not isinstance(self._shutting_down, bool):
            raise TypeError(f"VoiceTyperApp._shutting_down must be bool, got {type(self._shutting_down).__name__}")
        # Event version of _shutting_down so executor tasks can check it
        # without reading the boolean (no cross-thread memory-order guarantee).
        self._shutting_down_event = threading.Event()
        # Incremented by startup_sequence.py on persistent onboarding check
        # failure; declared so pyrefly sees a class attribute, not ad-hoc.
        self._onboarding_fail_count: int = 0
        # Idempotency guard for _do_cleanup(): quit(), restart_app(), and
        # _atexit_cleanup() delegate to the same body without double-flushing
        # history_db / double-stopping the recorder / double-closing handles.
        self._cleanup_done: bool = False
        # PID of the Electron subprocess we launched in standalone mode (None
        # when Electron spawned us or standalone launch failed); quit()
        # terminates it explicitly during shutdown.
        self._electron_pid: int | None = None
        # True when restart_app() runs in standalone mode and the process must
        # stay alive to re-initialize in the same console (the entrypoint loop
        # re-runs the startup sequence after app.start() returns).
        self._in_place_restart: bool = False
        # Gates the global ESC cancel hotkey: set by the set_esc_cancel_paused
        # IPC handler while the frontend HotkeyPicker is capturing, so the ESC
        # polling callback doesn't fire mid-assignment.
        self._esc_cancel_paused: bool = False
        # Timer lifecycle lives on TimerCoordinator; the app keeps thin
        # delegates (_schedule_timer / _cancel_pending_timers) so existing
        # callers and monkeypatch sites keep working.
        from voice_typer.server.timer_coordinator import TimerCoordinator

        self.timers: TimerCoordinator = TimerCoordinator(self)
        # Shadow declarations pointing at the coordinator's state so runtime
        # stress tests reading app._pending_timers_lock directly keep working.
        self._pending_timers: list[threading.Timer] = self.timers._pending_timers
        self._pending_timers_lock = self.timers._pending_timers_lock
        self._timer_generation: int = self.timers._timer_generation
        self._cycle_counter = 0  # monotonic dictation-cycle counter
        self._cycle_id: str = ""  # human-readable cycle id for log correlation

    def _schedule_timer(self, delay: float, func) -> threading.Thread:
        """Delegate to TimerCoordinator."""
        return self.timers._schedule_timer(delay, func)

    def _cancel_pending_timers(self):
        """Delegate to TimerCoordinator."""
        return self.timers._cancel_pending_timers()

    def _wire_waveform_bubble(self) -> None:
        """Delegate to WaveformBubbleWiring.

        The bubble window is owned by the Electron main process; we emit push
        events via the ipc_server module-level hook (no closure capture).
        """
        self.waveform_wiring._wire_waveform_bubble()

    def start(self):
        """Initialize and run the application."""
        self.tray.set_notifications_enabled(self.config.show_notifications)
        # Localized via i18n.t (English fallback registered in __init__).
        self.tray.set_state(AppState.LOADING, i18n.t("state.app.starting"))
        # Wire the waveform bubble on the main thread BEFORE the bg
        # _do_startup thread runs. Idempotent; the lazy properties defer
        # construction so a constructed-but-never-started app pays nothing.
        try:
            self._wire_waveform_bubble()
        except Exception:
            log.warning("[START] waveform bubble wiring failed", exc_info=True)
        self.tray.start(bg_work=self._do_startup)
        self._install_win32_console_handler()
        self._install_signal_handlers()
        # atexit safety net: daemon threads can be killed without running
        # their finally blocks, so cleanup that MUST happen (volume restore,
        # hotkey release) is registered here instead of relying on daemon
        # finally blocks alone.
        atexit.register(self._atexit_log)
        atexit.register(self._atexit_cleanup)
        # Enter the pystray event loop — MUST be on the main thread (run()
        # logs the tray-created line itself; no duplicate here).
        self.tray.run()

    def _do_startup(self) -> None:
        """Background work: sync autostart, load mics, load model, register hotkey.

        The body was extracted into StartupSequence.run — phase ordering,
        shutdown gates, parallel executor semantics, and onboarding auto-heal
        preserved (see that docstring). Tests calling app._do_startup()
        directly work; former delegate-method patch sites now patch the
        controller (e.g. monkeypatch.setattr(startup_tasks, "sync_autostart",
        ...) or app.hotkeys.register = MagicMock()).
        """
        from voice_typer.server.startup_sequence import StartupSequence

        StartupSequence(self).run()

    # Removed @property delegates — callers use the owning field directly:
    # transcriber/_qwen_engine/_parakeet_engine/_asr_registry/_model_load_* →
    # self.models.<field> (model_manager.py); _transcription_thread /
    # _streaming_session → self.recording.<field>; _hotkey_backend /
    # _esc_backend / _repaste_backend → self.hotkeys.<field>.

    def _do_cleanup(self) -> None:
        """Delegate to ShutdownController (idempotent body lives there).

        The indirection keeps monkeypatch.setattr(app, "_do_cleanup", spy)
        intercepting — pinned by
        tests/test_app_cleanup.py::test_quit_calls_do_cleanup.
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


# single_instance helpers re-exported so `from voice_typer.server.app import
# _ensure_single_instance / _backend_pid_file / _write_backend_pid_file /
# _clear_backend_pid_file / _is_pid_alive / _read_stale_backend_pid` keeps
# working (production: autostart launcher + autostart/pid_file; tests:
# test_app_cleanup, test_electron_launcher, test_feature_hardening_regressions,
# test_waveform_bubble). Source pins read THIS module for the mutex name
# "Local\\VoiceTyperSingleInstance" and _create_restrictive_security_attributes
# (tests/regressions/test_security.py, tests/test_security_hardening.py) —
# both strings must stay present in this file.
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
    """Entry point for the ``voice-typer`` console script.

    Not a bare re-export of ipc_server.main: (1) enables faulthandler for
    crash thread-dumps (SIGSEGV/SIGABRT — logged at WARNING if unavailable
    because the operator must know dumps won't be generated), and (2) wraps
    the canonical entry in try/except so a backend crash logs at ERROR and
    exits 1 instead of dying silently.
    """
    try:
        import faulthandler

        faulthandler.enable()
    except Exception:
        log.warning(
            "[IPC] faulthandler not available — crash thread-dumps will not be generated",
            exc_info=True,
        )

    from voice_typer.server.ipc_server import main as ipc_main

    try:
        ipc_main()
    except Exception:
        log.exception("[FATAL] backend crashed")
        sys.exit(1)


# Windows editor-launch helpers re-exported for the app-admin config-editor
# delegate and the patch sites in test_api_doc_accuracy.py /
# test_config_editor_lock.py. The bare PATH-resolved "notepad" pattern is
# intentionally NOT used — _systemroot_notepad_path validates the path via
# %SYSTEMROOT%\\System32\\notepad.exe.
from voice_typer.server.platform_launch import (  # noqa: E402,F401
    _systemroot_notepad_path,
    _windows_close_process_handle,
    _windows_open_with_default_app,
    _windows_wait_for_process_exit,
)
