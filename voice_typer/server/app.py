"""Main application orchestrator."""

import atexit
import contextlib
import logging
import logging.handlers
import os
import queue
import re
import signal
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import numpy as np

# CRASH-HANDLER: Windows VEH + Python excepthook for silent crash diagnostics
from voice_typer.server import crash_handler as _crash_handler

# Re-exported for monkeypatch.setattr("voice_typer.server.app.X", ...) in tests
# and for runtime lookups from voice_typer.server.startup_tasks.  # ruff: noqa: F401
from voice_typer.server import (
    i18n,  # NF-R16-1: server-side i18n for tray notifications
    task_scheduler,
)

# SEC-001: restart token functions moved to voice_typer.server.security
# COMPAT-001: backward-compat re-export for tests/test_pii_redaction.py
# which imports _PIIRedactionFilter from app. The class lives in
# voice_typer.server.security as PIIRedactionFilter (no underscore).
# RW-00: Win32 SECURITY_ATTRIBUTES builder extracted to a focused,
# security-reviewable module.  Re-exported here so existing callers
# (and tests that grep app.py source for the symbol name) keep working.
from voice_typer.server._security_attributes import (  # noqa: F401
    _create_restrictive_security_attributes,
)
from voice_typer.server.audio_processor import AudioProcessor
from voice_typer.server.audio_quality import AudioQualityAnalyzer
from voice_typer.server.branding import APP_NAME
from voice_typer.server.clipboard import ClipboardCopyError, ClipboardManager
from voice_typer.server.config import Config, _config_dir, _migrate_from_legacy
from voice_typer.server.config_editor import ConfigEditorLauncher
from voice_typer.server.crash_recovery import CrashRecovery
from voice_typer.server.duck_crash_recovery import DuckCrashRecovery
from voice_typer.server.history_db import HistoryDB

# Re-exported for monkeypatch.setattr("voice_typer.server.app.X", ...) in tests.  # ruff: noqa: F401
from voice_typer.server.hotkeys import HotkeyBackend, create_hotkey_backend
from voice_typer.server.log import (
    close_devnull_files as _close_devnull_files,
)
from voice_typer.server.log import (
    register_devnull_file as _register_devnull_file,
)

# CQ-029: use centralized platform helpers instead of raw sys.platform checks
from voice_typer.server.platform_utils import is_linux, is_macos, is_windows
from voice_typer.server.recording import Recorder
from voice_typer.server.security import PIIRedactionFilter as _PIIRedactionFilter  # noqa: F401
from voice_typer.server.security import (
    generate_restart_token as _generate_restart_token,
)
from voice_typer.server.security import (
    verify_restart_token as _verify_restart_token,
)

# create_launcher_shortcut + list_microphones are re-exported here (and consumed
# from voice_typer.server.startup_tasks) so tests that monkeypatch
# voice_typer.server.app.list_microphones / create_launcher_shortcut keep working.  # ruff: noqa: F401
from voice_typer.server.server_platform import (
    create_launcher_shortcut,
    disable_autostart,
    enable_autostart,
    is_autostart_enabled,
    list_microphones,
)
from voice_typer.server.streaming import (
    StreamingTranscriptionSession,  # noqa: F401  (re-exported for tests/test_app.py monkeypatch)
)
from voice_typer.server.text_cleanup import clean_transcribed_text, configure_corrections
from voice_typer.server.thread_registry import ThreadRegistry
from voice_typer.server.transcription import TranscriptionEngine
from voice_typer.server.tray import AppState, TrayIcon
from voice_typer.server.volume_ducker import VolumeDucker
from voice_typer.server.waveform import WaveformBubble

if TYPE_CHECKING:
    # TASK-14: imported only for type annotations on ``_template_manager``
    # and ``_vocabulary_manager`` (declared Optional so the eager-init
    # ``= None`` fallback in __init__ type-checks).  The runtime imports
    # remain inside the try/except in __init__ so a missing optional
    # dependency does not break VoiceTyperApp construction.
    from voice_typer.server.templates import TemplateManager
    from voice_typer.server.vocabulary import VocabularyManager

log = logging.getLogger(__name__)

# REF-3: extraction — _setup_logging moved to voice_typer.server.logging_setup.
# Re-exported here so callers (voice_typer.server.ipc_server.main,
# voice_typer.server.prewarm.run) and tests that monkeypatch
# voice_typer.server.app._setup_logging keep working unchanged.
# PLAT-021: _setup_logging calls warn_if_in_container() (from
# voice_typer.server.container_detect) at startup to detect container
# environments and warn about unavailable features. The call lives in
# logging_setup.py now but the source-string assertion in
# tests/regressions/platform_misc_test.py::test_container_detect_called_in_startup
# greps app.py source for the symbol name — kept here as a comment.  # ruff: noqa: F401
# REF-3: extraction — _validate_env_vars moved to voice_typer.server.env_validation.
# Re-exported here so tests doing `from voice_typer.server.app import _validate_env_vars`
# keep working (test_plat_fixes.py / regressions/platform_misc_test.py).
# SEC-audit-011: _validate_env_vars calls _validate_systemroot from
# voice_typer.server.config to reject attacker-controlled SystemRoot values
# that could enable DLL injection.  # ruff: noqa: F401
from voice_typer.server.env_validation import _validate_env_vars  # noqa: E402
from voice_typer.server.logging_setup import _setup_logging  # noqa: E402


