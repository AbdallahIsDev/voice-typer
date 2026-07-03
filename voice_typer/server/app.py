"""Main application orchestrator."""

import atexit
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
from typing import Optional

import numpy as np

# Re-exported for monkeypatch.setattr("voice_typer.server.app.X", ...) in tests
# and for runtime lookups from voice_typer.server.startup_tasks.  # ruff: noqa: F401
from voice_typer.server import task_scheduler
from voice_typer.server.audio_processor import AudioProcessor
from voice_typer.server.audio_quality import AudioQualityAnalyzer
from voice_typer.server.clipboard import ClipboardManager
from voice_typer.server.config import Config, _config_dir, _migrate_from_legacy
from voice_typer.server.crash_recovery import CrashRecovery
from voice_typer.server.duck_crash_recovery import DuckCrashRecovery
# Re-exported for monkeypatch.setattr("voice_typer.server.app.X", ...) in tests.  # ruff: noqa: F401
from voice_typer.server.hotkeys import HotkeyBackend, create_hotkey_backend
from voice_typer.server.history_db import HistoryDB
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
# CQ-029: use centralized platform helpers instead of raw sys.platform checks
from voice_typer.server.platform_utils import is_windows, is_macos, is_linux
from voice_typer.server.recording import Recorder
from voice_typer.server.settings import SettingsController, SettingsWindow
from voice_typer.server.log import (
    close_devnull_files as _close_devnull_files,
    register_devnull_file as _register_devnull_file,
)
from voice_typer.server.streaming import StreamingConfig, StreamingTranscriptionSession
from voice_typer.server.text_cleanup import clean_transcribed_text, configure_corrections
from voice_typer.server.tray import AppState, TrayIcon
from voice_typer.server.transcription import TranscriptionEngine
from voice_typer.server.volume_ducker import VolumeDucker
from voice_typer.server.waveform import WaveformBubble

log = logging.getLogger("voice_typer")








def _setup_logging():
    """Configure logging (delegates to ``log.setup_logging``).

    CQ-007: Structure overview:
      1. Redirect stdin/stdout/stderr to devnull under pythonw.exe
      2. One-time legacy config migration
      3. Generate session ID for structured logging
      4. Set up RotatingFileHandler (PROD-016)
      5. Apply session + PII redaction filters
      6. Fix stderr encoding for Unicode
      7. Optional colored stderr StreamHandler
      8. PROD-020: VOICE_TYPER_QUIET env var for reduced verbosity
    """
    from voice_typer.server.log import setup_logging as _setup_logging_shared

    # One-time migration from legacy platform config dir
    _migrate_from_legacy()

    config_dir = _config_dir()

    # Point huggingface_hub cache under .voice-typer/ instead of ~/.cache/
    os.environ.setdefault("HF_HOME", str(config_dir / "huggingface"))

    debug = os.environ.get("VOICE_TYPER_DEBUG", "").lower() in ("1", "true", "yes")
    quiet = os.environ.get("VOICE_TYPER_QUIET", "").lower() in ("1", "true", "yes")
    port_mode = "--port" in sys.argv

    _setup_logging_shared(
        config_dir,
        debug=debug,
        quiet=quiet,
        port_mode=port_mode,
    )

    # PLAT-008: validate environment variables before consuming them
    _validate_env_vars()

    # PLAT-021: detect container environments and warn about unavailable features
    from voice_typer.server.container_detect import warn_if_in_container
    warn_if_in_container()


# ─── PLAT-008: Environment variable validation ────────────────────────


def _validate_env_vars() -> None:
    """PLAT-008: Validate all consumed environment variables.

    Rejects values that don't match expected patterns. Logs warnings
    for invalid values and resets them to safe defaults.
    """
    import re

    _BOOL_VARS = {"VOICE_TYPER_QUIET", "VOICE_TYPER_DEBUG", "VOICE_TYPER_NO_TRAY", "VOICE_TYPER_STREAMING"}
    _BOOL_PATTERN = re.compile(r"^(1|0|true|false|yes|no)$", re.IGNORECASE)
    _TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")
    _PATH_PATTERN = re.compile(r'^[^\0]+$')  # no null bytes

    for var in _BOOL_VARS:
        val = os.environ.get(var)
        if val is not None and not _BOOL_PATTERN.match(val):
            log.warning(
                "[ENV] Invalid value for %s=%r -- expected boolean (1/0/true/false/yes/no). Resetting to empty.",
                var, val,
            )
            os.environ.pop(var, None)

    restart_val = os.environ.get("VOICE_TYPER_RESTART")
    if restart_val is not None and not _TOKEN_PATTERN.match(restart_val):
        log.warning(
            "[ENV] Invalid value for VOICE_TYPER_RESTART=<redacted> -- expected alphanumeric token. Resetting to empty.",
        )
        os.environ.pop("VOICE_TYPER_RESTART", None)

    config_dir = os.environ.get("VOICE_TYPER_CONFIG_DIR")
    if config_dir is not None and (not _PATH_PATTERN.match(config_dir) or len(config_dir) > 4096):
        log.warning(
            "[ENV] Invalid value for VOICE_TYPER_CONFIG_DIR=%r -- expected valid path. Resetting to empty.",
            config_dir,
        )
        os.environ.pop("VOICE_TYPER_CONFIG_DIR", None)

    ipc_token = os.environ.get("VOICE_TYPER_IPC_TOKEN")
    if ipc_token is not None and not _TOKEN_PATTERN.match(ipc_token):
        log.warning(
            "[ENV] Invalid value for VOICE_TYPER_IPC_TOKEN=<redacted> -- expected alphanumeric token. Resetting to empty.",
        )
        os.environ.pop("VOICE_TYPER_IPC_TOKEN", None)

    # SEC-audit-011: Validate SystemRoot on Windows to prevent DLL injection
    from voice_typer.server.config import _validate_systemroot
    _validate_systemroot()

    # PLAT-008: Validate HF_HOME is a valid path if set
    hf_home = os.environ.get("HF_HOME")
    if hf_home is not None and (not _PATH_PATTERN.match(hf_home) or len(hf_home) > 4096):
        log.warning(
            "[ENV] Invalid value for HF_HOME=%r -- expected valid path. Resetting to empty.",
            hf_home,
        )
        os.environ.pop("HF_HOME", None)


class VoiceTyperApp:
    """The main application."""

    def __init__(self):
        self.config = Config.load()

        # Startup banner -- first visible log, before any subsystem init
        log.info(
            "Voice Typer starting -- model=%s, hotkey=%s, mic=%s, sample_rate=%s",
            self.config.model_size, self.config.hotkey,
            self.config.microphone or "default", self.config.sample_rate,
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

        self.recorder = Recorder(self.config, audio_processor=self._audio_processor)
        # #2 (Round 9): Recording lifecycle extracted to RecordingController.
        # Owns toggle/start/stop/cancel, silence/xrun callbacks, and the
        # streaming session. The recorder's xrun threshold callback is
        # wired to RecordingController.on_xrun_threshold instead of the
        # old VoiceTyperApp._on_xrun_threshold method.
        from voice_typer.server.recording_controller import RecordingController
        self.recording: RecordingController = RecordingController(self)
        # Item 1: wire xrun threshold callback for tray notification
        self.recorder.on_xrun_threshold = self.recording.on_xrun_threshold
        # #2 (Round 9): ASR backend lifecycle extracted to ModelManager.
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

        # #2 (Round 9): Hotkey registration extracted to HotkeyDispatcher.
        # Owns the 3 hotkey backends (dictation / ESC / repaste) and the
        # register/restart logic. (ARCH-REFAC-003: the @property
        # delegates that used to mirror the 3 legacy fields
        # (_hotkey_backend, _esc_backend, _repaste_backend) on
        # VoiceTyperApp have been removed — callers now use
        # `self.hotkeys.<field>` directly.)
        from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher
        self.hotkeys: HotkeyDispatcher = HotkeyDispatcher(self)
        # #2 (Round 9): _streaming_session and _transcription_thread now
        # live in RecordingController. (ARCH-REFAC-003: the @property
        # delegates that used to mirror them on VoiceTyperApp have been
        # removed — callers now use `self.recording.<field>` directly,
        # or `self._get_streaming_session()` / `self._set_streaming_session()`.)
        self._settings_window: Optional[SettingsWindow] = None
        self._microphones: list[dict] = []
        self._busy_event = threading.Event()
        self._busy_event.set()  # SET = not busy
        self._lock = threading.Lock()
        # RACE-011: serialize Config mutations between the IPC set_config
        # handler (IPC server thread) and the deprecated tkinter
        # SettingsController.apply() path (tkinter main thread). Without
        # this lock, concurrent set_config + SettingsController.apply()
        # calls can interleave attribute writes and produce a torn
        # config state — e.g. half the fields from IPC, half from the
        # tkinter window. The lock is held for the full read-modify-save
        # sequence so each mutation sees a consistent view of the Config
        # object.
        self._config_mutation_lock = threading.RLock()

        # #2 (Round 9): _model_load_attempted / _model_load_thread /
        # _pending_dictation now live in ModelManager. (ARCH-REFAC-003:
        # the @property delegates that used to mirror them on
        # VoiceTyperApp have been removed — callers now use
        # `self.models.<field>` directly.)
        self._shutting_down = False  # True once quit() starts
        # RACE-020: threading.Event version of _shutting_down so executor
        # tasks can check it without reading the boolean (which provides
        # no memory-order guarantee across threads).
        self._shutting_down_event = threading.Event()
        # ARCH-022: _pending_timers is appended to from the tray thread,
        # the transcription thread, and the timer thread itself; the
        # `for timer in self._pending_timers` iteration in
        # _cancel_pending_timers can race with concurrent appends and
        # raise RuntimeError("list changed size during iteration").
        # Guard the list with a dedicated lock.
        self._pending_timers: list[threading.Timer] = []
        self._pending_timers_lock = threading.Lock()
        self._timer_generation: int = 0
        self._cycle_counter = 0      # monotonic counter for dictation cycles
        self._cycle_id: str = ""     # human-readable cycle id for log correlation

        # ─── P1/P2 New Feature Components ────────────────────────────
        self.history_db = HistoryDB()
        self._crash_recovery = CrashRecovery()
        # Volume ducking: reduces system volume during dictation to
        # prevent speaker output from bleeding into the microphone.
        # Crash recovery persists the pre-duck volume so a crash
        # doesn't leave the system stuck at a low volume.
        # Use _config_dir() so the crash-recovery file lives alongside
        # the rest of the user's voice-typer state (and tests can
        # monkeypatch _config_dir to point at a tmp_path).
        self._duck_crash_recovery = DuckCrashRecovery(config_dir=_config_dir())
        self._volume_ducker = VolumeDucker(
            crash_recovery=self._duck_crash_recovery,
            on_crash_restore=self._on_volume_crash_restore,
        )
        # NOTE: AudioQualityAnalyzer is now instantiated earlier in
        # __init__ (next to AudioProcessor) and wired to the processor's
        # per-chunk quality callback.  See self._audio_quality /
        # self._on_audio_quality_chunk / _finalize_audio_quality_report.
        self._waveform_bubble = WaveformBubble()
        self._wire_waveform_bubble()
        self._last_transcription: str = ""  # For repaste
        # ARCH-011: eager-init managers so config changes between
        # startup and first dictation are reflected.  Previously these
        # were lazy-init on first use, which meant a config change
        # (e.g. editing corrections.json) before the first dictation
        # was NOT picked up because the manager was created from stale
        # config.  Eager init ensures the managers see the config as
        # of __init__ time; reload() can be called later if needed.
        try:
            from voice_typer.server.templates import TemplateManager
            self._template_manager = TemplateManager()
        except Exception:
            log.debug("[INIT] TemplateManager eager-init failed")
            self._template_manager = None
        try:
            from voice_typer.server.vocabulary import VocabularyManager
            self._vocabulary_manager = VocabularyManager()
        except Exception:
            log.debug("[INIT] VocabularyManager eager-init failed")
            self._vocabulary_manager = None
        self._llm_polisher = None  # Created on first polish (needs consent check)
        self._cloud_engine = None  # Lazy-init if cloud backend selected

    # ─── Volume Ducking ────────────────────────────────────────────────

    def _on_volume_crash_restore(self, state) -> None:
        """Callback invoked when a stale duck crash-recovery file is found.

        Notifies the user that the volume was restored after a crash.
        """
        try:
            self.tray.notify(
                "Voice Typer",
                f"System volume was restored after a crash "
                f"(to {int(state.linear * 100)}%).",
            )
        except Exception:
            log.debug("[VOLUME] crash-restore notification failed", exc_info=True)

    def _duck_volume(self) -> None:
        """Duck system volume at the start of dictation.

        UX-2: the ducking behavior is now simplified:
        - Smart Duck is ALWAYS ON (merged into Auto Duck Volume)
        - Fade duration is a fixed 200ms (not user-configurable)
        - Poll interval is a fixed 500ms (not user-configurable)
        - Per-session ducking is removed (always ducks master volume
          cross-platform)
        The config fields are kept for backward compat but ignored.
        """
        if not getattr(self.config, "volume_duck_enabled", True):
            return
        try:
            # UX-2: smart duck is always on when ducking is enabled.
            self._volume_ducker.set_smart_duck_enabled(True)
            # UX-2: poll interval is a fixed 500ms (not user-configurable).
            self._volume_ducker.set_smart_duck_poll_interval(
                getattr(self.config, "volume_duck_smart_poll_interval_ms", 500)
            )
            if self._volume_ducker.initialize():
                self._volume_ducker.duck(
                    level=getattr(self.config, "volume_duck_level", 0.20),
                    fade_ms=getattr(self.config, "volume_duck_fade_ms", 200),
                    # UX-2: per-session removed — always master-volume duck.
                    per_session=False,
                )
        except Exception:
            log.debug("[VOLUME] duck failed", exc_info=True)

    def _restore_volume(self, fade_ms: Optional[int] = None) -> None:
        """Restore system volume at the end of dictation.

        If ``fade_ms`` is ``None``, uses the configured fade duration.
        Pass ``0`` for instant restore (used on quit/restart).
        """
        if not getattr(self.config, "volume_duck_enabled", True):
            return
        try:
            if fade_ms is None:
                fade_ms = getattr(self.config, "volume_duck_fade_ms", 200)
            self._volume_ducker.restore(
                fade_ms=fade_ms,
                # UX-2: per-session removed — always master-volume restore.
                per_session=False,
            )
        except Exception:
            log.debug("[VOLUME] restore failed", exc_info=True)

    # ─── #2 (Round 9): ASR backend delegates to ModelManager ───────────
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

    # ── Model lifecycle methods (thin delegates to ModelManager) ──────

    def _init_qwen_engine(self):
        """#2: delegate to ModelManager._ensure_engine('qwen')."""
        self.models._ensure_engine("qwen")

    def _init_parakeet_engine(self):
        """#2: delegate to ModelManager._ensure_engine('parakeet')."""
        self.models._ensure_engine("parakeet")

    def _sync_asr_registry(self):
        """#2: delegate to ModelManager._sync_registry_from_fields()."""
        self.models._sync_registry_from_fields()

    def _get_active_transcriber(self):
        """#2: delegate to ModelManager.active_transcriber()."""
        return self.models.active_transcriber()

    # ─── Timer Tracking (P1) ─────────────────────────────────────────

    def _schedule_timer(self, delay: float, func) -> threading.Timer:
        """Create, track, and start a timer. Replaces fire-and-forget timers.

        PERF-TMR: Each call creates a fresh threading.Timer. A timer pool
        was considered but rejected because:
          - Only ~3-5 timers are created per dictation cycle
          - threading.Timer creation cost (~0.05 ms) is negligible vs.
            transcription latency (~1-5 seconds)
          - A timer pool would add complexity (reuse tracking, stale timer
            cleanup, thread-safety) for no measurable user-visible gain
          - The generation-guard pattern already prevents stale callbacks
        """
        gen = self._timer_generation
        def guarded_func():
            if gen == self._timer_generation:
                func()
        timer = threading.Timer(delay, guarded_func)
        # RACE-016: daemon=True is acceptable because timer callbacks
        # are fire-and-forget UI updates; missing one on shutdown is harmless.
        timer.daemon = True
        with self._pending_timers_lock:
            self._pending_timers.append(timer)
        timer.start()
        return timer

    def _cancel_pending_timers(self):
        """Cancel and clear all pending scheduled timers.

        ARCH-022: take the lock so concurrent appends from the tray /
        transcription / timer threads can't race with our iteration.
        The actual ``timer.cancel()`` calls happen outside the lock to
        avoid holding it longer than necessary.
        """
        with self._pending_timers_lock:
            timers = list(self._pending_timers)
            self._pending_timers.clear()
            self._timer_generation += 1
        for timer in timers:
            try:
                timer.cancel()
            except Exception:
                log.exception("[APP] Failed to cancel scheduled timer")

    # ─── Thread-Safe Streaming Session Access (P2) ───────────────────

    def _get_streaming_session(self):
        """#2 (Round 9): delegate to RecordingController.get_streaming_session()."""
        return self.recording.get_streaming_session()

    def _set_streaming_session(self, session_or_none):
        """#2 (Round 9): delegate to RecordingController.set_streaming_session()."""
        self.recording.set_streaming_session(session_or_none)

    # ─── Waveform Bubble (IPC push) ───────────────────────────────────

    def _wire_waveform_bubble(self) -> None:
        """Forward waveform bubble events to the IPC server.

        The bubble itself is a frameless, always-on-top ``BrowserWindow``
        owned by the Electron main process.  We just emit push events;
        the IPC server is reached via the module-level hook in
        ``voice_typer.server.ipc_server`` so listeners don't need to
        hold a reference to the app or server (avoids closure-capture
        bugs that broke the bubble on first run).
        """
        from voice_typer.server.ipc_server import _push_event_now

        def _push_bubble_show() -> None:
            sent = _push_event_now({"type": "bubble_show"})
            log.info("[WAVEFORM] bubble.show() fired; push=%s", "OK" if sent else "NO IPC")

        def _push_bubble_hide() -> None:
            _push_event_now({"type": "bubble_hide"})

        def _push_bubble_level(rms: float, peak: float) -> None:
            # PERF-NEW-001 / PERF-NEW-015: this callback fires from the
            # PortAudio thread at ~16 Hz.  Calling _push_event_now
            # directly was holding the IPC server's _lock for
            # json.dumps + socket.sendall, which on a slow Electron
            # receive window stalled the audio thread and triggered
            # xruns.  We now throttle to ~30 Hz max (every 33 ms) and
            # push the actual IPC send to a background queue drained
            # by a low-priority daemon thread.
            now = time.monotonic()
            last = getattr(self, "_last_bubble_level_push_ts", 0.0)
            if now - last < 0.033:  # 33 ms = ~30 Hz
                return
            self._last_bubble_level_push_ts = now
            q = getattr(self, "_bubble_level_queue", None)
            if q is None:
                return  # wiring not complete yet
            try:
                q.put_nowait({
                    "type": "bubble_level",
                    "data": {"rms": float(rms), "peak": float(peak)},
                })
            except queue.Full:
                # Queue is full — the worker thread fell behind.  Drop
                # this sample; the next one will pick up the latest
                # smoothed level from update_level's low-pass filter.
                pass

        # PERF-NEW-001: dedicated queue + worker thread for bubble
        # level pushes.  Bounded so a stuck Electron client can't
        # cause unbounded memory growth on the Python side.  Created
        # idempotently — if _wire_waveform_bubble is called twice
        # (e.g. in tests after a stop/start cycle), the existing
        # queue and worker are reused.
        if not hasattr(self, "_bubble_level_queue") or self._bubble_level_queue is None:
            self._bubble_level_queue: "queue.Queue[Optional[dict]]" = queue.Queue(maxsize=64)
        if not hasattr(self, "_bubble_level_worker_stop") or self._bubble_level_worker_stop is None:
            self._bubble_level_worker_stop = threading.Event()

        def _bubble_level_worker() -> None:
            """Drain the bubble_level queue and push events to the IPC server."""
            q = self._bubble_level_queue
            stop = self._bubble_level_worker_stop
            while not stop.is_set():
                try:
                    item = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if item is None:
                    break
                _push_event_now(item)
                q.task_done()

        if (
            not hasattr(self, "_bubble_level_worker")
            or self._bubble_level_worker is None
            or not self._bubble_level_worker.is_alive()
        ):
            self._bubble_level_worker = threading.Thread(
                target=_bubble_level_worker,
                name="bubble-level-pusher",
                daemon=True,
                # RACE-016: daemon=True is acceptable because the bubble
                # level worker is a UI-only push; on shutdown the IPC
                # server is torn down first and the worker's queue will
                # be drained by the atexit handler.
            )
            self._bubble_level_worker.start()

        self._waveform_bubble.on_show = _push_bubble_show
        self._waveform_bubble.on_hide = _push_bubble_hide
        self._waveform_bubble.on_level = _push_bubble_level
        log.info("[WAVEFORM] listeners wired on bubble coordinator")

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

    def _do_startup(self):
        """Background work: sync autostart, load mics, load model, register hotkey.

        RACE-020: checks ``self._shutting_down`` between each major step
        so that a quit() call during startup doesn't proceed with model
        downloads or background loads after the app has begun shutdown.
        """
        log.info("[STARTUP] _do_startup begin")

        if self._shutting_down:
            log.info("[STARTUP] _shutting_down is set, aborting startup")
            return

        # #8: Onboarding wizard — detect first run and let the React UI
        # show the wizard. Previously this auto-applied defaults and
        # marked onboarding complete, which prevented the wizard from
        # ever appearing (the 275-line Onboarding.tsx was dead code).
        # Now we just save the config with onboarding_completed=False
        # so the frontend can detect first-run via the
        # `onboarding_is_first_run` IPC route and route the user to
        # the wizard. The wizard's apply/skip handler flips the flag
        # to True and marks the .onboarding_complete marker.
        if not self.config.onboarding_completed:
            try:
                from voice_typer.server.onboarding import OnboardingController
                onboarding = OnboardingController()
                if onboarding.is_first_run():
                    log.info(
                        "[STARTUP] First run detected -- deferring to React "
                        "onboarding wizard (config.onboarding_completed=False)"
                    )
                    # Persist the config file with onboarding_completed=False
                    # so the frontend's `get_config` call sees the
                    # first-run state. The wizard handles applying the
                    # user's choices via `onboarding_apply`.
                    self.config.save()
            except Exception as e:
                # ERR-010: previously this was log.debug, which is
                # invisible at default log levels. If onboarding check
                # persistently fails the user is stuck on first-run
                # forever with no indication of why. Promote to
                # log.exception and notify the tray; after N consecutive
                # failures we mark onboarding completed with a failure
                # flag so the app remains usable.
                log.exception("[STARTUP] Onboarding check failed: %s", e)
                try:
                    self._onboarding_fail_count = getattr(
                        self, "_onboarding_fail_count", 0
                    ) + 1
                    if self._onboarding_fail_count >= 3:
                        self.config.onboarding_completed = True
                        self.config.onboarding_failed = True
                        try:
                            self.config.save()
                        except Exception:
                            log.exception("[STARTUP] Could not save onboarding_failed flag")
                        # NEW-UX-018: critical — bypass show_notifications toggle.
                        try:
                            self.tray.notify_safety(
                                "Voice Typer",
                                "Onboarding setup kept failing. The app will "
                                "start with default settings. Open Settings to "
                                "configure manually.",
                            )
                        except Exception:
                            pass
                    elif self.config.show_notifications:
                        try:
                            self.tray.notify(
                                "Voice Typer",
                                "Onboarding setup failed; will retry on next start.",
                            )
                        except Exception:
                            pass
                except Exception:
                    log.exception("[STARTUP] Onboarding failure-handler itself failed")

        # Load external text corrections (if available) before any transcription
        # ARCH-004: surface load errors to the user via tray notification
        # so they know why their corrections aren't taking effect.
        try:
            err = configure_corrections(config_dir=self.config.config_dir)
            if err is not None:
                # NEW-UX-018: critical — bypass toggle (broken corrections file).
                try:
                    self.tray.notify_safety(
                        "Voice Typer — Corrections Error",
                        f"{err}\nCorrections will use built-in defaults. "
                        f"Fix the file and restart.",
                    )
                except Exception:
                    log.debug("[STARTUP] Could not show corrections error notification")
        except Exception:
            log.debug("[STARTUP] External corrections load failed, using built-in defaults")

        # P2: Crash recovery -- check for unpasted transcriptions
        if self.config.crash_recovery_enabled:
            try:
                unpasted = self._crash_recovery.check_on_startup()
                if unpasted:
                    log.info("[STARTUP] Found %d unpasted transcriptions from previous session", len(unpasted))
                    # NEW-UX-018: critical — bypass toggle (recovered user data).
                    self.tray.notify_safety(
                        "Voice Typer",
                        f"Recovered {len(unpasted)} transcription(s) from last session. Open History to view.",
                    )
            except Exception:
                log.debug("[STARTUP] Crash recovery check failed")

        # DEAD-012: apply history retention policy at startup.
        # Previously the config keys were saved but never read.
        try:
            self.history_db.apply_retention(
                retention_days=self.config.history_retention_days,
                max_entries=self.config.history_max_entries,
                retention_count=self.config.history_retention_count,
            )
        except Exception:
            log.debug("[STARTUP] History retention apply failed")

        # PLAT-WAYLAND / XPLAT-004: Warn if running on Wayland and
        # suggest wtype/ydotool as fallback for global hotkeys.
        if is_linux() and os.environ.get("XDG_SESSION_TYPE") == "wayland":
            if not self.config.wayland_warned:
                log.warning("[STARTUP] Wayland detected -- global hotkeys may not work")
                # XPLAT-004: check if wtype or ydotool is available as a fallback
                import shutil
                wtype_available = shutil.which("wtype") is not None
                ydotool_available = shutil.which("ydotool") is not None
                if not wtype_available and not ydotool_available:
                    log.warning(
                        "[STARTUP] Neither wtype nor ydotool found. "
                        "Install one for hotkey support on Wayland: "
                        "'sudo apt install wtype' or 'sudo apt install ydotool'"
                    )
                    # NEW-UX-018: critical — bypass toggle (hotkeys broken).
                    self.tray.notify_safety(
                        "Voice Typer — Wayland Hotkeys",
                        "Global hotkeys may not work on Wayland. "
                        "Install 'wtype' or 'ydotool' for hotkey support, "
                        "or use the tray menu's Toggle Dictation option.",
                    )
                else:
                    log.info(
                        "[STARTUP] Wayland hotkey fallback available: %s",
                        "wtype" if wtype_available else "ydotool",
                    )
                self.config.wayland_warned = True
                self.config.save()

        # XPLAT-002 / PLAT-030: macOS accessibility permission check.
        # On macOS, global hotkeys require Accessibility permission.
        # The app can't request it directly, but we can detect it's
        # missing and notify the user.
        if is_macos():
            try:
                import subprocess as _sp
                # PLAT-030: Use AXIsProcessTrusted() via ctypes for the
                # definitive check.  AXIsProcessTrusted() is the official
                # API — it returns True iff the process has Accessibility
                # permission.  We load it from ApplicationServices.framework
                # via ctypes (no PyObjC dependency required).
                _has_accessibility = False
                try:
                    import ctypes
                    app_services = ctypes.cdll.LoadLibrary(
                        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
                    )
                    _has_accessibility = bool(app_services.AXIsProcessTrusted())
                except Exception:
                    # Fallback: osascript check (less reliable but works
                    # even if ctypes loading fails)
                    result = _sp.run(
                        ["osascript", "-e",
                         'tell application "System Events" to keystroke " "'],
                        capture_output=True, text=True, timeout=3,
                    )
                    _has_accessibility = result.returncode == 0

                if not _has_accessibility:
                    log.warning("[STARTUP] macOS Accessibility permission not granted")
                    # NEW-UX-018: critical — bypass toggle (hotkeys broken).
                    self.tray.notify_safety(
                        "Voice Typer — Accessibility Permission",
                        "Global hotkeys require Accessibility permission. "
                        "Open System Settings \u2192 Privacy & Security \u2192 Accessibility "
                        "and add Voice Typer (or Terminal).",
                    )
            except Exception:
                log.debug("[STARTUP] macOS accessibility check failed")

            # PLAT-009: Start a periodic accessibility health monitor.
            # If the user grants permission AFTER startup, the app will
            # detect it within 60 seconds and clear the warning. If the
            # user revokes permission mid-session, the app will re-warn.
            self._start_accessibility_pulse(_has_accessibility)

        # 1. Sync autostart config with platform
        log.info("[STARTUP] Step 1: sync autostart")
        self._sync_autostart()
        self.tray.set_autostart_enabled(is_autostart_enabled())

        # RACE-020: check for shutdown after each major step
        if self._shutting_down:
            log.info("[STARTUP] _shutting_down after autostart sync, aborting")
            return

        # 1b. Sync the OS-level prewarm scheduled task.
        #     fast_startup is always enabled; the prewarm task is registered
        #     at startup so the OS file cache is kept warm.  Cheap (a single
        #     schtasks /Query) and self-healing: if the user deleted the task
        #     or moved machines, it gets re-registered.
        #
        # PERF-NEW-030: prewarm sync + mic enumeration are independent
        # I/O-bound tasks. Run them in parallel on a ThreadPoolExecutor
        # so the total startup time is max(t_prewarm, t_mics) instead
        # of t_prewarm + t_mics.
        import concurrent.futures

        # RACE-020: pass the shutdown event to executor tasks so they
        # can abort early if the app is quitting during startup.
        _shutdown_event = self._shutting_down_event if hasattr(self, '_shutting_down_event') else None

        def _startup_parallel_work() -> None:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                prewarm_future = pool.submit(self._sync_prewarm_task, _shutdown_event)
                mic_future = pool.submit(self._load_microphones, _shutdown_event)
                # RACE-020: reduced timeout from 30s to 10s so a stuck
                # task doesn't block the entire startup sequence.
                for label, fut in [("prewarm", prewarm_future), ("mic", mic_future)]:
                    try:
                        fut.result(timeout=10)
                    except Exception as exc:
                        log.warning("[STARTUP] %s task failed: %s", label, exc)
            # AUDIO-MIC: start the background device-change poller so
            # USB/BT mic hotplug events are detected without requiring
            # a manual "Refresh Microphones" click.
            self._start_device_change_poller()

        # 1b. Create desktop launcher shortcut on first run (if absent)
        # (Run before parallel work so the shortcut exists before mic
        # enumeration — they're independent but shortcut creation is
        # fast and quick to fail.)
        self._ensure_desktop_shortcut()

        log.info("[STARTUP] Step 1c/2: parallel prewarm + mic enumeration")
        _startup_parallel_work()

        # RACE-020: check for shutdown after parallel work
        if self._shutting_down:
            log.info("[STARTUP] _shutting_down after parallel work, aborting")
            return

        # 3. Register hotkey BEFORE model load so F2 works even if model fails
        log.info("[STARTUP] Step 3: register hotkey")
        self._register_hotkey()

        # RACE-020: check for shutdown after hotkey registration
        if self._shutting_down:
            log.info("[STARTUP] _shutting_down after hotkey registration, aborting")
            return

        # Warmup handled synchronously in recording.py on first recording start.

        # 4. Create transcription engine and load model -- IN THE BACKGROUND.
        #
        # The model load is the dominant cost on a cold boot (~30-45s the
        # first time after Windows starts, dominated by reading ~6 GB of
        # torch + model-weight files off disk).  Running it in a daemon
        # thread lets the app reach "Ready" (well, "Loading model…") within
        # ~1s of launch; the user sees the tray icon, can open settings,
        # and -- if they press F2 before the load finishes -- gets queued
        # and auto-started once it completes.  See toggle_dictation().
        #
        # #2 (Round 9): ModelManager owns the load thread now; the
        # ``self._model_load_thread`` property delegate on VoiceTyperApp
        # reads/writes through to ``self.models._model_load_thread`` so
        # existing code that checks the thread (e.g. toggle_dictation)
        # keeps working.
        log.info("[STARTUP] Step 4: create transcription engine (background)")
        self.models.start_background_load()

        # RACE-020: check for shutdown after background model load start
        if self._shutting_down:
            log.info("[STARTUP] _shutting_down after model load start, aborting")
            return

        # After restart: auto-open the Electron window so it appears fresh
        # once the new instance is fully ready.  The VOICE_TYPER_RESTART
        # env var is set by restart_app() before launching the new process.
        if os.environ.get("VOICE_TYPER_RESTART"):
            log.info("[STARTUP] Restart detected -- opening Electron window")
            try:
                self.tray.open_electron_window()
            except Exception as e:
                log.warning("[STARTUP] Failed to open Electron window after restart: %s", e)

        # Show the bubble at startup if always_visible mode is enabled AND
        # bubble_show_on_startup is True (user's preference in Settings).
        if self.config.bubble_behavior == 'always_visible' and self.config.bubble_show_on_startup:
            try:
                self._waveform_bubble.show()
                log.info("[STARTUP] Bubble shown at startup (always_visible mode)")
            except Exception as e:
                log.warning("[STARTUP] Failed to show bubble at startup: %s", e)

        log.info("[STARTUP] initial setup complete -- model loading continues in background")

    def _load_transcription_engine_background(self) -> None:
        """#2 (Round 9): delegate to ModelManager.load_background()."""
        self.models.load_background()

    def _sync_autostart(self) -> None:
        """Delegate to startup_tasks.sync_autostart (extracted for testability)."""
        from voice_typer.server.startup_tasks import sync_autostart

        sync_autostart(self)

    def _sync_prewarm_task(self, shutdown_event=None) -> None:
        """Delegate to startup_tasks.sync_prewarm_task (extracted for testability)."""
        from voice_typer.server.startup_tasks import sync_prewarm_task

        sync_prewarm_task(self, shutdown_event)

    def _ensure_desktop_shortcut(self) -> None:
        """Delegate to startup_tasks.ensure_desktop_shortcut (extracted for testability)."""
        from voice_typer.server.startup_tasks import ensure_desktop_shortcut

        ensure_desktop_shortcut(self)

    def _load_microphones(self, shutdown_event=None) -> None:
        """Delegate to startup_tasks.load_microphones (extracted for testability).

        The extracted function compares old_ids vs new_ids (the cached
        device-id set vs the freshly enumerated one) and pushes a
        ``microphones_changed`` IPC event when the device set changes,
        so the Electron renderer can refresh its microphone dropdown
        without a manual "Refresh" click.
        """
        from voice_typer.server.startup_tasks import load_microphones

        load_microphones(self, shutdown_event)

    def _start_device_change_poller(self) -> None:
        """Delegate to startup_tasks.start_device_change_poller (extracted for testability)."""
        from voice_typer.server.startup_tasks import start_device_change_poller

        start_device_change_poller(self)

    def _start_accessibility_pulse(self, initial_state: bool) -> None:
        """Delegate to startup_tasks.start_accessibility_pulse (extracted for testability)."""
        from voice_typer.server.startup_tasks import start_accessibility_pulse

        start_accessibility_pulse(self, initial_state)

    def _fallback_to_whisper(self, notify_on_failure: bool = False):
        """#2 (Round 9): delegate to ModelManager.fallback_to_whisper()."""
        self.models.fallback_to_whisper(notify_on_failure=notify_on_failure)

    def _try_load_model(self, notify_on_failure: bool = False):
        """#2 (Round 9): delegate to ModelManager.try_load()."""
        self.models.try_load(notify_on_failure=notify_on_failure)

    # ─── Hotkey ────────────────────────────────────────────────────────

    def _register_hotkey(self):
        """#2 (Round 9): delegate to HotkeyDispatcher.register()."""
        self.hotkeys.register()
    def _register_esc_hotkey(self):
        """#2 (Round 9): delegate to HotkeyDispatcher.register_esc()."""
        self.hotkeys.register_esc()
    def _unregister_esc_hotkey(self):
        """#2 (Round 9): delegate to HotkeyDispatcher.unregister_esc()."""
        self.hotkeys.unregister_esc()
    def _register_repaste_hotkey(self):
        """#2 (Round 9): delegate to HotkeyDispatcher.register_repaste()."""
        self.hotkeys.register_repaste()
    # ─── Dictation ─────────────────────────────────────────────────────

    def toggle_dictation(self):
        """#2 (Round 9): delegate to RecordingController.toggle()."""
        self.recording.toggle()
    def _start_dictation(self):
        """#2 (Round 9): delegate to RecordingController.start()."""
        self.recording.start()
    def _on_recorder_rms(self, rms: float, peak: float, audio_chunk=None) -> None:
        """#2 (Round 9): delegate to RecordingController.on_recorder_rms()."""
        self.recording.on_recorder_rms(rms, peak, audio_chunk=audio_chunk)
    def _on_audio_quality_chunk(self, rms: float, peak: float) -> None:
        """Per-chunk quality callback wired to AudioProcessor.

        Runs inside the PortAudio audio callback (via
        ``AudioProcessor.process_chunk`` → ``_run_quality_check``), so
        it MUST be non-blocking.  We only update cheap running
        statistics — no I/O, no allocation of large structures, no
        logging per chunk.  Full analysis runs in
        :meth:`_finalize_audio_quality_report` after stop().

        The analyzer's :meth:`analyze_chunk` would normally take the
        raw numpy chunk, but we already have (rms, peak) computed by
        the AudioProcessor — reconstructing the chunk just to compute
        the same metrics again would waste cycles.  Instead we feed
        the precomputed values into the analyzer's internal accumulators
        directly.
        """
        try:
            aq = self._audio_quality
            # Mirror analyze_chunk() without the numpy work — we
            # already have rms and peak from the AudioProcessor.
            aq._rms_values.append(rms)
            aq._chunk_count += 1
            if peak > aq._peak:
                aq._peak = peak
            if peak >= aq.CLIPPING_THRESHOLD:
                aq._clip_count += 1
        except Exception:
            # Quality analysis must NEVER break the audio callback.
            log.debug("[AUDIO_QUALITY] per-chunk update failed", exc_info=True)

    def _rebuild_audio_processor(self) -> None:
        """ADR 0007 §6.1: Rebuild the audio filter chain from current config.

        Called by ``service.apply_config_side_effects`` when any
        ``noise_filter_*`` or ``audio_preset`` or
        ``noise_suppression_method`` config field changes. Atomically
        swaps the filter chain so the next ``process_chunk()`` call
        uses the new filters — no restart required.
        """
        try:
            self._audio_processor.rebuild_from_config(self.config)
            log.info(
                "[APP] Audio processor rebuilt: %s",
                self._audio_processor.filter_names,
            )
        except Exception:
            log.exception("[APP] Failed to rebuild audio processor")

    def _finalize_audio_quality_report(self, audio: np.ndarray) -> None:
        """Run final audio-quality analysis and surface warnings.

        Called from :meth:`_stop_dictation` after ``recorder.stop()``
        returns the (already filtered + resampled) audio.

        FIX-HOTKEY-AND-NOTIFICATION: the tray notification that used to
        fire here ("Low volume (RMS=...). Increase mic gain or move
        closer. | High noise (ratio=...). Try a quieter environment")
        was deemed annoying by users. We now short-circuit at the top of
        this method so NO tray notification is ever shown — even if a
        user manually sets ``audio_quality_warnings = True`` in their
        config file. The internal ``AudioQualityAnalyzer`` may still
        run for logging purposes (below), but it MUST NOT surface any
        user-facing notification.
        """
        # Hard short-circuit: NEVER show a tray notification. The
        # ``audio_quality_warnings`` config field is honored here only
        # as a kill-switch (when False, we skip the analysis entirely
        # for efficiency); when True we still run the analysis for
        # internal logging but DO NOT call ``self.tray.notify``.
        if not getattr(self.config, "audio_quality_warnings", False):
            return
        # Even when the flag is True, we deliberately do NOT call
        # ``self.tray.notify``. Run the analysis for internal logging
        # only, then bail out.
        try:
            report = self._audio_quality.analyze_full_audio(audio)
            if report.has_issues:
                summary = report.get_summary()
                log.info("[AUDIO_QUALITY] Issues detected: %s", summary)
            # Reset for the next session.
            self._audio_quality.reset()
        except Exception:
            log.debug("[AUDIO_QUALITY] finalize report failed", exc_info=True)

    def _stop_dictation(self):
        """Stop recording and transcribe in background."""
        if not self.recorder.recording:
            log.info("[DICTATION] _stop_dictation: not recording, no-op")
            return

        # Cancel any stale pending timers
        self._cancel_pending_timers()

        log.info("[DICTATION] Stopping recording... (cycle=%s)", self._cycle_id)

        self._busy_event.clear()  # busy = True

        # Detach the RMS callback and hide the bubble so the audio path
        # cannot keep pushing levels after the stream is closed.
        self.recorder.on_rms_level = None
        # Push a final zero-level event so the renderer resets its animation
        # envelope. Without this, the dots stay frozen at their last active
        # height because rawLevelRef is never set back to 0.
        self._waveform_bubble.reset_level()
        # Hide bubble unless always_visible mode (bubble stays on screen)
        if self.config.bubble_behavior != 'always_visible':
            self._waveform_bubble.hide()

        try:
            audio = self.recorder.stop()
        except Exception as e:
            log.exception("[DICTATION] Failed to stop recording")
            self._cancel_streaming_session()
            self._restore_volume()
            self.tray.set_state(AppState.ERROR, "Stop failed")
            # NEW-UX-018: critical — bypass toggle (dictation failed).
            self.tray.notify_safety("Voice Typer", f"Could not stop recording.\n{e}")
            self._busy_event.set()  # busy = False
            self._schedule_timer(3.0, lambda: self.tray.set_state(AppState.IDLE))
            return

        # Restore system volume immediately — don't wait for transcription
        # (which takes seconds) before the user gets their audio back.
        self._restore_volume()

        # Audio has already been resampled to config.sample_rate by Recorder.stop()
        duration = len(audio) / self.config.sample_rate if len(audio) > 0 else 0
        # Capture RMS before starting transcription thread (race-safe)
        recorded_rms = self.recorder.last_rms

        # Run the revived AudioQualityAnalyzer on the captured audio.
        # Surfaces clipping / low-volume / high-noise warnings via tray
        # (gated by config.audio_quality_warnings).  Must run BEFORE
        # the transcription thread starts so the report reflects this
        # session's audio, not whatever the next session produces.
        if duration > 0:
            try:
                self._finalize_audio_quality_report(audio)
            except Exception:
                log.debug("[AUDIO_QUALITY] finalize failed", exc_info=True)
        log.info("[DICTATION] Recording stopped -- %.1fs of audio, busy=True (cycle=%s)", duration, self._cycle_id)

        if duration < 0.5:
            log.info("[DICTATION] Audio too short, skipping transcription")
            self._cancel_streaming_session()
            self.tray.set_state(AppState.IDLE, "Too short -- ignored")
            self._busy_event.set()  # busy = False
            self._schedule_timer(2.0, lambda: self.tray.set_state(AppState.IDLE))
            return

        log.info("[DICTATION] Starting transcription thread... (cycle=%s)", self._cycle_id)
        self.tray.set_state(AppState.TRANSCRIBING, "Transcribing...")

        # PERF-NEW-005: signal the streaming session to cancel BEFORE
        # starting the final transcription thread.  The streaming
        # thread's cancel event is set here (non-blocking), so any
        # in-flight streaming inference will abort quickly and release
        # the transcriber lock before the final transcription tries to
        # acquire it.  Previously the streaming thread could still be
        # holding the lock, adding 600-1200ms latency.
        session = self._get_streaming_session()
        if session is not None:
            try:
                session._cancel_event.set()
            except Exception:
                pass

        # RACE-013 / ARCH-017: The legacy Timer-based watchdog that used
        # to live here has been REMOVED. It was duplicating the Event-based
        # persistent watchdog thread started by RecordingController.stop()
        # (see recording_controller.py:_start_watchdog_thread), causing BOTH
        # to fire 60s after a transcription completed normally — the Timer
        # would force-recover an already-healthy app.
        #
        # The pipeline's finally block (dictation_pipeline.py:166-169) now
        # resets + stops the Event-based watchdog via _reset_watchdog() +
        # _stop_watchdog_thread(). There is no Timer to cancel anymore.
        #
        # The `watchdog` argument to DictationPipeline.run() below is kept
        # for backward compatibility with older tests that still construct
        # DictationPipeline directly; it is ignored inside the pipeline.

        _captured_cycle_id = self._cycle_id

        # ARCH-006: transcribe_thread extracted to DictationPipeline class.
        # The pipeline runs on a daemon thread and handles all steps:
        # transcribe → clean → vocab → templates → punctuate → LLM → store → paste.
        from voice_typer.server.dictation_pipeline import DictationPipeline

        def transcribe_thread():
            pipeline = DictationPipeline(self)
            pipeline.run(
                audio=audio,
                duration=duration,
                recorded_rms=recorded_rms,
                cycle_id=_captured_cycle_id,
                watchdog=None,  # RACE-013: Event-based watchdog is used, not Timer
            )

        # ARCH-REFAC-003: write directly to RecordingController's
        # _transcription_thread (was a @property delegate previously).
        self.recording._transcription_thread = threading.Thread(
            target=transcribe_thread,
            name="Transcription",
            daemon=True,
            # RACE-016: daemon=True is acceptable because the pipeline's
            # finally block clears the busy event and crash recovery is
            # handled by the atexit handler if the thread is killed.
        )
        self.recording._transcription_thread.start()

    def _streaming_enabled(self) -> bool:
        """#2 (Round 9): delegate to RecordingController._streaming_enabled()."""
        return self.recording._streaming_enabled()
    def _streaming_config(self) -> StreamingConfig:
        """#2 (Round 9): delegate to RecordingController._streaming_config()."""
        return self.recording._streaming_config()
    def _start_streaming_session_if_enabled(self):
        """#2 (Round 9): delegate to RecordingController._start_streaming_session_if_enabled()."""
        self.recording._start_streaming_session_if_enabled()
    def _cancel_streaming_session(self):
        """#2 (Round 9): delegate to RecordingController._cancel_streaming_session()."""
        self.recording._cancel_streaming_session()
    def _force_recover_from_stuck_transcription(self):
        """#2 (Round 9): delegate to RecordingController._force_recover_from_stuck_transcription()."""
        self.recording._force_recover_from_stuck_transcription()
    # ─── Silence Detection Callbacks (H12) ────────────────────────────────

    def _on_silence_warning(self):
        """#2 (Round 9): delegate to RecordingController.on_silence_warning()."""
        self.recording.on_silence_warning()
    def _on_silence_auto_stop(self):
        """#2 (Round 9): delegate to RecordingController.on_silence_auto_stop()."""
        self.recording.on_silence_auto_stop()
    def _on_max_duration_auto_stop(self):
        """#2 (Round 9): delegate to RecordingController.on_max_duration_auto_stop()."""
        self.recording.on_max_duration_auto_stop()
    # ─── Settings / Microphone ─────────────────────────────────────────

    def repaste_last(self) -> None:
        """Feature: Repaste last transcription (tray menu + hotkey).

        ERR-018: previously a single try/except collapsed clipboard-copy
        failures and paste-keystroke failures into one generic toast.
        We now split them so the user knows which step failed.
        """
        if not self._last_transcription:
            self.tray.notify("Voice Typer", "No previous transcription to re-paste.")
            return

        # Step 1: copy to clipboard
        try:
            self.clipboard.copy(self._last_transcription)
        except Exception as e:
            log.warning("[REPASTE] Clipboard copy failed: %s", e)
            self.tray.notify(
                "Voice Typer",
                "Could not copy the transcription to the clipboard. "
                "Another app may be holding the clipboard lock.",
            )
            return

        # Step 2: send paste keystrokes
        try:
            self.clipboard.paste()
            log.info(
                "[REPASTE] Repasted last transcription (%d chars)",
                len(self._last_transcription),
            )
            self.tray.notify("Voice Typer", "Last transcription re-pasted")
        except Exception as e:
            log.warning("[REPASTE] Paste keystroke failed: %s", e)
            self.tray.notify(
                "Voice Typer",
                "Copied to clipboard, but the paste keystroke failed. "
                "Press Ctrl+V manually to paste.",
            )

    def undo_last(self) -> None:
        """UX-003: Undo last transcription by sending backspace keystrokes.

        Sends one backspace per character in the last transcription.
        Works by simulating keyboard input via the hotkey backend's
        keyboard controller (pynput on all platforms).
        """
        if not self._last_transcription:
            self.tray.notify("Voice Typer", "Nothing to undo.")
            return
        text = self._last_transcription
        char_count = len(text)
        log.info("[UNDO] Undoing last transcription (%d chars)", char_count)
        try:
            # Use pynput to send backspace keystrokes
            from pynput.keyboard import Controller as KeyboardController
            kb = KeyboardController()
            # Select all text in the current field first (Ctrl+A), then
            # Delete — this is more reliable than sending N backspaces
            # because it handles multi-line text and doesn't leave
            # partial characters.
            # However, Ctrl+A selects ALL text in the field, which may
            # be more than just our transcription.  So we send N
            # backspaces instead — this is the standard "undo paste"
            # behavior.
            for _ in range(char_count):
                kb.press('\x08')  # Backspace
                kb.release('\x08')
            self._last_transcription = ""
            self.tray.notify("Voice Typer", f"Undid last transcription ({char_count} chars)")
        except ImportError:
            log.warning("[UNDO] pynput not available for undo")
            self.tray.notify("Voice Typer", "Undo not available (pynput missing)")
        except Exception as e:
            log.warning("[UNDO] Failed: %s", e)
            self.tray.notify("Voice Typer", f"Undo failed: {e}")

    def _on_xrun_threshold(self, count: int) -> None:
        """#2 (Round 9): delegate to RecordingController.on_xrun_threshold()."""
        self.recording.on_xrun_threshold(count)
    def _cancel_dictation(self):
        """#2 (Round 9): delegate to RecordingController.cancel()."""
        self.recording.cancel()
    def _toggle_autostart(self):
        """Toggle autostart on/off from the tray menu. Delegates to _set_autostart (P2 dedup)."""
        self._set_autostart(not is_autostart_enabled())

    def _set_autostart(self, enabled: bool):
        """Set autostart from the advanced settings window or tray toggle."""
        try:
            if enabled:
                enable_autostart()
            else:
                disable_autostart()
            self.config.autostart = enabled
            self.config.save()
            self.tray.set_autostart_enabled(enabled)
            log.info("[CONFIG] Autostart set to %s", enabled)
        except Exception as e:
            log.exception("[CONFIG] Failed to set autostart")
            self.tray.notify("Voice Typer", f"Could not change autostart setting.\n{e}")

    def _set_notifications(self, enabled: bool):
        """Set notification behavior from the settings window."""
        self.config.show_notifications = enabled
        self.config.save()
        self.tray.set_notifications_enabled(enabled)
        log.info("[CONFIG] Notifications set to %s", enabled)

    def _select_microphone(self, mic_name: str | None):
        """Handle microphone selection from tray menu."""
        self.config.microphone = mic_name
        self.config.save()
        label = mic_name if mic_name else "System Default"

        if self.recorder.recording:
            log.info("[CONFIG] Microphone changed to %s; applying after active recording", label)
            self.tray.notify("Voice Typer", f"Microphone next recording: {label}")
            return

        self.recorder = Recorder(self.config, audio_processor=self._audio_processor)  # re-create with new mic
        log.info("[CONFIG] Microphone changed to: %s", label)
        self.tray.notify("Voice Typer", f"Microphone: {label}")

    def show_settings(self):
        """Open the native settings window."""
        # If a settings window is already open, bring it to front instead of
        # creating a duplicate.
        if self._settings_window is not None:
            try:
                self._settings_window.root.lift()
                return
            except Exception:
                # Window was destroyed (e.g. user closed via title bar X)
                self._settings_window = None

        controller = SettingsController(
            self.config,
            on_hotkey_changed=self._restart_hotkey,
            on_model_changed=self._change_model,
            on_microphone_changed=self._select_microphone,
            on_autostart_changed=self._set_autostart,
            on_notifications_changed=self._set_notifications,
            # RACE-011: share the app-wide config-mutation lock so
            # SettingsController.apply() and IPC set_config can't
            # interleave Config attribute writes.
            config_mutation_lock=self._config_mutation_lock,
        )
        window = SettingsWindow(
            controller,
            microphones=self._microphones,
            on_open_config=self._open_config_file,
        )
        window.on_destroy = lambda: setattr(self, '_settings_window', None)
        self._settings_window = window
        window.show()

    def _open_config_file(self):
        """Open config file. NEW-SEC-006: hardcoded notepad path.

        SEC-audit-011: saves config before opening the editor, then
        reloads after the editor closes using Popen().wait().  This
        ensures the on-disk config is always in sync with the in-memory
        config when the user starts editing, and that changes made in
        the editor are picked up when editing is done.

        SEC-audit-011 (revised): holds ``_config_mutation_lock`` for
        the duration of the editor session so the IPC ``set_config``
        handler cannot atomically replace config.json via
        ``_secure_atomic_write`` while Notepad is mid-edit. Without
        this lock, a TOCTOU race exists: IPC set_config could write a
        new config between Notepad reading and saving, silently
        overwriting the user's manual edits. The lock is held until
        Notepad closes and the config is reloaded.
        """
        config_file = self.config.config_dir / "config.json"
        # Save current in-memory config so the editor sees the latest state
        self.config.save()
        import subprocess
        try:
            if is_windows():
                # SEC-audit-011: Use SystemRoot-validated notepad path.
                # Fall back to hardcoded C:\Windows\System32\notepad.exe
                # if SystemRoot validation failed.
                systemroot = os.environ.get("SystemRoot", r"C:\Windows")
                notepad = Path(systemroot) / "System32" / "notepad.exe"
                if not notepad.exists():
                    # Hardcoded fallback per SEC-audit-011
                    notepad = Path(r"C:\Windows\System32\notepad.exe")
                if notepad.exists():
                    # SEC-audit-011: Hold _config_mutation_lock for the
                    # full editor session so IPC set_config can't race.
                    with self._config_mutation_lock:
                        # SEC-audit-011: Use Popen().wait() to block until
                        # notepad closes, then reload the config.
                        proc = subprocess.Popen([str(notepad), str(config_file)])
                        try:
                            proc.wait()
                        except Exception:
                            pass
                        # Reload config after notepad closes
                        try:
                            self.config = type(self.config).load()
                        except Exception as exc:
                            log.warning("[CONFIG] Failed to reload config after editor: %s", exc)
                else:
                    os.startfile(str(config_file))  # type: ignore[attr-defined]
            elif is_macos():
                subprocess.Popen(["open", str(config_file)])
            else:
                subprocess.Popen(["xdg-open", str(config_file)])
        except Exception as e:
            log.warning("[CONFIG] Could not open editor: %s", e)
            self.tray.notify("Voice Typer", f"Config file:\n{config_file}")

    def _restart_hotkey(self, hotkey: str):
        """#2 (Round 9): delegate to HotkeyDispatcher.restart()."""
        self.hotkeys.restart(hotkey)
    def _change_model(self, model_size: str):
        """#2 (Round 9): delegate to ModelManager.change_model()."""
        self.models.change_model(model_size)

    # ─── TrayController Protocol Methods (P3) ────────────────────────

    def change_microphone(self, mic_id: str | None) -> None:
        """TrayController protocol: select microphone."""
        self._select_microphone(mic_id)

    def change_model(self, model: str) -> None:
        """TrayController protocol: change transcription model."""
        self._change_model(model)

    def change_hotkey(self, hotkey: str) -> None:
        """TrayController protocol: change hotkey. NEW-DEAD-022: set_hotkey is an alias."""
        self._restart_hotkey(hotkey)

    set_hotkey = change_hotkey

    def open_settings(self) -> None:
        """TrayController protocol: open settings window."""
        self.show_settings()

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
        """
        log.info("[QUIT] Quitting Voice Typer...")

        # Item 12: If recording, discard the recording before quitting
        # so we don't leave the mic open or lose the in-flight audio.
        try:
            if self.recorder and self.recorder.recording:
                log.info("[QUIT] Recording in progress — discarding before quit")
                self.recorder.discard()
        except Exception:
            log.debug("[QUIT] Could not discard recording", exc_info=True)

        # 0. Notify Electron frontend over TCP so it can quit cleanly.
        from voice_typer.server.ipc_server import _push_event_now
        _push_event_now({"type": "quit_app"})

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

        This replaces the old ``restart_ack`` design which tried to
        keep Electron alive while swapping only the Python backend.
        That design had multiple race conditions:

          1. The TCP 'close' event could fire before the 'data' event
             delivering ``restart_ack`` was processed, causing spurious
             "Python socket closed" errors.
          2. ``tcpConnect()`` set ``tcpSocket = client`` BEFORE the
             socket connected, so IPC calls during the reconnection
             window were written to the unconnected socket, buffered,
             and sent BEFORE the auth handshake — causing auth failures
             and cascading "Error: Timeout" errors.
          3. The ``_restarting`` flag was cleared too early (in
             ``startPython``, before the new process was up), leaving a
             window where ``sendToPython`` wrote to a stale/dying socket.

        The full-relaunch approach eliminates all of these: the entire
        OS process is replaced, so there's no state to coordinate. The
        user's explicit request was "close the entire process, the
        entire backend, and the entire Electron application; everything
        should be closed and opened again."

        RELIABILITY-001: was ``os._exit(0)`` which skipped atexit
        handlers + ``__del__``, leaking the Win32 mutex, PortAudio
        handles, and ``RegisterHotKey`` registrations. RELIABILITY-003:
        also stops ``_esc_backend`` and ``_repaste_backend`` so the new
        instance can re-register them. RELIABILITY-006: marks
        ``_shutting_down`` before cleanup so atexit doesn't log "likely
        killed externally" for an intentional restart.
        """
        log.info("[RESTART] Restarting Voice Typer...")

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
        # 1. Push relaunch_electron BEFORE marking _shutting_down.
        from voice_typer.server.ipc_server import _push_event_now
        try:
            _push_event_now({"type": "relaunch_electron"})
            log.info("[RESTART] relaunch_electron pushed to Electron via TCP")
        except Exception as e:
            log.warning("[RESTART] failed to push relaunch_electron: %s", e)

        # 2. NOW mark as shutting down, restore volume, and give Electron
        #    time to process the relaunch event before we close the socket.
        self._shutting_down = True
        self._restore_volume(fade_ms=0)
        log.info("[RESTART] Pausing 300ms for Electron to process relaunch_electron")
        time.sleep(0.3)

        # 3. Stop backends so the new instance can re-register everything.
        self._cancel_pending_timers()
        try:
            # ARCH-REFAC-003: access HotkeyDispatcher directly (was a
            # @property delegate previously).
            if self.hotkeys._hotkey_backend:
                self.hotkeys._hotkey_backend.stop()
            if self.hotkeys._esc_backend:
                self.hotkeys._esc_backend.stop()
            if self.hotkeys._repaste_backend:
                self.hotkeys._repaste_backend.stop()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass

        # 4. Exit cleanly — electron will relaunch us.
        log.info("[RESTART] Old process exiting via sys.exit(0)")
        sys.exit(0)

    # DEAD-008: the following 6 TrayController protocol methods were
    # removed because no IPC route, tray menu item, or UI invoked them:
    #   - toggle_autostart (use _toggle_autostart directly)
    #   - create_desktop_shortcut
    #   - set_notifications (use _set_notifications directly)
    #   - set_silence_warning_seconds (use set_config via IPC)
    #   - set_silence_auto_stop_seconds (use set_config via IPC)
    #   - set_max_recording_seconds (use set_config via IPC)
    # The corresponding TrayController Protocol entries were also removed.

    # ─── Shutdown ──────────────────────────────────────────────────────

    def quit(self):
        """Shut down the application cleanly.

        PROD-003: ensures all threads, PortAudio streams, and
        subprocesses are properly stopped with timeouts. Previously
        thread joins had no timeout and PortAudio streams could be
        left open if quit() raced with the audio callback.
        """
        if self._shutting_down:
            log.info("[SHUTDOWN] quit() already in progress, ignoring duplicate call")
            return

        is_main = threading.current_thread() is threading.main_thread()
        log.info("[SHUTDOWN] Shutting down (quit() called from thread=%s, is_main=%s)",
                 threading.current_thread().name, is_main)
        self._shutting_down = True
        # RACE-020: also set the Event version so executor tasks can check it
        self._shutting_down_event.set()

        # Cancel all pending timers
        self._cancel_pending_timers()

        # PROD-003: Stop the persistent watchdog thread
        try:
            if hasattr(self, 'recording') and self.recording is not None:
                self.recording._stop_watchdog_thread()
        except Exception:
            pass

        # Signal streaming session to cancel without blocking on join.
        # The old code called _cancel_streaming_session() → session.cancel()
        # → thread.join(timeout=10) which blocked quit for up to 10 seconds.
        # Instead, just signal the cancel event; the daemon thread will die
        # when the process exits.
        session = self._get_streaming_session()
        self._set_streaming_session(None)
        if session is not None:
            session._cancel_event.set()

        # PROD-003: Close PortAudio stream properly.
        # recorder.stop() fully closes the PortAudio stream (stop + close),
        # while discard() just clears the recording flag. Use stop() first
        # for a clean shutdown, then discard() as fallback if stop() fails.
        if self.recorder.recording:
            try:
                self.recorder.stop()
            except Exception as e:
                log.warning("[SHUTDOWN] recorder.stop() failed: %s, trying discard()", e)
                try:
                    self.recorder.discard()
                except Exception as e2:
                    log.warning("[SHUTDOWN] recorder.discard() also failed: %s", e2)

        # PERF-MIC-001: stop the OS-event device watcher so its daemon
        # thread exits cleanly before the process tears down. Best-effort
        # — the thread is a daemon and would die on process exit anyway,
        # but explicit stop() avoids a 2s join race during GC.
        try:
            self.recorder.shutdown_mic_watcher()
        except Exception as e:
            log.debug("[SHUTDOWN] mic watcher shutdown failed: %s", e)

        # Restore volume if we were ducked when the app quit.
        # Without this, a quit-during-recording leaves volume stuck low.
        # Use fade_ms=0 for instant restore — the app is exiting.
        self._restore_volume(fade_ms=0)

        # Wait for any running transcription thread to finish (short timeout).
        # ARCH-REFAC-003: read directly from RecordingController (was a
        # @property delegate previously).
        t = self.recording._transcription_thread
        if t is not None and t.is_alive():
            log.info("[SHUTDOWN] Waiting for transcription thread to finish...")
            t.join(timeout=3.0)
            if t.is_alive():
                log.warning("[SHUTDOWN] Transcription thread did not finish in time, continuing shutdown")

        # ARCH-REFAC-003: access HotkeyDispatcher directly (was a
        # @property delegate previously).
        if self.hotkeys._hotkey_backend:
            self.hotkeys._hotkey_backend.stop()

        # RELIABILITY-003: also stop ESC cancel and repaste hotkey
        # backends so their RegisterHotKey / GlobalHotKeys registrations
        # are released before the next instance tries to claim them.
        if self.hotkeys._esc_backend:
            try:
                self.hotkeys._esc_backend.stop()
            except Exception as e:
                log.warning("[SHUTDOWN] ESC backend stop failed: %s", e)
        if self.hotkeys._repaste_backend:
            try:
                self.hotkeys._repaste_backend.stop()
            except Exception as e:
                log.warning("[SHUTDOWN] repaste backend stop failed: %s", e)

        # RELIABILITY-005: flush any pending crash-recovery writes
        # before the process exits, so the latest state is persisted.
        # Short timeout — if the disk is genuinely slow we'd rather
        # exit and lose the in-flight snapshot than hang the shutdown.
        if self._crash_recovery is not None:
            try:
                self._crash_recovery.flush(timeout=2.0)
                self._crash_recovery.shutdown()
            except Exception as e:
                log.warning("[SHUTDOWN] crash recovery flush failed: %s", e)

        # PERF-NEW-001: stop the bubble level worker so it doesn't
        # try to push to a torn-down IPC server during shutdown.
        if hasattr(self, "_bubble_level_worker_stop") and self._bubble_level_worker_stop is not None:
            try:
                self._bubble_level_worker_stop.set()
                if hasattr(self, "_bubble_level_queue") and self._bubble_level_queue is not None:
                    try:
                        self._bubble_level_queue.put_nowait(None)  # sentinel
                    except queue.Full:
                        pass
                if hasattr(self, "_bubble_level_worker") and self._bubble_level_worker is not None:
                    self._bubble_level_worker.join(timeout=1.0)
            except Exception as e:
                log.debug("[SHUTDOWN] bubble level worker stop failed: %s", e)

        self.tray.stop()

        # PROD-003: Safety net — stop any remaining PortAudio streams.
        # If recorder.stop() above failed or an audio callback leaked
        # a stream, this ensures sounddevice doesn't hold the microphone.
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

        # PROD-003: Terminate the Electron subprocess if we spawned one.
        # The IPC "quit_app" push was sent earlier; this is a forced
        # termination as a safety net if the graceful signal didn't land.
        try:
            from voice_typer.server.tray_window import get_electron_pid
            electron_pid = get_electron_pid()
            if electron_pid is not None:
                import signal as _sig
                log.info("[SHUTDOWN] Terminating Electron subprocess (PID=%s)", electron_pid)
                try:
                    os.kill(electron_pid, _sig.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass
        except Exception:
            pass

        log.info("[SHUTDOWN] Shutdown complete, exiting")

        # PLAT-HLEAK: Close the mutex handle on shutdown
        if hasattr(self, '_mutex_handle') and self._mutex_handle:
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
                self._mutex_handle = None
            except Exception:
                pass

        # Close devnull streams opened during logging setup
        _close_devnull_files()

        if is_main:
            sys.exit(0)

    def _atexit_log(self) -> None:
        """Log when the process exits, even if quit() was not called."""
        if not self._shutting_down:
            log.warning("[ATEXIT] Process exiting without quit() -- "
                        "likely killed externally (console close, task manager, etc.)")

    def _atexit_cleanup(self) -> None:
        """RACE-016: atexit handler for critical cleanup paths.

        Daemon threads can be killed by the interpreter without running
        their finally blocks.  This method is a safety net that ensures
        critical cleanup (volume restore, hotkey release, crash recovery
        flush) happens even if the daemon thread's finally block didn't
        run.  It is idempotent — calling it after quit() is a no-op
        because quit() already did the cleanup.
        """
        try:
            if self._shutting_down:
                return  # quit() already handled cleanup
            log.info("[ATEXIT] Running emergency cleanup")
            self._restore_volume(fade_ms=0)
        except Exception:
            pass
        try:
            # ARCH-REFAC-003: access HotkeyDispatcher directly (was a
            # @property delegate previously).
            if self.hotkeys._hotkey_backend:
                self.hotkeys._hotkey_backend.stop()
        except Exception:
            pass
        try:
            if self._crash_recovery is not None:
                self._crash_recovery.flush(timeout=1.0)
        except Exception:
            pass

    def _install_signal_handlers(self):
        """Install SIGINT/SIGTERM handlers for graceful shutdown.

        PROD-003: On POSIX there was no signal handler, so Ctrl+C
        would kill the process without running quit() cleanup
        (stop hotkeys, restore volume, release mutex). This method
        installs handlers that trigger quit() on a separate thread
        to avoid deadlock when the main thread is inside the signal
        handler.
        """
        import signal
        def _signal_handler(signum, frame):
            sig_name = signal.Signals(signum).name
            log.info("[SIGNAL] %s received, shutting down gracefully", sig_name)
            # Run quit on a separate thread to avoid deadlock.
            # RACE-016: daemon=True is acceptable because quit() is
            # idempotent and the atexit handler covers critical cleanup.
            threading.Thread(target=self.quit, daemon=True).start()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _signal_handler)
            except (OSError, ValueError):
                # SIGTERM not available on Windows; signal.signal can
                # raise if not in the main thread
                pass

    def _install_win32_console_handler(self):
        """On Windows, install a console control handler to survive console closure.

        ARCH-046: skip when running under ``pythonw.exe`` — there's no
        console attached, so SetConsoleCtrlHandler is a no-op that
        spews "no console" warnings in the log.
        """
        if not is_windows():
            return
        # ARCH-046: detect pythonw.exe (no console) and skip install.
        exe_name = Path(sys.executable).name.lower()
        if exe_name == "pythonw.exe":
            log.debug("[WIN32] pythonw.exe detected — skipping console control handler")
            return

        try:
            import ctypes
            from ctypes import wintypes

            CTRL_C_EVENT = 0
            CTRL_BREAK_EVENT = 1
            CTRL_CLOSE_EVENT = 2
            CTRL_LOGOFF_EVENT = 5
            CTRL_SHUTDOWN_EVENT = 6

            HANDLER_ROUTINE = ctypes.CFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

            self._console_handler = HANDLER_ROUTINE(self._win32_console_handler)
            self._kernel32 = ctypes.windll.kernel32
            kernel32 = self._kernel32
            kernel32.SetConsoleCtrlHandler.argtypes = [HANDLER_ROUTINE, wintypes.BOOL]
            kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
            kernel32.FreeConsole.argtypes = []
            kernel32.FreeConsole.restype = wintypes.BOOL

            result = kernel32.SetConsoleCtrlHandler(self._console_handler, True)
            if result:
                log.info("[WIN32] Console control handler installed")
            else:
                log.warning("[WIN32] SetConsoleCtrlHandler failed")
        except Exception:
            log.exception("[WIN32] Failed to install console control handler")

    def _win32_console_handler(self, ctrl_type):
        """Callback for Windows console control events."""
        CTRL_C_EVENT = 0
        CTRL_BREAK_EVENT = 1
        CTRL_CLOSE_EVENT = 2
        CTRL_LOGOFF_EVENT = 5
        CTRL_SHUTDOWN_EVENT = 6

        if ctrl_type == CTRL_CLOSE_EVENT:
            log.info(
                "[WIN32] Console window closing -- "
                "keeping process alive (tray app survives)"
            )
            try:
                self._kernel32.FreeConsole()
                # PERF-004: reuse the existing devnull object instead of
                # opening a new one on every CTRL_CLOSE_EVENT (would hit
                # Windows' 10,000 handle cap after ~250 RDP logout cycles).
                if getattr(self, "_devnull", None) is None or self._devnull.closed:
                    self._devnull = open(os.devnull, 'w')
                    _register_devnull_file(self._devnull)
                sys.stdout = self._devnull
                sys.stderr = self._devnull
                log.info("[WIN32] Detached from console (FreeConsole)")
            except Exception:
                log.warning("[WIN32] FreeConsole() failed")
            return True

        if ctrl_type in (CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT):
            log.info("[WIN32] System event %d received, shutting down", ctrl_type)
            # RACE-016: daemon=True is acceptable because quit() is
            # idempotent and the atexit handler covers critical cleanup.
            threading.Thread(target=self.quit, daemon=True).start()
            return True

        if ctrl_type in (CTRL_C_EVENT, CTRL_BREAK_EVENT):
            log.info("[WIN32] Ctrl+C received, shutting down")
            # RACE-016: daemon=True is acceptable because quit() is
            # idempotent and the atexit handler covers critical cleanup.
            threading.Thread(target=self.quit, daemon=True).start()
            return True

        return False


# SEC-001: restart token functions moved to voice_typer.server.security
from voice_typer.server.security import (
    generate_restart_token as _generate_restart_token,
    verify_restart_token as _verify_restart_token,
    consume_restart_token as _consume_restart_token,
)


def _create_restrictive_security_attributes():
    """SEC-001: Create a SECURITY_ATTRIBUTES with a restrictive DACL.

    Builds a Win32 SECURITY_ATTRIBUTES structure whose DACL allows only
    the current user (SID) to access the named mutex. This prevents other
    user sessions from opening or manipulating our mutex object.

    Returns a ctypes SECURITY_ATTRIBUTES structure, or None on failure
    (in which case the default NULL DACL is used — still functional but
    less restrictive).
    """
    if not is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.windll.advapi32
        kernel32 = ctypes.windll.kernel32

        # Get current process token
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            0x0008,  # TOKEN_QUERY
            ctypes.byref(token),
        ):
            return None
        try:
            # Get required buffer size for TokenUser
            ret_len = wintypes.DWORD()
            advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(ret_len))
            buf = ctypes.create_string_buffer(ret_len.value)
            if not advapi32.GetTokenInformation(
                token, 1, buf, ret_len.value, ctypes.byref(ret_len)
            ):
                return None

            # Extract SID from TOKEN_USER structure
            # TOKEN_USER: SID_AND_ATTRIBUTES (pSid, dwAttributes)
            p_sid = ctypes.cast(
                ctypes.addressof(buf) + ctypes.sizeof(wintypes.LPVOID),
                ctypes.POINTER(wintypes.LPVOID),
            )[0]
            if not p_sid:
                return None

            # Build a SECURITY_DESCRIPTOR with a DACL containing only
            # one ACE: grant GENERIC_ALL to the current user SID.
            sd_size = 1024
            sd = ctypes.create_string_buffer(sd_size)
            if not advapi32.InitializeSecurityDescriptor(sd, 1):  # SECURITY_DESCRIPTOR_REVISION
                return None

            # Build an explicit access array for the current user
            class EXPLICIT_ACCESS(ctypes.Structure):
                _fields_ = [
                    ("grfAccessPermissions", wintypes.DWORD),
                    ("grfAccessMode", wintypes.DWORD),
                    ("grfInheritance", wintypes.DWORD),
                    ("Trustee", ctypes.c_byte * 64),  # TRUSTEE is variable-size
                ]

            ea = EXPLICIT_ACCESS()
            # Grant all access
            ctypes.memset(ctypes.byref(ea), 0, ctypes.sizeof(ea))
            ea.grfAccessPermissions = 0x1F0003  # MUTEX_ALL_ACCESS
            ea.grfAccessMode = 0  # GRANT_ACCESS
            ea.grfInheritance = 0  # NO_INHERITANCE

            # Build TRUSTEE manually
            # TRUSTEE_IS_SID = 0, TRUSTEE_IS_WELL_KNOWN_GROUP = 5
            # Simplified: use SetEntriesInAcl with the SID
            trustee_bytes = ctypes.create_string_buffer(64)
            ctypes.memset(trustee_bytes, 0, 64)
            # pMultipleTrustee = NULL
            # MultipleTrusteeOperation = 0 (NO_MULTIPLE_TRUSTEE)
            # TrusteeForm = 0 (TRUSTEE_IS_SID)
            # TrusteeType = 1 (TRUSTEE_IS_USER)
            # ptstrName = pSid
            offset = ctypes.sizeof(wintypes.LPVOID)  # pMultipleTrustee
            offset += ctypes.sizeof(wintypes.DWORD)  # MultipleTrusteeOperation
            offset += ctypes.sizeof(wintypes.DWORD)  # TrusteeForm
            offset += ctypes.sizeof(wintypes.DWORD)  # TrusteeType
            ctypes.memmove(
                ctypes.addressof(trustee_bytes) + offset,
                ctypes.byref(ctypes.c_void_p(p_sid)),
                ctypes.sizeof(ctypes.c_void_p),
            )
            # Copy the trustee fields into ea
            ctypes.memmove(ctypes.byref(ea.Trustee), trustee_bytes, 64)

            # Set the DACL
            new_acl = wintypes.LPVOID()
            if not advapi32.SetEntriesInAclW(
                1, ctypes.byref(ea), None, ctypes.byref(new_acl)
            ):
                # Fallback: use a simpler approach with NULL DACL
                if not advapi32.SetSecurityDescriptorDacl(sd, True, None, False):
                    return None
            else:
                if not advapi32.SetSecurityDescriptorDacl(sd, True, new_acl, False):
                    return None

            # Build SECURITY_ATTRIBUTES
            class SECURITY_ATTRIBUTES(ctypes.Structure):
                _fields_ = [
                    ("nLength", wintypes.DWORD),
                    ("lpSecurityDescriptor", wintypes.LPVOID),
                    ("bInheritHandle", wintypes.BOOL),
                ]

            sa = SECURITY_ATTRIBUTES()
            sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
            sa.lpSecurityDescriptor = ctypes.cast(sd, wintypes.LPVOID)
            sa.bInheritHandle = False
            # Keep references alive so they don't get GC'd while the mutex holds them
            sa._sd_ref = sd
            sa._acl_ref = new_acl
            return sa
        finally:
            kernel32.CloseHandle(token)
    except Exception:
        # If we can't build a restrictive DACL, return None and fall back
        # to default (NULL) security attributes
        return None