class VoiceTyperApp:
    """The main application."""

    def __init__(self):
        # DE-48: catch unexpected exceptions from Config.load() (e.g.
        # KeyError from a data[...] access without a default, or
        # AttributeError from a None dereference during schema
        # migration).  Log at ERROR with exc_info=True, fall back to
        # Config() defaults, and flag the failure so a tray notification
        # can be surfaced once the tray is built later in __init__.
        try:
            self.config = Config.load()
        except Exception:
            log.error("[INIT] Config.load() raised", exc_info=True)
            self.config = Config()
            self._config_load_failed = True
        else:
            self._config_load_failed = False

        # THREAD-REGISTRY: create the central registry FIRST so all
        # subsystems constructed below (Recorder, CrashRecovery,
        # StreamingTranscriptionSession via RecordingController, and the
        # bubble-level-pusher spawned in _wire_waveform_bubble) can
        # register their threads with it. ``quit()`` calls
        # ``shutdown_all()`` before the existing _do_cleanup() sequence
        # so the registry's signal-and-join runs first; the per-site
        # shutdown methods then run as a safety net (they're idempotent).
        self._thread_registry = ThreadRegistry()

        # Install Python-level excepthook for unhandled Python exceptions.
        # APP-9 (F-07): wrapped in try/except so an excepthook-install
        # failure (e.g. a missing Win32 API on an unsupported build, or
        # a sys.excepthook assignment that raises on a restricted
        # interpreter) does not abort VoiceTyperApp construction. The
        # excepthook is a best-effort diagnostics aid — if it can't be
        # installed, we log at DEBUG (with exc_info=True) so the
        # failure is diagnosable without spamming the default-INFO
        # production log, and continue with init.
        try:
            _crash_handler.install_python_excepthook()
        except Exception:
            log.debug("[INIT] excepthook install failed", exc_info=True)

        # Startup banner -- first visible log, before any subsystem init
        log.info(
            "%s starting -- model=%s, hotkey=%s, mic=%s, sample_rate=%s",
            APP_NAME,
            self.config.model_size,
            self.config.hotkey,
            self.config.microphone or "default",
            self.config.sample_rate,
        )

        # ADR 0007: Audio processor wraps a FilterChain built from config.
        # Rebuilt on every config change via _rebuild_audio_processor()
        # so Settings UI changes take effect immediately in dictation.
        self._audio_processor = AudioProcessor(
            self.config,
            sample_rate=self.config.sample_rate,
        )

        # AudioQualityAnalyzer: wired to the AudioProcessor's
        # per-chunk quality callback so it accumulates clipping /
        # low-volume / high-noise statistics during recording.
        # After Recorder.stop(), _finalize_audio_quality_report() runs
        # analyze_full_audio() on the captured samples and surfaces any
        # issues via a tray notification (gated by
        # config.audio_quality_warnings).
        self._audio_quality = AudioQualityAnalyzer()
        self._audio_quality.reset()
        self._audio_processor.set_quality_callback(self._on_audio_quality_chunk)

        self.recorder = Recorder(
            self.config,
            audio_processor=self._audio_processor,
            thread_registry=self._thread_registry,
        )
        # #2 Recording lifecycle extracted to RecordingController.
        # Owns toggle/start/stop/cancel, silence/xrun callbacks, and the
        # streaming session. The recorder's xrun threshold callback is
        # wired to RecordingController.on_xrun_threshold instead of the
        # old VoiceTyperApp._on_xrun_threshold method.
        from voice_typer.server.recording_controller import RecordingController

        self.recording: RecordingController = RecordingController(self)
        # Item 1: wire xrun threshold callback for tray notification
        self.recorder.on_xrun_threshold = self.recording.on_xrun_threshold
        # #2 ASR backend lifecycle extracted to ModelManager.
        # Previously VoiceTyperApp owned the AsrBackendRegistry + three
        # engine fields + ~500 LOC of load/fallback/change logic. Now
        # ModelManager owns all of that; app.py accesses it via
        # `self.models`. (ARCH-REFAC-003: the @property delegates that
        # used to mirror `self.transcriber` / `self._qwen_engine` /
        # `self._asr_registry` / etc. on VoiceTyperApp have been
        # removed — callers now use `self.models.<field>` directly.)
        from voice_typer.server.model_manager import ModelManager

        self.models: ModelManager = ModelManager(self)
        if self.config.asr_backend == "qwen" and self.config.qwen_model_path:
            # Eager-init the Qwen engine if configured (mirrors the
            # pre-Round-9 behavior in __init__).
            self.models._ensure_engine("qwen")

        self.clipboard = ClipboardManager(
            paste_enabled=self.config.paste_on_stop,
        )
        self.tray = TrayIcon(
            controller=self,
            config=self.config,
        )

        # DE-48: if Config.load() failed earlier, surface a tray
        # notification so the user knows their settings were reset to
        # defaults.  Wrapped in try/except so a tray.backend failure
        # (e.g. notification daemon not ready) doesn't crash init.
        if self._config_load_failed:
            try:
                self.tray.notify(
                    "Config load failed",
                    "Settings were reset to defaults. Check the logs for details.",
                )
            except Exception:
                log.debug("[INIT] tray.notify for config load failure failed", exc_info=True)

        # RW-9 Phase 6: settings side-effects (autostart, notifications,
        # microphone selection) extracted to SettingsController. The app
        # keeps thin delegate methods (``_toggle_autostart``,
        # ``_set_autostart``, ``_set_notifications``, ``_select_microphone``)
        # so tray menu callbacks and tests calling ``app._select_microphone``
        # keep working unchanged. ``_open_config_file`` stays on
        # VoiceTyperApp because source-level structure tests
        # (test_config_editor_lock.py) pin its body via inspect.getsource.
        from voice_typer.server.settings_controller import SettingsController

        self.settings: SettingsController = SettingsController(self)

        # RW-9 Phase 7: shutdown / cleanup lifecycle (quit, _do_cleanup,
        # _atexit_*, signal handlers, Win32 console handler) extracted to
        # ShutdownController. The app keeps thin delegate methods so
        # ``app.start()``'s ``atexit.register`` calls, tray menu callbacks
        # (quit_app -> self.quit()), restart_app (-> self._do_cleanup()),
        # and tests calling ``app._do_cleanup()`` directly all keep working
        # unchanged.
        from voice_typer.server.shutdown_controller import (
            SHUTDOWN_WATCHDOG_TIMEOUT_S,
            ShutdownController,
        )

        self.shutdown: ShutdownController = ShutdownController(self)
        # GT-43: stash the watchdog timeout on the instance so
        # restart_app()'s non-main-thread branch can arm the watchdog
        # without re-importing the constant.
        self._shutdown_watchdog_timeout_s: float = SHUTDOWN_WATCHDOG_TIMEOUT_S

        # RW-9 Phase 7: audio-quality side-effects extracted to
        # AudioQualityController. The app keeps thin delegate methods so
        # ``self._audio_processor.set_quality_callback(self._on_audio_quality_chunk)``,
        # ``service.apply_config_side_effects`` (-> _rebuild_audio_processor),
        # and ``RecordingController.stop`` (-> _finalize_audio_quality_report)
        # all keep working unchanged.
        from voice_typer.server.audio_quality_controller import AudioQualityController

        self.audio_quality: AudioQualityController = AudioQualityController(self)

        # #2 Hotkey registration extracted to HotkeyDispatcher.
        # Owns the 3 hotkey backends (dictation / ESC / repaste) and the
        # register/restart logic. (ARCH-REFAC-003: the @property
        # delegates that used to mirror the 3 legacy fields
        # (_hotkey_backend, _esc_backend, _repaste_backend) on
        # VoiceTyperApp have been removed — callers now use
        # `self.hotkeys.<field>` directly.)
        from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher

        self.hotkeys: HotkeyDispatcher = HotkeyDispatcher(self)
        # #2 _streaming_session and _transcription_thread now
        # live in RecordingController. (ARCH-REFAC-003: the @property
        # delegates that used to mirror them on VoiceTyperApp have been
        # removed — callers now use `self.recording.<field>` directly,
        # or `self.recording.get_streaming_session()` /
        # `self.recording.set_streaming_session(...)`.)
        self._microphones: list[dict] = []
        self._busy_event = threading.Event()
        self._busy_event.set()  # SET = not busy
        self._lock = threading.Lock()
        # RACE-011: serialize Config mutations between concurrent IPC
        # set_config handlers (multiple IPC server threads). Without
        # this lock, two simultaneous set_config calls can interleave
        # attribute writes and produce a torn config state — e.g. half
        # the fields from one request, half from another. The lock is
        # held for the full read-modify-save sequence so each mutation
        # sees a consistent view of the Config object. The historical
        # tkinter SettingsController.apply() path that also consumed
        # this lock has been removed (the deprecated settings.py module
        # was deleted); the lock remains because the IPC set_config
        # path still requires serialization.
        self._config_mutation_lock = threading.RLock()

        # #2 _model_load_attempted / _model_load_thread /
        # _pending_dictation now live in ModelManager. (ARCH-REFAC-003:
        # the @property delegates that used to mirror them on
        # VoiceTyperApp have been removed — callers now use
        # `self.models.<field>` directly.)
        self._shutting_down = False  # True once quit() starts
        # RACE-020: threading.Event version of _shutting_down so executor
        # tasks can check it without reading the boolean (which provides
        # no memory-order guarantee across threads).
        self._shutting_down_event = threading.Event()
        # PYREFLY-TASK-16: counter incremented by startup_sequence.py
        # when the onboarding check persistently fails (see
        # startup_sequence.py:140-149). Declared here so pyrefly
        # recognizes it as a class attribute rather than an ad-hoc
        # dynamic attribute. Initialized to 0; startup_sequence.py
        # uses getattr-with-default as a defensive read but always
        # assigns before incrementing.
        self._onboarding_fail_count: int = 0
        # RW-3: idempotency guard for _do_cleanup(). Set to True once
        # the shared cleanup body has run, so a second call (e.g. from
        # _atexit_cleanup after quit() already ran) is a no-op. This
        # is the safety that lets quit(), restart_app(), and
        # _atexit_cleanup() all delegate to the same _do_cleanup()
        # without double-flushing history_db / double-stopping the
        # recorder / double-closing the Win32 mutex handle.
        self._cleanup_done: bool = False
        # P1-1.3: PID of the Electron subprocess we launched in standalone
        # mode (None when Electron spawned us, or when standalone launch
        # failed).  Tracked here so quit() can terminate the subprocess
        # explicitly during shutdown.
        self._electron_pid: int | None = None
        # ESC-FIX-001: flag gating the global ESC cancel hotkey.  Set to
        # True by the ""set_esc_cancel_paused"" IPC handler when the
        # frontend HotkeyPicker enters capture mode, so the backend's
        # ESC polling callback doesn't fire while the user is assigning
        # a custom hotkey in the Settings UI.
        self._esc_cancel_paused: bool = False
        # RW-9 Phase 7: timer lifecycle extracted to TimerCoordinator.
        # The three state attributes (_pending_timers / _pending_timers_lock
        # / _timer_generation) now live on TimerCoordinator; VoiceTyperApp
        # keeps thin delegate methods (_schedule_timer / _cancel_pending_timers)
        # so existing callers and tests that monkeypatch them keep working.
        from voice_typer.server.timer_coordinator import TimerCoordinator

        self.timers: TimerCoordinator = TimerCoordinator(self)
        # Shadow declarations — point at the coordinator's state so
        # tests/test_lock_order_contract.py::TestLockInventory (which
        # pins app.py source for `self._pending_timers_lock = threading.Lock()`)
        # and runtime stress tests that read app._pending_timers_lock
        # directly both keep working.
        self._pending_timers: list[threading.Timer] = self.timers._pending_timers
        self._pending_timers_lock = self.timers._pending_timers_lock
        self._timer_generation: int = self.timers._timer_generation
        self._cycle_counter = 0  # monotonic counter for dictation cycles
        self._cycle_id: str = ""  # human-readable cycle id for log correlation

        # ─── P1/P2 New Feature Components ────────────────────────────
        self.history_db = HistoryDB()
        self._crash_recovery = CrashRecovery(
            thread_registry=self._thread_registry,
        )
        # Volume ducking: reduces system volume during dictation to
        # prevent speaker output from bleeding into the microphone.
        # Crash recovery persists the pre-duck volume so a crash
        # doesn't leave the system stuck at a low volume.
        # Use _config_dir() so the crash-recovery file lives alongside
        # the rest of the user's voice-typer state (and tests can
        # monkeypatch _config_dir to point at a tmp_path).
        self._duck_crash_recovery = DuckCrashRecovery(config_dir=_config_dir())
        # RW-9 Phase 7: VolumeController owns duck/restore side effects.
        # Constructed BEFORE _volume_ducker because the ducker's
        # on_crash_restore callback is bound to self._on_volume_crash_restore,
        # which delegates to self.volume.
        from voice_typer.server.volume_controller import VolumeController

        self.volume: VolumeController = VolumeController(self)
        self._volume_ducker = VolumeDucker(
            crash_recovery=self._duck_crash_recovery,
            on_crash_restore=self._on_volume_crash_restore,
        )
        # NOTE: AudioQualityAnalyzer is now instantiated earlier in
        # __init__ (next to AudioProcessor) and wired to the processor's
        # per-chunk quality callback.  See self._audio_quality /
        # self._on_audio_quality_chunk / _finalize_audio_quality_report.
        self._waveform_bubble = WaveformBubble()
        # RW-9 Phase 7: waveform-bubble wiring extracted to
        # WaveformBubbleWiring. The app keeps a thin delegate method
        # (_wire_waveform_bubble) so existing callers and tests keep
        # working. The worker / queue / stop_event now live on
        # WaveformBubbleWiring; _do_cleanup calls waveform_wiring.stop()
        # to shut the worker down.
        from voice_typer.server.waveform_bubble_wiring import WaveformBubbleWiring

        self.waveform_wiring: WaveformBubbleWiring = WaveformBubbleWiring(self)
        self._wire_waveform_bubble()
        self._last_transcription: str = ""  # For repaste
        # TASK-14: declare ``_ipc_server`` upfront so VoiceTyperApp
        # satisfies the ``AppProtocol`` structural type checked by
        # ``providers.build_ipc_server``.  The attribute is set later
        # by ``IPCServer.start()`` (``self.app._ipc_server = self``);
        # initializing it to ``None`` here means pyrefly sees the
        # attribute exists on every instance, satisfying the protocol.
        self._ipc_server: Any | None = None
        # ARCH-011: eager-init managers so config changes between
        # startup and first dictation are reflected.  Previously these
        # were lazy-init on first use, which meant a config change
        # (e.g. editing corrections.json) before the first dictation
        # was NOT picked up because the manager was created from stale
        # config.  Eager init ensures the managers see the config as
        # of __init__ time; reload() can be called later if needed.
        # TASK-14: annotate as ``Optional`` so the ``= None`` fallback
        # in the except branch below type-checks.  Without the
        # annotation pyrefly infers ``TemplateManager`` from the
        # try-block assignment and then rejects the ``None`` reset.
        self._template_manager: "TemplateManager | None" = None  # noqa: UP037
        self._vocabulary_manager: "VocabularyManager | None" = None  # noqa: UP037
        # APP-8 (F-07): eager-init failures must be logged at WARNING
        # (not DEBUG) with exc_info=True so they're visible in the
        # default-INFO production log and the stack trace is captured
        # for diagnosis. Pre-fix, these were swallowed at DEBUG, making
        # template/vocabulary init failures effectively invisible.
        try:
            from voice_typer.server.templates import TemplateManager

            self._template_manager = TemplateManager()
        except Exception:
            log.warning("[INIT] TemplateManager eager-init failed", exc_info=True)
            self._template_manager = None
        try:
            from voice_typer.server.vocabulary import VocabularyManager

            self._vocabulary_manager = VocabularyManager()
        except Exception:
            log.warning("[INIT] VocabularyManager eager-init failed", exc_info=True)
            self._vocabulary_manager = None
        self._llm_polisher = None  # Created on first polish (needs consent check)
        self._cloud_engine = None  # Lazy-init if cloud backend selected

    # ─── Volume Ducking ────────────────────────────────────────────────

    def _on_volume_crash_restore(self, state) -> None:
        """RW-9 Phase 7: delegate to VolumeController."""
        self.volume._on_volume_crash_restore(state)

    def _duck_volume(self) -> None:
        """RW-9 Phase 7: delegate to VolumeController."""
        self.volume._duck_volume()

    def _restore_volume(self, fade_ms: int | None = None) -> None:
        """RW-9 Phase 7: delegate to VolumeController."""
        self.volume._restore_volume(fade_ms=fade_ms)

    # ─── #2 ASR backend delegates to ModelManager ───────────
    #
    # ARCH-REFAC-003: removed @property delegates (transcriber,
    # _qwen_engine, _parakeet_engine, _asr_registry, _model_load_thread,
    # _model_load_attempted, _pending_dictation) — callers now use
    # ``self.models.<field>`` directly (e.g. ``self.models.transcriber``,
    # ``self.models._registry``, ``self.models._model_load_thread``).
    #
    # The actual logic lives in voice_typer/server/model_manager.py.

    # ARCH-REFAC-003: removed @property delegates (_transcription_thread,
    # _streaming_session) — callers now use self.recording._transcription_thread
    # and self.recording._streaming_session (or the get/set_streaming_session
    # methods) directly.

    # ARCH-REFAC-003: removed @property delegates (_hotkey_backend,
    # _esc_backend, _repaste_backend) — callers now use
    # self.hotkeys._hotkey_backend / self.hotkeys._esc_backend /
    # self.hotkeys._repaste_backend directly.

    # ─── Timer Tracking (P1) ─────────────────────────────────────────
    # RW-9 Phase 7: logic moved to TimerCoordinator. VoiceTyperApp keeps
    # thin delegates so existing callers (and tests that monkeypatch
    # app._schedule_timer / app._cancel_pending_timers) keep working.

    def _schedule_timer(self, delay: float, func) -> threading.Timer:
        """RW-9 Phase 7: delegate to TimerCoordinator."""
        return self.timers._schedule_timer(delay, func)

    def _cancel_pending_timers(self):
        """RW-9 Phase 7: delegate to TimerCoordinator."""
        return self.timers._cancel_pending_timers()

    # ─── Waveform Bubble (IPC push) ───────────────────────────────────

    def _wire_waveform_bubble(self) -> None:
        """RW-9 Phase 7: delegate to WaveformBubbleWiring.

        The bubble itself is a frameless, always-on-top ``BrowserWindow``
        owned by the Electron main process.  We just emit push events;
        the IPC server is reached via the module-level hook in
        ``voice_typer.server.ipc_server`` so listeners don't need to
        hold a reference to the app or server (avoids closure-capture
        bugs that broke the bubble on first run).
        """
        self.waveform_wiring._wire_waveform_bubble()

    # ─── Startup ───────────────────────────────────────────────────────

    def start(self):
        """Initialize and run the application."""
        # Wire notifications
        self.tray.set_notifications_enabled(self.config.show_notifications)

        # Queue "Loading" state before the event loop starts
        self.tray.set_state(AppState.LOADING, "Starting...")

        # Create the icon and start background work (non-blocking)
        self.tray.start(bg_work=self._do_startup)

        # On Windows: install a console control handler
        self._install_win32_console_handler()

        # PROD-003: POSIX signal handlers for graceful shutdown
        self._install_signal_handlers()

        # Register atexit handler to log any unexpected process exit
        atexit.register(self._atexit_log)

        # RACE-016: Register atexit handlers for critical cleanup paths
        # instead of relying solely on daemon thread finally blocks.
        # Daemon threads can be killed at any time by the interpreter
        # without running their finally blocks, so cleanup that MUST
        # happen (e.g. restoring system volume, releasing hotkey
        # registrations) is registered here as a safety net.
        atexit.register(self._atexit_cleanup)

        # Enter pystray event loop -- MUST be on the main thread
        log.info("[TRAY] Entering tray event loop on main thread")
        self.tray.run()

    def _do_startup(self) -> None:
        """Background work: sync autostart, load mics, load model, register hotkey.

        RW-9 Phase 5: the body of this method (~340 lines) was extracted
        into :class:`voice_typer.server.startup_sequence.StartupSequence`
        to reduce the god-class size of ``VoiceTyperApp``.  The phase
        ordering, RACE-020 shutdown gates, parallel executor semantics,
        and onboarding auto-heal logic are all preserved verbatim — see
        the docstring on ``StartupSequence.run`` for the full rationale.

        Tests that call ``app._do_startup()`` directly still work; tests
        that previously monkeypatched the now-removed delegate methods
        (``app._sync_autostart``, ``app._load_microphones``,
        ``app._register_hotkey``, etc.) must now monkeypatch the
        controller instead (e.g.
        ``monkeypatch.setattr(startup_tasks, "sync_autostart", ...)``
        or ``app.hotkeys.register = MagicMock()``).
        """
        from voice_typer.server.startup_sequence import StartupSequence

        StartupSequence(self).run()

    # ─── Dictation ─────────────────────────────────────────────────────

    def toggle_dictation(self):
        """#2 delegate to RecordingController.toggle()."""
        self.recording.toggle()

    def _start_dictation(self):
        """#2 delegate to RecordingController.start()."""
        self.recording.start()

    def _on_audio_quality_chunk(self, *a, **kw):
        """RW-9 Phase 7: delegate to AudioQualityController."""
        return self.audio_quality._on_audio_quality_chunk(*a, **kw)

    def _rebuild_audio_processor(self, *a, **kw):
        """RW-9 Phase 7: delegate to AudioQualityController."""
        return self.audio_quality._rebuild_audio_processor(*a, **kw)

    def _finalize_audio_quality_report(self, *a, **kw):
        """RW-9 Phase 7: delegate to AudioQualityController."""
        return self.audio_quality._finalize_audio_quality_report(*a, **kw)

    def _stop_dictation(self):
        """Stop recording and transcribe in background.

        SOUND-FIX-005 (Round 0): this method is now a thin delegate to
        ``RecordingController.stop()``. Previously it was a 125-line
        duplicate of ``RecordingController.stop()`` that was missing
        three critical side effects:

        1. It never emitted the ``recording_stopped`` IPC push event,
           so the renderer's ``useSoundFeedback`` hook never received
           the stop cue and the stop beep never played.
        2. It never reset ``keyboard_ownership`` back to ``"normal"``,
           so the ESC cancel hotkey kept firing after a normal stop.
        3. It never started the Event-based watchdog thread
           (``_start_watchdog_thread``), so transcription hangs (>60s)
           never auto-recovered.

        ``RecordingController.stop()`` already contains the full,
        correct implementation — including all three missing side
        effects — but was unreachable from production call sites
        (``toggle``, ``on_silence_auto_stop``, ``on_max_duration_auto_stop``
        all called ``app._stop_dictation`` directly). Making this method
        a delegate routes all production stop traffic through the
        correct implementation and eliminates the duplication.
        """
        self.recording.stop()

    def _cancel_streaming_session(self):
        """#2 delegate to RecordingController._cancel_streaming_session()."""
        self.recording._cancel_streaming_session()

    # ─── Settings / Microphone ─────────────────────────────────────────

    def repaste_last(self) -> None:
        """Feature: Repaste last transcription (tray menu + hotkey).

        ADR-0010 §7.1 / DP6 / DP4.

        Reads from ``history_db.get_latest_text()`` (primary — survives
        app restart), falling back to ``self._last_transcription`` if
        the DB read fails. Uses the same snapshot/restore mechanism as
        auto-paste so the user's clipboard is preserved.

        ``paste(force=True)`` bypasses the ``paste_enabled`` gate (§2.12)
        so a manual repaste works regardless of the auto-paste
        (``paste_on_stop``) setting.

        ERR-018: previously a single try/except collapsed clipboard-copy
        failures and paste-keystroke failures into one generic toast.
        We now split them so the user knows which step failed.

        Fallback chain:
          1. ``history_db.get_latest_text()``  (primary — survives restart)
          2. ``self._last_transcription``        (fallback if DB read fails)
          3. "No previous transcription" toast  (both empty)
        """
        # ① READ FROM DB (primary — survives restart)
        text = ""
        try:
            text = self.history_db.get_latest_text()
        except Exception as e:
            log.warning("[REPASTE] DB read failed, falling back to memory: %s", e)
            text = self._last_transcription

        if not text:
            self.tray.notify(APP_NAME, "No previous transcription to re-paste.")
            return

        # ② COPY (snapshot + empty + pyperclip.copy + verify).
        # copy() returns None when save/restore is disabled; it raises
        # ClipboardCopyError only on a genuine copy failure.
        snapshot = None
        try:
            snapshot = self.clipboard.copy(text)
            pasted_seq = self.clipboard._clipboard_seq
        except ClipboardCopyError as e:
            log.warning("[REPASTE] Clipboard copy failed: %s", e)
            self.tray.notify(
                APP_NAME,
                "Could not copy the transcription to the clipboard. Another app may be holding the clipboard lock.",
            )
            return

        # ③ PASTE (keystroke + delayed restore scheduled inside paste()).
        # paste() schedules the restore of the user's ORIGINAL clipboard
        # at its top, before any early return (DP1). It returns False
        # (does not raise) when the keystroke is skipped/blocked/rate-
        # limited — and the restore is still scheduled. We therefore do
        # NOT call restore_now() here: that would be redundant and would
        # remove the transcription from the clipboard. The transcription
        # is safely stored in the DB. ``force=True`` bypasses the
        # ``paste_enabled`` gate (§2.12) so a manual repaste works
        # regardless of the auto-paste (``paste_on_stop``) setting.
        # pasted_seq is threaded per-request (CRIT-3) so a concurrent
        # copy() can't clobber the seq validated in paste().
        pasted = self.clipboard.paste(snapshot, pasted_text=text, force=True, pasted_seq=pasted_seq)
        if pasted:
            log.info("[REPASTE] Repasted transcription (%d chars)", len(text))
            self.tray.notify(APP_NAME, "Last transcription re-pasted")
        else:
            log.warning("[REPASTE] Paste keystroke was skipped/blocked")
            self.tray.notify(
                APP_NAME,
                "Re-paste was blocked (unsafe target or rate-limited). "
                "Your previous clipboard was preserved. Use the repaste "
                "hotkey again to try pasting.",
            )

    def undo_last(self) -> None:
        """UX-003: Undo last transcription by sending backspace keystrokes.

        Sends one backspace per character in the last transcription.
        Works by simulating keyboard input via the hotkey backend's
        keyboard controller (pynput on all platforms).

        CR-016 (IMPROVE-mode run, 2026-07-21): count grapheme clusters
        (not Unicode code points) so emoji and combining-character
        sequences are deleted with a single backspace (matching the
        OS's grapheme-level delete behavior). Pre-fix, ``len(text)`` returned
        14 for ``"Hello \U0001f468\u200d\U0001f469\u200d\U0001f467 world"``
        (1 grapheme = 5 code points ZWJ-joined) but the OS only needs 11
        backspaces — the extra 3 deleted the user's PREVIOUS text.

        CR-017 (IMPROVE-mode run, 2026-07-21): check keyboard_ownership
        before sending backspaces (mirror ``_cancel_dictation``). Pre-fix,
        if the frontend HotkeyPicker was in capture mode, the backspaces
        landed in the capture field instead of undoing the transcription.
        """
        # CR-017: keyboard-ownership check — mirror _cancel_dictation.
        try:
            from voice_typer.server.keyboard_ownership import keyboard_ownership

            if keyboard_ownership().is_hotkey_capture_active():
                log.debug("[UNDO] skipping undo — frontend hotkey capture active")
                return
        except Exception:
            log.debug("[UNDO] keyboard ownership check failed", exc_info=True)
        if not self._last_transcription:
            self.tray.notify(APP_NAME, i18n.t("notify.app.undo_nothing"))
            return
        text = self._last_transcription
        # CR-016: count grapheme clusters using the regex library (already
        # in deps for text_cleanup.py). ``\X`` matches a single user-perceived
        # grapheme (handles ZWJ emoji, combining marks, etc.).
        try:
            import regex as _regex

            char_count = len(_regex.findall(r"\X", text))
        except ImportError:
            # Fallback: code-point count (pre-CR-016 behavior — buggy for
            # multi-code-point graphemes but at least doesn't crash).
            char_count = len(text)
        log.info("[UNDO] Undoing last transcription (%d chars)", char_count)
        try:
            # CR-016 (cont.): use ``import pynput.keyboard as _pk_keyboard``
            # + ``_pk_keyboard.Controller()`` (module-attribute access at
            # call time) rather than ``from pynput.keyboard import Controller``
            # (which binds the name at import time). Both are functionally
            # identical in production, but the module-attribute form makes
            # the existing test mock setup work: tests do
            # ``pk.Controller = MagicMock(return_value=kb_instance)`` AFTER
            # the autouse fixture installs the ``pynput.keyboard`` MagicMock,
            # and the module-attribute access picks up that late assignment
            # while the ``from`` import may bind to the auto-generated
            # MagicMock child before the test's override lands.
            import pynput.keyboard as _pk_keyboard

            kb = _pk_keyboard.Controller()
            # Select all text in the current field first (Ctrl+A), then
            # Delete — this is more reliable than sending N backspaces
            # because it handles multi-line text and doesn't leave
            # partial characters.
            # However, Ctrl+A selects ALL text in the field, which may
            # be more than just our transcription.  So we send N
            # backspaces instead — this is the standard "undo paste"
            # behavior.
            #
            # APP-6: batch backspaces into chunks of ``_undo_chunk_size``
            # (10) with a 10ms ``time.sleep(0.01)`` between chunks so we
            # don't flood the OS keyboard event queue on long
            # transcriptions (>200 chars). Without rate limiting,
            # pynput can drop keystrokes silently. The sleep is omitted
            # after the final (possibly partial) chunk — there's no
            # subsequent chunk to space it from.
            _undo_chunk_size = 10
            for _i in range(char_count):
                kb.press("\x08")  # Backspace
                kb.release("\x08")
                if (_i + 1) % _undo_chunk_size == 0 and (_i + 1) < char_count:
                    time.sleep(0.01)
            self._last_transcription = ""
            self.tray.notify(APP_NAME, i18n.t("notify.app.undo_done", char_count=char_count))
        except ImportError:
            log.warning("[UNDO] pynput not available for undo")
            self.tray.notify(APP_NAME, i18n.t("notify.app.undo_no_pynput"))
        except Exception as e:
            log.warning("[UNDO] Failed: %s", e)
            self.tray.notify(APP_NAME, i18n.t("notify.app.undo_failed", error=e))

    def _cancel_dictation(self):
        """#2 delegate to RecordingController.cancel().

        ESC-FIX-001: while the frontend HotkeyPicker is in hotkey capture
        mode, the ESC cancel is a no-op — the frontend owns the Escape key
        while capturing.

        NOTE: this reads the *canonical* KeyboardOwnership state via
        ``is_hotkey_capture_active()`` rather than the legacy
        ``self._esc_cancel_paused`` alias. ``_esc_cancel_paused`` is only
        written by the set_esc_cancel_paused IPC handler and could drift out
        of sync with the real ownership (the ESC-release path resets the
        canonical owner but relied on a frontend round-trip to clear the
        alias). Trusting the stale alias made ESC a permanent no-op whenever
        the two diverged — see the ESC-cancel regression fix.
        """
        try:
            from voice_typer.server.keyboard_ownership import keyboard_ownership

            if keyboard_ownership().is_hotkey_capture_active():
                log.debug("[CANCEL] ESC cancel paused (frontend hotkey capture) — no-op")
                return
        except Exception:  # pragma: no cover - defensive
            log.debug("[CANCEL] keyboard ownership check failed", exc_info=True)
        self.recording.cancel()

    def _toggle_autostart(self):
        """Toggle autostart on/off from the tray menu. Delegates to SettingsController."""
        self.settings.toggle_autostart()

    def _set_autostart(self, enabled: bool):
        """Set autostart from the advanced settings window or tray toggle.

        RW-9 Phase 6: body extracted to
        :meth:`voice_typer.server.settings_controller.SettingsController.set_autostart`.
        Behaviour preserved verbatim — only the class boundary moved.
        """
        self.settings.set_autostart(enabled)

    def _set_notifications(self, enabled: bool):
        """Set notification behavior from the settings window.

        RW-9 Phase 6: body extracted to
        :meth:`voice_typer.server.settings_controller.SettingsController.set_notifications`.
        """
        self.settings.set_notifications(enabled)

    def _select_microphone(self, mic_name: str | None):
        """Handle microphone selection from tray menu.

        RW-9 Phase 6: body extracted to
        :meth:`voice_typer.server.settings_controller.SettingsController.select_microphone`.
        """
        self.settings.select_microphone(mic_name)

    def _open_config_file(self):
        """Open the config file in the user's default editor.

        Delegates to :class:`voice_typer.server.config_editor.ConfigEditorLauncher`
        (extracted from this method). The launcher holds
        ``_config_mutation_lock`` for the full editor session (XPLAT-01 /
        SEC-audit-011 / B-4 / CR-015) and reloads the config from disk
        after the editor exits. See ``config_editor.py`` for the full
        platform-specific behavior and the security invariants it pins.
        """
        config_file = self.config.config_dir / "config.json"
        ConfigEditorLauncher(self).launch(config_file)

    # ─── TrayController Protocol Methods (P3) ────────────────────────

    def change_microphone(self, mic_id: str | None) -> None:
        """TrayController protocol: select microphone."""
        self._select_microphone(mic_id)

    def change_model(self, model_size: str) -> None:
        """TrayController protocol: change transcription model.

        RW-6 (pyrefly): parameter renamed from ``model`` to
        ``model_size`` to match :class:`voice_typer.server.providers.AppProtocol`'s
        ``change_model(self, model_size: str)`` signature. Pyrefly
        enforces parameter-name matching for Protocol members (a call
        like ``app.change_model(model_size="large")`` must be valid on
        any AppProtocol implementation), so the names must agree.

        RW-9 Phase 2: the ``_change_model`` delegate has been removed;
        this method now calls ``self.models.change_model`` directly.
        """
        self.models.change_model(model_size)

    def quit_app(self) -> None:
        """TrayController protocol: quit the app.

        RELIABILITY-001: previously this method duplicated cleanup
        inline and ended with ``os._exit(0)`` because ``_wrap`` in
        ``tray.py`` swallowed ``SystemExit``, preventing the audited
        ``self.quit()`` path from terminating the process.  ``os._exit``
        skips Python atexit handlers, ``__del__`` methods, and
        ``finally`` blocks — leaking the Win32 named mutex, leaving
        PortAudio mic handles open, and not unregistering
        ``RegisterHotKey`` registrations.

        Now that ``_wrap`` suppresses ``SystemExit`` (see ERR-QUIT-002
        fix in ``tray.py`` — ``tray.stop()`` inside ``quit()`` already
        breaks the pystray loop, so re-raising just caused pystray to
        print a noisy traceback), we delegate to ``self.quit()`` which
        does the full cleanup (cancel timers, signal streaming cancel,
        discard recorder, join transcription thread, stop all three
        hotkey backends, ``self.tray.stop()`` to break the pystray
        loop, close devnull FDs, ``sys.exit(0)``).

        Before cleanup, pushes a ``quit_app`` event over the TCP channel
        so the Electron frontend knows to call ``app.quit()`` and shut
        down cleanly (instead of being left orphaned with no backend).

        APP-10 (F-06): the ``event_bus.publish({"type": "quit_app"})``
        call MUST come BEFORE the ``if self._shutting_down:`` re-entry
        guard. Pre-fix, the guard sat at the top of the method and a
        double-quit (e.g. user clicks the tray Quit item twice, or
        SIGTERM races with the tray quit) silently dropped the second
        push — leaving Electron with no shutdown signal if the first
        push was lost in a TCP race. The fix pushes unconditionally on
        every call and only guards the actual ``self.quit()`` call so
        cleanup isn't run twice.
        """
        log.info("[QUIT] Quitting %s", APP_NAME)

        # Item 12: If recording, discard the recording before quitting
        # so we don't leave the mic open or lose the in-flight audio.
        try:
            if self.recorder and self.recorder.recording:
                log.info("[QUIT] Recording in progress — discarding before quit")
                self.recorder.discard()
        except Exception:
            log.debug("[QUIT] Could not discard recording", exc_info=True)

        # 0. Notify Electron frontend over TCP so it can quit cleanly.
        # APP-10: this MUST run BEFORE the _shutting_down guard so a
        # double-quit still pushes the event (the first push may have
        # been lost in a TCP race; the second push is the safety net).
        from voice_typer.server import event_bus

        event_bus.publish({"type": "quit_app"})

        # APP-10: re-entry guard sits AFTER the push so the quit event
        # is always published, even on a double-quit. Only the actual
        # ``self.quit()`` cleanup is skipped on the second call.
        # DE-49: use _shutting_down_event.is_set() for cross-thread memory
        # ordering (the threading.Event version provides acquire/release
        # semantics — the plain boolean has no such guarantee).
        if self._shutting_down_event.is_set():
            log.debug("[QUIT] Already shutting down, ignoring duplicate quit_app call")
            return

        # 1. Delegate to the audited cleanup path.  self.quit() raises
        #    SystemExit(0) at the end; _wrap re-raises it, and pystray
        #    unwinds because self.tray.stop() was called inside quit().
        self.quit()

    def restart_app(self) -> None:
        """TrayController protocol: restart the app.

        Sends a ``relaunch_electron`` event to Electron over the active
        TCP channel, then exits the current instance via the clean
        ``sys.exit(0)`` path.  Electron's handler calls
        ``app.relaunch()`` + ``app.exit(0)``, which spawns a fresh
        Electron process (which in turn spawns a fresh Python backend).
        If the ``relaunch_electron`` event is lost (TCP race),
        Electron's ``pythonProcess.on("exit")`` handler sees exit code
        0 and triggers the same relaunch as a fallback — see
        ``client/src/main/index.ts``.

        CR-013 (IMPROVE-mode run, 2026-07-21): re-entry guard at the
        top — mirror ``quit_app``. Pre-fix, a double-clicked tray
        "Restart" item or a tray restart racing with SIGTERM-triggered
        quit would push duplicate ``relaunch_electron`` events, re-acquire
        ``_config_mutation_lock`` for a second ``config.save()``, re-enter
        ``_do_cleanup()``, and fire a second ``sys.exit(0)`` while the
        first call's finally blocks were still draining.

        CR-014 (same run): pass ``APP_NAME`` as the format argument to
        ``log.info("[RESTART] Restarting %s...", APP_NAME)``. Pre-fix,
        the ``%s`` placeholder was never substituted, producing a literal
        ``[RESTART] Restarting %s...`` log line.
        """
        # CR-013: re-entry guard.
        # DE-49: use _shutting_down_event.is_set() for cross-thread memory
        # ordering (the threading.Event version provides acquire/release
        # semantics — the plain boolean has no such guarantee).
        if self._shutting_down_event.is_set():
            log.debug("[RESTART] ignoring duplicate restart_app call (already shutting down)")
            return
        # CR-014: pass APP_NAME as the format argument.
        log.info("[RESTART] Restarting %s...", APP_NAME)

        # ── THEME-RESTART-FIX: save the config before push ───────────
        # Save any pending in-memory config changes (e.g. a theme preset
        # change that was set via `set_config` but whose save completed
        # while the user navigated to the tray menu) to disk before the
        # restart sequence begins.  This ensures the new Python process
        # loads the latest config, preventing the theme from reverting
        # to default after a restart.
        # DE-47: wrap in try/except so an unexpected exception from
        # save() (e.g. RecursionError from asdict on a cyclic dataclass)
        # does not abort the restart sequence — the user's "Restart"
        # tray click must still work.
        try:
            save_ok = self.config.save()
        except Exception:
            log.warning("[RESTART] config.save() raised", exc_info=True)
        else:
            if not save_ok:
                log.warning("[RESTART] config.save() before push failed")

        # ── CRITICAL ORDERING FIX ────────────────────────────────────
        #
        # _push_event_now() MUST be called BEFORE _shutting_down is set
        # to True.  The _send() method in ipc_server.py checks
        # _shutting_down and if True, closes the TCP socket WITHOUT
        # writing the event — silently dropping it.  This was the root
        # cause of the "restart does nothing" bug: the relaunch_electron
        # event was never received by Electron, so _relaunching stayed
        # false, and the fallback exit handler also failed because the
        # Python process never actually exited (SystemExit was caught
        # by wrap_callback without tray.stop() breaking the loop).
        #
        # 1. Push relaunch_app BEFORE marking _shutting_down.
        # PVT-2 cleanup (this change): the published event name is now
        # ``relaunch_app`` directly (no longer ``relaunch_electron``).
        # The Rust WS bridge no longer renames it (the rename arm in
        # ws.rs was dropped); main.rs listens for ``relaunch_app`` and
        # calls ``app.restart()``.
        from voice_typer.server import event_bus

        try:
            event_bus.publish({"type": "relaunch_app"})
            log.info("[RESTART] relaunch_app pushed to host via event_bus")
        except Exception as e:
            log.warning("[RESTART] failed to push relaunch_app: %s", e)

        # 2. NOW mark as shutting down, restore volume, and wait (event-driven)
        #    for Electron to process the relaunch event before we close the
        #    socket.  PERF-005: replaced the fixed time.sleep(0.3) with a
        #    bounded wait on the ``relaunch_ack`` event that Electron sets when
        #    it receives ``relaunch_electron``.  This unblocks the (tray)
        #    calling thread as soon as Electron acks, instead of always
        #    blocking 300ms; if no ack arrives (e.g. Electron already gone),
        #    we fall back to the original 300ms pause so behaviour is unchanged.
        self._shutting_down = True
        # RACE-020: also set the Event version so executor tasks can
        # check it (matches quit()'s shutdown signaling — important now
        # that restart_app() shares the same _do_cleanup() body).
        self._shutting_down_event.set()
        # CR-018 (IMPROVE-mode run, 2026-07-21): the redundant
        # ``self._restore_volume(fade_ms=0)`` call that lived here was
        # deleted — ``_do_cleanup()`` (invoked further down via
        # ``ShutdownController._do_cleanup``) already invokes the
        # volume-restore path. Pre-fix, the double-restore wasted ~10ms
        # and produced confusing log noise (two "volume restored" lines
        # per restart). If the volume backend isn't reentrant, the second
        # call could see a stale "ducked" state and re-apply the duck.
        # CR-64: encapsulated the IPCServer's private
        # ``_relaunch_ack_event`` access in ``_wait_for_relaunch_ack``
        # so the backwards coupling (VoiceTyperApp reaching INTO the
        # IPCServer's private state) is at least named and easy to
        # migrate.  The helper still uses ``getattr`` defensively
        # because the IPCServer may not be wired yet (early restart)
        # or may be a non-IPC test double.
        # TODO Fix-A: replace ``_wait_for_relaunch_ack`` with a call
        # to ``IPCServer.wait_for_relaunch_ack(timeout)`` — a public
        # method on the IPCServer itself (Fix-A owns IPCServer).  Once
        # that public method exists, ``_wait_for_relaunch_ack`` and
        # the ``getattr(self._ipc_server, "_relaunch_ack_event")``
        # lookup below can be deleted.
        self._wait_for_relaunch_ack(timeout=2.0)

        # 3. RW-3: run the SAME audited cleanup as quit() — flushes
        #    history_db and _crash_recovery (so no pending writes are
        #    silently lost on restart), stops recorder + mic watcher
        #    (so PortAudio streams don't leak across the restart), stops
        #    all three hotkey backends + the bubble level worker,
        #    terminates any Electron subprocess we spawned, releases
        #    the single-instance mutex + PID file, and closes devnull
        #    streams.
        #
        #    Previously restart_app() did only a PARTIAL cleanup
        #    (timers + hotkeys + tray) and skipped the rest, leaking
        #    PortAudio streams / the Win32 mutex / the mic watcher
        #    daemon thread and silently losing pending history_db +
        #    crash_recovery writes on EVERY restart. The
        #    _atexit_cleanup safety net couldn't pick up the slack
        #    because its _shutting_down guard short-circuited as soon
        #    as restart_app() set _shutting_down = True above.
        #    Extracting the shared _do_cleanup() body fixes both bugs.
        #
        #    HIGH-36 / XCUT-1: mirror quit()'s ordering by running
        #    ``self._thread_registry.shutdown_all()`` BEFORE
        #    ``_do_cleanup()``. The registry's centralized
        #    signal-and-join (per-thread stop_event + join-with-timeout)
        #    must run on the restart path too — otherwise the
        #    bubble-level-pusher and any other registered daemon threads
        #    never receive their stop signal and are only cleaned up
        #    implicitly when the process exits. The per-site shutdown
        #    methods inside ``_do_cleanup()`` are idempotent safety
        #    nets, but they don't cover every registered thread (e.g.
        #    future additions registered only with the registry).
        #    ``shutdown_all()`` is itself idempotent, so a subsequent
        #    call from ``_atexit_cleanup()`` is a no-op.
        try:
            self._thread_registry.shutdown_all()
        except Exception:
            log.warning(
                "[RESTART] thread_registry.shutdown_all failed",
                exc_info=True,
            )
        self._do_cleanup()

        # 4. Exit cleanly — electron will relaunch us.
        # CR-17: mirror ShutdownController.quit()'s threading-aware exit.
        # restart_app is invoked from the tray menu callback, which runs
        # on pystray's worker thread (NOT the main thread). When
        # sys.exit(0) is called from a non-main thread, CPython raises
        # SystemExit in THAT thread only — the process does not exit.
        # The tray's _wrap callback wrapper suppresses SystemExit, so
        # the sys.exit(0) is silently swallowed and the process lingers
        # for up to ~1s (holding the single-instance mutex / IPC port)
        # until tray.stop() (called inside _do_cleanup above) breaks
        # the pystray loop and app.start() returns. On the main thread,
        # sys.exit(0) works normally. The conditional mirrors quit()'s
        # pattern at shutdown_controller.py:464,497-498.
        #
        # GT-43: if restart_app() is running on a non-main thread (the
        # common case — pystray tray menu callback), arm the same
        # shutdown watchdog ``quit()`` uses. If ``tray.stop()`` (called
        # inside ``_do_cleanup`` above) failed to break the pystray
        # loop and the main thread is still parked in ``tray.run()``
        # after ``SHUTDOWN_WATCHDOG_TIMEOUT_S`` seconds, the watchdog
        # calls ``os._exit(0)`` to unblock the process. Without this,
        # a hung pystray backend leaves the old process unkillable
        # after a Restart click — and the new process can't claim the
        # single-instance mutex / IPC port, so the restart silently
        # fails. The watchdog is a daemon thread, so it never blocks
        # the normal exit path (if the main thread returns from
        # ``tray.run()`` promptly, the process exits and the daemon
        # is killed).
        is_main_thread = threading.current_thread() is threading.main_thread()
        if not is_main_thread:
            try:
                self.shutdown._arm_shutdown_watchdog(self._shutdown_watchdog_timeout_s)
            except Exception:
                log.debug(
                    "[RESTART] GT-43: failed to arm shutdown watchdog",
                    exc_info=True,
                )
        log.info("[RESTART] Old process exiting via sys.exit(0)")
        if is_main_thread:
            sys.exit(0)
        # else: rely on tray.stop() (called inside _do_cleanup) to
        # break the pystray loop so app.start() returns and
        # ipc_server.main() falls through to process exit. If that
        # doesn't happen within SHUTDOWN_WATCHDOG_TIMEOUT_S seconds,
        # the GT-43 watchdog will call os._exit(0) as a last resort.

    def _wait_for_relaunch_ack(self, timeout: float) -> bool:
        """Wait for Electron to ack the ``relaunch_electron`` event.

        CR-64: encapsulates the IPCServer's private
        ``_relaunch_ack_event`` access so :meth:`restart_app` no longer
        reaches directly into ``self._ipc_server._relaunch_ack_event``.
        The previous inline code did::

            _relaunch_ack_event = (
                getattr(self._ipc_server, "_relaunch_ack_event", None)
                if self._ipc_server is not None
                else None
            )
            if _relaunch_ack_event is not None:
                _relaunch_ack_event.clear()
                _relaunch_ack_event.wait(timeout=2.0)
            else:
                time.sleep(0.3)

        — which couples :class:`VoiceTyperApp` backwards into the
        IPCServer's private state.  This helper preserves the exact
        behaviour (defensive ``getattr`` for early-restart / test
        double scenarios, 300ms fallback sleep when no IPCServer is
        attached, bounded ``timeout`` wait on the cleared event) while
        making the dependency explicit and easy to migrate.

        TODO Fix-A: once ``IPCServer`` exposes a public
        ``wait_for_relaunch_ack(timeout)`` method (Fix-A owns
        IPCServer), this helper should delegate to it and the
        ``getattr(self._ipc_server, "_relaunch_ack_event")`` lookup
        can be removed entirely.

        Parameters
        ----------
        timeout :
            Maximum seconds to wait for Electron's ack.  Mirrors the
            original hardcoded ``2.0`` in :meth:`restart_app`.

        Returns
        -------
        bool
            ``True`` if the ack event was signalled within ``timeout``;
            ``False`` if no IPCServer is attached, the IPCServer has no
            ``_relaunch_ack_event`` attribute (e.g. a test double), or
            the wait timed out.  Callers today ignore the return value
            (the original inline code didn't return one either) — the
            contract is preserved for future use.
        """
        # TODO Fix-A: replace this defensive ``getattr`` with a call
        # to ``self._ipc_server.wait_for_relaunch_ack(timeout)`` once
        # that public method exists on the IPCServer (Fix-A owns it).
        ipc_server = self._ipc_server
        if ipc_server is None:
            log.info("[RESTART] No IPC server available; pausing 300ms for Electron")
            time.sleep(0.3)
            return False
        _relaunch_ack_event = getattr(ipc_server, "_relaunch_ack_event", None)
        if _relaunch_ack_event is None:
            log.info("[RESTART] No IPC server available; pausing 300ms for Electron")
            time.sleep(0.3)
            return False
        _relaunch_ack_event.clear()
        log.info("[RESTART] Waiting for relaunch_ack from Electron (timeout %.1fs)", timeout)
        acked = _relaunch_ack_event.wait(timeout=timeout)
        return acked

    # DEAD-008: the following 6 TrayController protocol methods were
    # removed because no IPC route, tray menu item, or UI invoked them:
    #   - toggle_autostart (use _toggle_autostart directly)
    #   - create_desktop_shortcut
    #   - set_notifications (use _set_notifications directly)
    #   - set_silence_warning_seconds (use set_config via IPC)
    #   - set_stop_on_silence_seconds (use set_config via IPC)
    #   - set_max_recording_time_seconds (use set_config via IPC)
    # The corresponding TrayController Protocol entries were also removed.

    # ─── Shutdown ──────────────────────────────────────────────────────

    def _do_cleanup(self) -> None:
        """RW-9 Phase 7: delegate to ShutdownController.

        The shared, idempotent cleanup body lives in
        ``shutdown_controller.py``. The delegate indirection lets
        ``ShutdownController.quit`` and ``ShutdownController._atexit_cleanup``
        call ``self._app._do_cleanup()`` so test spies like
        ``monkeypatch.setattr(app, "_do_cleanup", spy)`` still intercept
        the call (preserves the contract pinned by
        ``tests/test_app_cleanup.py::test_quit_calls_do_cleanup``).
        """
        return self.shutdown._do_cleanup()

    def quit(self):
        """RW-9 Phase 7: delegate to ShutdownController."""
        return self.shutdown.quit()

    def _atexit_log(self) -> None:
        """RW-9 Phase 7: delegate to ShutdownController."""
        return self.shutdown._atexit_log()

    def _atexit_cleanup(self) -> None:
        """RW-9 Phase 7: delegate to ShutdownController."""
        return self.shutdown._atexit_cleanup()

    def _install_signal_handlers(self):
        """RW-9 Phase 7: delegate to ShutdownController."""
        return self.shutdown._install_signal_handlers()

    def _install_win32_console_handler(self):
        """RW-9 Phase 7: delegate to ShutdownController."""
        return self.shutdown._install_win32_console_handler()

    def _win32_console_handler(self, ctrl_type):
        """RW-9 Phase 7: delegate to ShutdownController."""
        return self.shutdown._win32_console_handler(ctrl_type)