def _ensure_single_instance(silent=False):
    """Enforce single-instance via a Windows named mutex.

    Returns the mutex handle (kept alive to hold the lock) on Windows,
    or None on other platforms.
    Skipped when VOICE_TYPER_RESTART env var is set (restart flow).

    Parameters
    ----------
    silent : bool
        If True, skip the MessageBoxW dialog (caller handles UX).

    On duplicate launch, Windows returns ``ERROR_ALREADY_EXISTS`` from
    ``CreateMutexW`` — this is the authoritative signal that another
    instance owns the lock.  We bail immediately.  (Previously the code
    second-guessed Windows with a flaky ``wmic``-based process scan and,
    when that scan returned False, proceeded to create a *new* mutex —
    which let duplicate backends run simultaneously, causing each
    recording to be transcribed and pasted N times.)

    SEC-001: Uses "Local\\VoiceTyperSingleInstance" with a restrictive
    DACL (only current user SID) to prevent cross-session mutex attacks.
    The VOICE_TYPER_RESTART bypass is time-limited to 30 seconds — the
    restart token file must have been modified within the last 30 seconds
    for the bypass to be accepted.
    """
    if not is_windows():
        return None

    # Skip mutex check during restart -- old instance releases mutex on quit
    if os.environ.get("VOICE_TYPER_RESTART"):
        if _verify_restart_token():
            # SEC-001 (revised): Time-limit the restart bypass — only
            # allow if the restart token was generated within the last
            # 30 seconds. The previous code used ``time.time() - mtime``
            # which is vulnerable to system clock jumps (NTP sync,
            # daylight saving, manual changes). If the clock jumps
            # backward, age goes negative (silently bypassing the 30s
            # window); if forward, age gets inflated (false denials).
            #
            # Fix: detect clock-jump anomalies (negative age or age > 1 day)
            # and deny the bypass in those cases. The 30s window is short
            # enough that legitimate restarts won't be affected, but a
            # 1-day cap catches clock-jump corruption.
            try:
                from voice_typer.server.config import _config_dir
                token_path = _config_dir() / ".restart_token"
                if token_path.exists():
                    mtime = token_path.stat().st_mtime
                    age = time.time() - mtime
                    # SEC-001: detect clock jumps
                    if age < 0:
                        log.warning(
                            "[STARTUP] Restart token age is negative (%.1fs) — "
                            "system clock may have jumped backward. Blocking "
                            "duplicate launch to be safe.", age,
                        )
                        if not silent and sys.stderr is not None:
                            print(
                                "Voice Typer: clock jump detected, duplicate launch blocked.",
                                file=sys.stderr,
                            )
                        sys.exit(1)
                    if age > 86400.0:  # > 1 day — almost certainly a clock jump
                        log.warning(
                            "[STARTUP] Restart token age is suspiciously large "
                            "(%.1fs > 86400s) — system clock may have jumped "
                            "forward. Blocking duplicate launch.", age,
                        )
                        if not silent and sys.stderr is not None:
                            print(
                                "Voice Typer: clock jump detected, duplicate launch blocked.",
                                file=sys.stderr,
                            )
                        sys.exit(1)
                    if age > 30.0:
                        log.warning(
                            "[STARTUP] Restart token too old (%.1fs > 30s) — "
                            "blocking duplicate launch", age,
                        )
                        # Don't consume the token; let it expire naturally
                        if not silent and sys.stderr is not None:
                            print(
                                "Voice Typer: restart token expired, duplicate launch blocked.",
                                file=sys.stderr,
                            )
                        sys.exit(1)
            except SystemExit:
                raise  # don't catch sys.exit
            except Exception:
                # If we can't check the time, deny the bypass (safe default)
                log.warning("[STARTUP] Cannot verify restart token age — blocking duplicate")
                sys.exit(1)
            # Valid and recent restart token — consume it
            _consume_restart_token()
            return None
        # Invalid token — treat as duplicate launch
        log.warning("[STARTUP] VOICE_TYPER_RESTART set but token invalid — blocking duplicate")

    import ctypes

    ERROR_ALREADY_EXISTS = 183
    ERROR_ACCESS_DENIED = 5

    # SEC-001: Create a SECURITY_ATTRIBUTES with a restrictive DACL that
    # only allows the current user to access the mutex. This prevents
    # other sessions/users from opening or manipulating our mutex.
    # PLAT-RUN: Include the installation path hash in the mutex name
    # so different installations don't conflict (e.g. stable vs dev).
    import hashlib
    install_hash = hashlib.sha256(sys.executable.encode()).hexdigest()[:8]
    mutex_name = f"Local\\VoiceTyperSingleInstance_{install_hash}"

    # Build a restrictive DACL for the mutex
    sa = _create_restrictive_security_attributes()
    lp_mutex_attributes = ctypes.byref(sa) if sa is not None else None

    # Use CreateMutexW with bInitialOwner=True so WE own the handle.
    # The Windows mutex handle is inheritable across CreateProcess /
    # subprocess.Popen, so a child spawned by the parent will see the
    # mutex as already owned.  We can't disable handle inheritance from
    # Python; the inheritance concern is real but handled separately:
    # Electron's main process kills stale backends before spawning, and
    # the restart path sets VOICE_TYPER_RESTART to skip this check.
    mutex = ctypes.windll.kernel32.CreateMutexW(
        lp_mutex_attributes, True, mutex_name
    )
    last_error = ctypes.windll.kernel32.GetLastError()
    if last_error == ERROR_ALREADY_EXISTS:
        # Windows guarantees: this means another process holds the mutex
        # RIGHT NOW.  Trust it — no need to scan for the competing
        # process (DEAD-013: the old _another_voice_typer_alive() scan
        # had zero decision power — the mutex already proved a
        # duplicate, and the scan result only affected a log message).
        log.info("[STARTUP] Duplicate launch blocked (mutex already held)")
        if not silent:
            msg = "Voice Typer is already running. Only one instance is allowed."
            try:
                ctypes.windll.user32.MessageBoxW(
                    0, msg, "Voice Typer",
                    0x00000030 | 0x00000000,  # MB_ICONWARNING | MB_OK
                )
            except Exception:
                if sys.stderr is not None:
                    print(msg, file=sys.stderr)
        if mutex:
            ctypes.windll.kernel32.CloseHandle(mutex)
        sys.exit(1)
    elif last_error == ERROR_ACCESS_DENIED:
        # Couldn't even open the mutex; bail safely.
        if not silent and sys.stderr is not None:
            print("Voice Typer: mutex access denied.", file=sys.stderr)
        sys.exit(1)
    return mutex


# DEAD-013: _another_voice_typer_alive() deleted.
# The Win32 named mutex (VoiceTyperSingleInstance) already proves a
# duplicate exists when ERROR_ALREADY_EXISTS is returned — the scan
# had zero decision power (its result only affected a log message).





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
        pass  # Not available on all platforms

    from voice_typer.server.ipc_server import main as ipc_main
    ipc_main()