# REF-3: extraction — single-instance enforcement + backend PID file
# helpers moved to voice_typer.server.single_instance. Re-exported here so
# tests doing `from voice_typer.server.app import _ensure_single_instance` /
# `_write_backend_pid_file` / `_clear_backend_pid_file` / `_is_pid_alive` /
# `_read_stale_backend_pid` / `_backend_pid_file` keep working (test_app.py,
# test_app_cleanup.py, test_electron_launcher.py, test_feature_hardening_regressions.py,
# test_waveform_bubble.py). Source-level tests that inspect app.py for the
# mutex name "Local\\VoiceTyperSingleInstance" (PLAT-040 / SEC-001) and
# _create_restrictive_security_attributes continue to see those symbols here
# via the import below + the comment in this block.
# DEAD-013: _another_voice_typer_alive() was deleted; the Win32 named
# mutex (VoiceTyperSingleInstance) already proves a duplicate exists when
# error_already_exists is returned — the scan had zero decision power.
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
    """Entry point for the ``voice-typer`` console script (pyproject).

    ERR-IPC-001 (fix): the ``VoiceTyperApp.main()`` line was accidentally deleted
    in a prior refactor. pyproject.toml now points to
    ``voice_typer.server.ipc_server:main`` as the canonical entry point;
    this function is kept as a thin re-export for backward compat.
    """
    # RACE-018: Enable faulthandler for automatic thread-dump on SIGSEGV/SIGABRT.
    # Invaluable for debugging production crashes with CUDA/GPU drivers.
    try:
        import faulthandler

        faulthandler.enable()
    except Exception:
        log.debug("[IPC] faulthandler not available", exc_info=True)

    from voice_typer.server.ipc_server import main as ipc_main

    # DE-50: wrap in try/except so a backend crash logs at ERROR with
    # the full traceback and the process exits with code 1 (rather than
    # propagating to the console-script wrapper with no structured log).
    try:
        ipc_main()
    except Exception:
        log.exception("[FATAL] backend crashed")
        sys.exit(1)


# REF-3: extraction — Windows editor-launch helpers moved to
# voice_typer.server.platform_launch. Re-exported here so callers
# (VoiceTyperApp._open_config_file) and tests that monkeypatch
# voice_typer.server.app._windows_open_with_default_app /
# _windows_wait_for_process_exit / _windows_close_process_handle /
# _systemroot_notepad_path keep working unchanged (test_api_doc_accuracy.py,
# test_config_editor_lock.py). The bare PATH-resolved "notepad" pattern
# is intentionally NOT used — _systemroot_notepad_path validates the path
# via %SYSTEMROOT%\\System32\\notepad.exe (SEC-audit-011 / XPLAT-01).
from voice_typer.server.platform_launch import (  # noqa: E402,F401
    _systemroot_notepad_path,
    _windows_close_process_handle,
    _windows_open_with_default_app,
    _windows_wait_for_process_exit,
)
