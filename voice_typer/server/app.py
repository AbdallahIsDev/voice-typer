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

# Re-exported for monkeypatch.setattr("voice_typer.server.app.X", ...) in tests
# and for runtime lookups from voice_typer.server.startup_tasks.  # ruff: noqa: F401
from voice_typer.server import task_scheduler
from voice_typer.server.audio_processor import AudioProcessor
from voice_typer.server.audio_quality import AudioQualityAnalyzer
from voice_typer.server.branding import APP_NAME
from voice_typer.server.clipboard import ClipboardManager
from voice_typer.server.config import Config, _config_dir, _migrate_from_legacy
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

# SEC-001: restart token functions moved to voice_typer.server.security
# COMPAT-001: backward-compat re-export for tests/test_pii_redaction.py
# which imports _PIIRedactionFilter from app. The class lives in
# voice_typer.server.security as PIIRedactionFilter (no underscore).
from voice_typer.server.security import PIIRedactionFilter as _PIIRedactionFilter  # noqa: F401
from voice_typer.server.security import (
    consume_restart_token as _consume_restart_token,
)
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
from voice_typer.server.settings import SettingsController, SettingsWindow
from voice_typer.server.streaming import (
    StreamingTranscriptionSession,  # noqa: F401  (re-exported for tests/test_app.py monkeypatch)
)
from voice_typer.server.text_cleanup import clean_transcribed_text, configure_corrections
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

    _bool_vars = {"VOICE_TYPER_QUIET", "VOICE_TYPER_DEBUG", "VOICE_TYPER_NO_TRAY", "VOICE_TYPER_STREAMING"}
    _bool_pattern = re.compile(r"^(1|0|true|false|yes|no)$", re.IGNORECASE)
    _token_pattern = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")
    _path_pattern = re.compile(r'^[^\0]+$')  # no null bytes

    for var in _bool_vars:
        val = os.environ.get(var)
        if val is not None and not _bool_pattern.match(val):
            log.warning(
                "[ENV] Invalid value for %s=%r -- expected boolean (1/0/true/false/yes/no). Resetting to empty.",
                var, val,
            )
            os.environ.pop(var, None)

    restart_val = os.environ.get("VOICE_TYPER_RESTART")
    if restart_val is not None and not _token_pattern.match(restart_val):
        log.warning(
            ("[ENV] Invalid value for VOICE_TYPER_RESTART=<redacted> -- "
            "expected alphanumeric token. Resetting to empty."),
        )
        os.environ.pop("VOICE_TYPER_RESTART", None)

    config_dir = os.environ.get("VOICE_TYPER_CONFIG_DIR")
    if config_dir is not None and (not _path_pattern.match(config_dir) or len(config_dir) > 4096):
        log.warning(
            "[ENV] Invalid value for VOICE_TYPER_CONFIG_DIR=%r -- expected valid path. Resetting to empty.",
            config_dir,
        )
        os.environ.pop("VOICE_TYPER_CONFIG_DIR", None)

    ipc_token = os.environ.get("VOICE_TYPER_IPC_TOKEN")
    if ipc_token is not None and not _token_pattern.match(ipc_token):
        log.warning(
            ("[ENV] Invalid value for VOICE_TYPER_IPC_TOKEN=<redacted> -- "
            "expected alphanumeric token. Resetting to empty."),
        )
        os.environ.pop("VOICE_TYPER_IPC_TOKEN", None)

    # SEC-audit-011: Validate SystemRoot on Windows to prevent DLL injection
    from voice_typer.server.config import _validate_systemroot
    _validate_systemroot()

    # PLAT-008: Validate HF_HOME is a valid path if set
    hf_home = os.environ.get("HF_HOME")
    if hf_home is not None and (not _path_pattern.match(hf_home) or len(hf_home) > 4096):
        log.warning(
            "[ENV] Invalid value for HF_HOME=%r -- expected valid path. Resetting to empty.",
            hf_home,
        )
        os.environ.pop("HF_HOME", None)


class VoiceTyperApp:
    """The main application."""

    # PERF-BUBBLE-001 (Round 0 forward-port): declared at class scope
    # (not just in __init__) so ``MagicMock(spec=VoiceTyperApp)`` in
    # ``tests/test_waveform_bubble.py`` auto-creates a truthy mock
    # attribute instead of raising AttributeError when
    # ``_push_bubble_level`` reads it. ``__init__`` re-sets it to
    # ``False`` on real instances so the level-push gate fires until
    # the bubble is shown.
    _bubble_visible: bool = False

    def __init__(self):
        self.config = Config.load()

        # Startup banner -- first visible log, before any subsystem init
        log.info(
            "%s starting -- model=%s, hotkey=%s, mic=%s, sample_rate=%s",
            APP_NAME, self.config.model_size, self.config.hotkey,
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
        self._settings_window: SettingsWindow | None = None
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
        # TASK-14: declare ``_ipc_server`` upfront so VoiceTyperApp
        # satisfies the ``AppProtocol`` structural type checked by
        # ``providers.build_ipc_server``.  The attribute is set later
        # by ``IPCServer.start()`` (``self.app._ipc_server = self``);
        # initializing it to ``None`` here means pyrefly sees the
        # attribute exists on every instance, satisfying the protocol.
        self._ipc_server: Any | None = None
        # PERF-BUBBLE-001 (Round 0 forward-port): instance-level reset so
        # the level-push gate fires until the bubble is shown.  The class
        # attribute above is only for MagicMock(spec=...) compatibility.
        self._bubble_visible: bool = False
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
                APP_NAME,
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

    def _restore_volume(self, fade_ms: int | None = None) -> None:
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

    def _get_active_transcriber(self):
        """Test seam — kept for monkeypatch compatibility.

        RW-9 Phase 2: production callers invoke ``self.models.active_transcriber()``
        directly (recording_controller.py + dictation_pipeline.py).
        This delegate remains for tests that monkeypatch it.
        """
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
        """Test seam — kept for monkeypatch compatibility.

        RW-9 Phase 2: production callers invoke ``self.recording.get_streaming_session()``
        directly (dictation_pipeline.py + _do_cleanup). This delegate
        remains for tests that monkeypatch it.
        """
        return self.recording.get_streaming_session()

    def _set_streaming_session(self, session_or_none):
        """Test seam — kept for monkeypatch compatibility.

        RW-9 Phase 2: production callers invoke ``self.recording.set_streaming_session(...)``
        directly (dictation_pipeline.py + _do_cleanup). This delegate
        remains for tests that monkeypatch it.
        """
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
            # PERF-BUBBLE-001 (Round 0 forward-port): mark the bubble as
            # visible so the level pusher (firing from the audio callback
            # at ~60 Hz) starts forwarding samples again. Paired with
            # _push_bubble_hide.
            self._bubble_visible = True
            sent = _push_event_now({"type": "bubble_show"})
            log.info("[WAVEFORM] bubble.show() fired; push=%s", "OK" if sent else "NO IPC")

        def _push_bubble_hide() -> None:
            # PERF-BUBBLE-001 (Round 0 forward-port): mark hidden first
            # so any in-flight _push_bubble_level call queued behind this
            # hide sees the updated flag and skips its IPC push.
            self._bubble_visible = False
            _push_event_now({"type": "bubble_hide"})

        def _push_bubble_level(rms: float, peak: float) -> None:
            # PERF-BUBBLE-001 (Round 0 forward-port): early-return when
            # the bubble is hidden. The audio callback fires this
            # listener at the device's native chunk rate (~31 Hz @ 16 kHz
            # / blocksize 512, ~94 Hz @ 48 kHz). When the bubble isn't on
            # screen, every push wastes CPU on json.dumps + queue.put +
            # IPC writer thread wake-up, and Electron has to receive and
            # discard the message. Gating here eliminates ~60 Hz of
            # wasted IPC while the user is not dictating.
            if not self._bubble_visible:
                return
            # PERF-NEW-001 / PERF-NEW-015: this callback fires from the
            # PortAudio thread at the device's native chunk rate
            # (~31 Hz @ 16 kHz / blocksize 512, ~94 Hz @ 48 kHz).
            # Calling _push_event_now directly was holding the IPC
            # server's _lock for json.dumps + socket.sendall, which on
            # a slow Electron receive window stalled the audio thread
            # and triggered xruns.  We push the actual IPC send to a
            # background queue drained by a low-priority daemon thread.
            #
            # BUBBLE-FIX-4.1: the previous throttle (33 ms / ~30 Hz) sat
            # exactly at the 32 ms chunk interval for 16 kHz devices, so
            # PortAudio timing jitter caused irregular accept/drop
            # patterns and the visualizer froze.  Lowered to 16 ms
            # (~60 Hz) so every chunk is delivered; the bounded queue
            # (maxsize=64) and worker thread handle backpressure.  Each
            # message is ~40 bytes JSON, so 60 msg/s is trivial for TCP.
            now = time.monotonic()
            last = getattr(self, "_last_bubble_level_push_ts", 0.0)
            if now - last < 0.016:  # 16 ms = ~60 Hz
                return
            self._last_bubble_level_push_ts = now
            q = getattr(self, "_bubble_level_queue", None)
            if q is None:
                return  # wiring not complete yet
            with contextlib.suppress(queue.Full):
                # Queue is full — the worker thread fell behind.  Drop
                # this sample; the next one will pick up the latest
                # smoothed level from update_level's low-pass filter.
                q.put_nowait({
                    "type": "bubble_level",
                    "data": {"rms": float(rms), "peak": float(peak)},
                })

        # PERF-NEW-001: dedicated queue + worker thread for bubble
        # level pushes.  Bounded so a stuck Electron client can't
        # cause unbounded memory growth on the Python side.  Created
        # idempotently — if _wire_waveform_bubble is called twice
        # (e.g. in tests after a stop/start cycle), the existing
        # queue and worker are reused.
        if not hasattr(self, "_bubble_level_queue") or self._bubble_level_queue is None:
            self._bubble_level_queue: queue.Queue[dict | None] = queue.Queue(maxsize=64)
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

        def _push_bubble_set_state(state: str) -> None:
            _push_event_now({
                "type": "bubble_set_state",
                "data": {"state": state},
            })

        self._waveform_bubble.on_show = _push_bubble_show
        self._waveform_bubble.on_hide = _push_bubble_hide
        self._waveform_bubble.on_level = _push_bubble_level
        self._waveform_bubble.on_set_state = _push_bubble_set_state
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

    def _do_startup(self) -> None:
        """Background work: sync autostart, load mics, load model, register hotkey.

        RW-9 Phase 5: the body of this method (~340 lines) was extracted
        into :class:`voice_typer.server.startup_sequence.StartupSequence`
        to reduce the god-class size of ``VoiceTyperApp``.  The phase
        ordering, RACE-020 shutdown gates, parallel executor semantics,
        and onboarding auto-heal logic are all preserved verbatim — see
        the docstring on ``StartupSequence.run`` for the full rationale.

        Tests that call ``app._do_startup()`` directly still work; tests
        that monkeypatch delegate methods like ``app._sync_autostart``
        must now monkeypatch the controller instead (e.g.
        ``monkeypatch.setattr(startup_tasks, "sync_autostart", ...)``
        or ``app.hotkeys.register = MagicMock()``).
        """
        from voice_typer.server.startup_sequence import StartupSequence
        StartupSequence(self).run()

    def _sync_autostart(self) -> None:
        """Test seam — kept for monkeypatch compatibility.

        RW-9 Phase 2: production callers (``_do_startup`` via
        ``startup_tasks.sync_autostart(self)``) invoke the controller
        directly. This delegate remains so existing tests that do
        ``monkeypatch.setattr(app, "_sync_autostart", ...)`` keep working.
        """
        from voice_typer.server.startup_tasks import sync_autostart

        sync_autostart(self)

    def _sync_prewarm_task(self, shutdown_event=None) -> None:
        """Test seam — kept for monkeypatch compatibility.

        RW-9 Phase 2: production callers invoke ``startup_tasks.sync_prewarm_task``
        directly. This delegate remains for tests that monkeypatch it.
        """
        from voice_typer.server.startup_tasks import sync_prewarm_task

        sync_prewarm_task(self, shutdown_event)

    def _ensure_desktop_shortcut(self) -> None:
        """Test seam — kept for monkeypatch compatibility.

        RW-9 Phase 2: production callers invoke ``startup_tasks.ensure_desktop_shortcut``
        directly. This delegate remains for tests that monkeypatch it.
        """
        from voice_typer.server.startup_tasks import ensure_desktop_shortcut

        ensure_desktop_shortcut(self)

    def _load_microphones(self, shutdown_event=None) -> None:
        """Test seam — kept for monkeypatch compatibility.

        RW-9 Phase 2: production callers invoke ``startup_tasks.load_microphones``
        directly. This delegate remains for tests that monkeypatch it.

        The extracted function compares old_ids vs new_ids (the cached
        device-id set vs the freshly enumerated one) and pushes a
        ``microphones_changed`` IPC event when the device set changes,
        so the Electron renderer can refresh its microphone dropdown
        without a manual "Refresh" click.
        """
        from voice_typer.server.startup_tasks import load_microphones

        load_microphones(self, shutdown_event)

    def _start_accessibility_pulse(self, initial_state: bool) -> None:
        """Test seam — kept for monkeypatch compatibility.

        RW-9 Phase 2: production callers invoke ``startup_tasks.start_accessibility_pulse``
        directly. This delegate remains for tests that monkeypatch it.
        """
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
        """Test seam — kept for monkeypatch compatibility.

        RW-9 Phase 2: production callers invoke ``self.hotkeys.register()``
        directly. This delegate remains for tests that monkeypatch it.
        """
        self.hotkeys.register()
    def _register_esc_hotkey(self):
        """Test seam — kept for monkeypatch compatibility.

        RW-9 Phase 2: production callers invoke ``self.hotkeys.register_esc()``
        directly. This delegate remains for tests that monkeypatch it.
        """
        self.hotkeys.register_esc()
    def _unregister_esc_hotkey(self):
        """Test seam — kept for monkeypatch compatibility.

        RW-9 Phase 2: production callers invoke ``self.hotkeys.unregister_esc()``
        directly. This delegate remains for tests that monkeypatch it.
        """
        self.hotkeys.unregister_esc()
    def _register_repaste_hotkey(self):
        """Test seam — kept for monkeypatch compatibility.

        RW-9 Phase 2: production callers invoke ``self.hotkeys.register_repaste()``
        directly. This delegate remains for tests that monkeypatch it.
        """
        self.hotkeys.register_repaste()
    # ─── Dictation ─────────────────────────────────────────────────────

    def toggle_dictation(self):
        """#2 (Round 9): delegate to RecordingController.toggle()."""
        self.recording.toggle()
    def _start_dictation(self):
        """#2 (Round 9): delegate to RecordingController.start()."""
        self.recording.start()
    def _on_recorder_rms(self, rms: float, peak: float, audio_chunk=None) -> None:
        """DEAD — pinned by test_e2e_smoke + test_waveform_bubble signature checks.

        RW-9 Phase 1: this 3-line delegate has no production callers (the
        RecordingController wires ``on_recorder_rms`` directly via
        ``self._app._on_recorder_rms`` in ``recording.py:1621``).  Kept as a
        thin facade because two tests assert
        ``inspect.signature(VoiceTyperApp._on_recorder_rms)`` contains the
        ``audio_chunk`` parameter — see ``test_e2e_smoke.py:113`` and
        ``test_waveform_bubble.py:512``.  Delete those signature checks
        before removing this method.
        """
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
            # 17-C-FIX-3: _rms_values was removed (write-only list);
            # we no longer append to it here.
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
        """#2 (Round 9): delegate to RecordingController._cancel_streaming_session()."""
        self.recording._cancel_streaming_session()
    def _force_recover_from_stuck_transcription(self, force: bool = False):
        """Test seam — kept for monkeypatch compatibility.

        RW-9 Phase 2: production callers (tray.py force-cancel menu item)
        invoke ``self.recording._force_recover_from_stuck_transcription(force=...)``
        directly. This delegate remains for tests that monkeypatch it.

        PR-2 Finding #3: accepts an optional ``force`` parameter.  When
        ``True``, the recovery proceeds even if the transcription worker
        thread is still alive, providing a manual escape hatch for users
        whose transcription is genuinely stuck.  The tray menu's "Cancel
        Transcription" item calls this with ``force=True``.
        """
        self.recording._force_recover_from_stuck_transcription(force=force)
    # ─── Settings / Microphone ─────────────────────────────────────────

    def repaste_last(self) -> None:
        """Feature: Repaste last transcription (tray menu + hotkey).

        ERR-018: previously a single try/except collapsed clipboard-copy
        failures and paste-keystroke failures into one generic toast.
        We now split them so the user knows which step failed.
        """
        if not self._last_transcription:
            self.tray.notify(APP_NAME, "No previous transcription to re-paste.")
            return

        # Step 1: copy to clipboard
        try:
            self.clipboard.copy(self._last_transcription)
        except Exception as e:
            log.warning("[REPASTE] Clipboard copy failed: %s", e)
            self.tray.notify(
                APP_NAME,
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
            self.tray.notify(APP_NAME, "Last transcription re-pasted")
        except Exception as e:
            log.warning("[REPASTE] Paste keystroke failed: %s", e)
            self.tray.notify(
                APP_NAME,
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
            self.tray.notify(APP_NAME, "Nothing to undo.")
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
            self.tray.notify(APP_NAME, f"Undid last transcription ({char_count} chars)")
        except ImportError:
            log.warning("[UNDO] pynput not available for undo")
            self.tray.notify(APP_NAME, "Undo not available (pynput missing)")
        except Exception as e:
            log.warning("[UNDO] Failed: %s", e)
            self.tray.notify(APP_NAME, f"Undo failed: {e}")

    def _cancel_dictation(self):
        """#2 (Round 9): delegate to RecordingController.cancel().

        ESC-FIX-001: If _esc_cancel_paused is True (the frontend
        HotkeyPicker is in hotkey capture mode), the ESC cancel is a
        no-op — the frontend owns the Escape key while capturing.
        """
        if self._esc_cancel_paused:
            log.debug("[CANCEL] ESC cancel paused (frontend hotkey capture) — no-op")
            return
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
            self.tray.notify(APP_NAME, f"Could not change autostart setting.\n{e}")

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
            self.tray.notify(APP_NAME, f"Microphone next recording: {label}")
            return

        self.recorder = Recorder(self.config, audio_processor=self._audio_processor)  # re-create with new mic
        log.info("[CONFIG] Microphone changed to: %s", label)
        self.tray.notify(APP_NAME, f"Microphone: {label}")

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
                systemroot = os.environ.get("SYSTEMROOT", r"C:\Windows")
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
                        with contextlib.suppress(Exception):
                            proc.wait()
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
            self.tray.notify(APP_NAME, f"Config file:\n{config_file}")

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
        """TrayController protocol: change hotkey."""
        self._restart_hotkey(hotkey)

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
        if self._shutting_down:
            log.debug("[QUIT] Already shutting down, ignoring duplicate quit_app call")
            return
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
        log.info("[RESTART] Restarting %s...")

        # ── THEME-RESTART-FIX: save the config before push ───────────
        # Save any pending in-memory config changes (e.g. a theme preset
        # change that was set via `set_config` but whose save completed
        # while the user navigated to the tray menu) to disk before the
        # restart sequence begins.  This ensures the new Python process
        # loads the latest config, preventing the theme from reverting
        # to default after a restart.
        try:
            self.config.save()
        except Exception:
            log.debug("[RESTART] config.save() before push failed", exc_info=True)

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
        # RACE-020: also set the Event version so executor tasks can
        # check it (matches quit()'s shutdown signaling — important now
        # that restart_app() shares the same _do_cleanup() body).
        self._shutting_down_event.set()
        self._restore_volume(fade_ms=0)
        log.info("[RESTART] Pausing 300ms for Electron to process relaunch_electron")
        time.sleep(0.3)

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
        self._do_cleanup()

        # 4. Exit cleanly — electron will relaunch us.
        log.info("[RESTART] Old process exiting via sys.exit(0)")
        sys.exit(0)

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
        """RW-3: shared cleanup body used by ``quit()``, ``restart_app()``,
        and ``_atexit_cleanup()``.

        Performs ALL the cleanup that ``quit()`` previously did inline,
        EXCEPT the final ``sys.exit(0)``.  Every operation is guarded by
        a None-check or try-except so the method is IDEMPOTENT — calling
        it twice (e.g. once from ``quit()`` and once from the atexit
        safety net) is a no-op on the second call.

        The caller is responsible for setting ``self._shutting_down = True``
        and ``self._shutting_down_event.set()`` BEFORE calling this
        method so the atexit safety net doesn't double-cleanup. The
        ``_cleanup_done`` flag below is the hard guarantee: once set,
        every subsequent call returns immediately.

        Prior to RW-3, ``restart_app()`` did only a PARTIAL cleanup
        (cancel timers, stop hotkey backends, stop tray) and skipped:
          - ``history_db.flush()`` — pending transcription history
            writes were silently lost
          - ``_crash_recovery.flush()`` / ``shutdown()`` — pending
            recovery writes were lost
          - ``recorder.shutdown_mic_watcher()`` — mic watcher daemon
            thread leaked
          - ``recorder.stop()`` / ``discard()`` — PortAudio stream
            not closed
          - ``_bubble_level_worker`` stop — daemon thread leaked
          - ``_clear_backend_pid_file()`` — stale PID file remained
          - Win32 mutex handle close

        The ``_atexit_cleanup`` safety net's ``_shutting_down`` guard
        meant it was completely DISABLED when ``restart_app()`` set
        ``_shutting_down = True``, so the safety net couldn't pick up
        the slack. Extracting the shared body here fixes both bugs.
        """
        # Idempotency guard — once cleanup has run, subsequent calls
        # are no-ops. This is the hard safety that lets
        # _atexit_cleanup() call us unconditionally after
        # quit()/restart_app() already ran.
        if getattr(self, "_cleanup_done", False):
            return
        self._cleanup_done = True

        # Cancel all pending timers
        try:
            self._cancel_pending_timers()
        except Exception:
            log.debug("[CLEANUP] _cancel_pending_timers failed", exc_info=True)

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
        try:
            # RW-9 Phase 2: call RecordingController directly.
            session = self.recording.get_streaming_session()
            self.recording.set_streaming_session(None)
            if session is not None:
                session._cancel_event.set()
        except Exception:
            log.debug("[CLEANUP] streaming session cancel failed", exc_info=True)

        # PROD-003: Close PortAudio stream properly.
        # recorder.stop() fully closes the PortAudio stream (stop + close),
        # while discard() just clears the recording flag. Use stop() first
        # for a clean shutdown, then discard() as fallback if stop() fails.
        try:
            if self.recorder is not None and self.recorder.recording:
                try:
                    self.recorder.stop()
                except Exception as e:
                    log.warning("[SHUTDOWN] recorder.stop() failed: %s, trying discard()", e)
                    try:
                        self.recorder.discard()
                    except Exception as e2:
                        log.warning("[SHUTDOWN] recorder.discard() also failed: %s", e2)
        except Exception:
            log.debug("[CLEANUP] recorder stop/discard failed", exc_info=True)

        # PERF-MIC-001: stop the OS-event device watcher so its daemon
        # thread exits cleanly before the process tears down. Best-effort
        # — the thread is a daemon and would die on process exit anyway,
        # but explicit stop() avoids a 2s join race during GC.
        try:
            if self.recorder is not None:
                self.recorder.shutdown_mic_watcher()
        except Exception as e:
            log.debug("[SHUTDOWN] mic watcher shutdown failed: %s", e)

        # Restore volume if we were ducked when the app quit.
        # Without this, a quit-during-recording leaves volume stuck low.
        # Use fade_ms=0 for instant restore — the app is exiting.
        try:
            self._restore_volume(fade_ms=0)
        except Exception:
            log.debug("[CLEANUP] volume restore failed", exc_info=True)

        # Wait for any running transcription thread to finish (short timeout).
        # ARCH-REFAC-003: read directly from RecordingController (was a
        # @property delegate previously).
        try:
            if hasattr(self, 'recording') and self.recording is not None:
                t = self.recording._transcription_thread
                if t is not None and t.is_alive():
                    log.info("[SHUTDOWN] Waiting for transcription thread to finish...")
                    t.join(timeout=3.0)
                    if t.is_alive():
                        log.warning("[SHUTDOWN] Transcription thread did not finish in time, continuing shutdown")
        except Exception:
            log.debug("[CLEANUP] transcription thread join failed", exc_info=True)

        # ARCH-REFAC-003: access HotkeyDispatcher directly (was a
        # @property delegate previously).
        try:
            _hk_info = (
                f"dictation={self.hotkeys._hotkey_backend.hotkey_str if self.hotkeys._hotkey_backend else 'none'}, "
                f"esc={self.hotkeys._esc_backend.hotkey_str if self.hotkeys._esc_backend else 'none'}, "
                f"repaste={self.hotkeys._repaste_backend.hotkey_str if self.hotkeys._repaste_backend else 'none'}"
            )
            log.info("[HOTKEY] Stopping hotkey listeners (%s)", _hk_info)

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

            log.info("[HOTKEY] All hotkey listeners stopped")
        except Exception:
            log.debug("[CLEANUP] hotkey backend stop failed", exc_info=True)

        # RELIABILITY-005: flush any pending crash-recovery writes
        # before the process exits, so the latest state is persisted.
        # Short timeout — if the disk is genuinely slow we'd rather
        # exit and lose the in-flight snapshot than hang the shutdown.
        try:
            if self._crash_recovery is not None:
                self._crash_recovery.flush(timeout=2.0)
                self._crash_recovery.shutdown()
        except Exception as e:
            log.warning("[SHUTDOWN] crash recovery flush failed: %s", e)

        # CRASH-SAFE-GAP-A: flush pending fire-and-forget history DB writes
        # before the process exits. add_transcription() is fire-and-forget
        # (enqueues the INSERT and returns immediately). If quit() exits
        # without draining the queue, the writer thread (a daemon) is killed
        # by the OS and any unprocessed INSERTs are silently lost. Flushing
        # here ensures the writer drains its queue and commits all pending
        # writes before the process terminates.
        try:
            if self.history_db is not None:
                self.history_db.flush()
        except Exception as e:
            log.warning("[SHUTDOWN] history DB flush failed: %s", e)

        # ARCH-1A-011 (Round 0 forward-port): close the history DB's
        # read connections after flushing.  Each ``HistoryDB`` instance
        # holds open SQLite read connections (one per thread that called
        # ``get_recent`` / ``search``).  In production these are daemons
        # so the OS cleans them up on exit, but explicit close() makes
        # the shutdown deterministic (helpful for test suites that
        # instantiate + tear down many VoiceTyperApp instances in a
        # single process, and for ResourceWarning leak detection).
        try:
            if self.history_db is not None:
                self.history_db.close()
        except Exception as e:
            log.debug("[SHUTDOWN] history DB close failed: %s", e)

        # ARCH-1A-011 (Round 0 forward-port): stop the IPC server's TCP
        # accept loop explicitly.  Without this, the accept loop survives
        # the shutdown window (it blocks on ``server_sock.accept()``) and
        # a reconnecting Electron client can race the cleanup, getting a
        # half-torn-down app object.  ``stop()`` closes the listening
        # socket, which unblocks the accept() call and lets the loop
        # exit cleanly.  Safe to call when no server is running (no-op).
        try:
            if self._ipc_server is not None:
                self._ipc_server.stop()
        except Exception as e:
            log.debug("[SHUTDOWN] IPC server stop failed: %s", e)

        # PERF-NEW-001: stop the bubble level worker so it doesn't
        # try to push to a torn-down IPC server during shutdown.
        try:
            if hasattr(self, "_bubble_level_worker_stop") and self._bubble_level_worker_stop is not None:
                self._bubble_level_worker_stop.set()
                if hasattr(self, "_bubble_level_queue") and self._bubble_level_queue is not None:
                    with contextlib.suppress(queue.Full):
                        self._bubble_level_queue.put_nowait(None)  # sentinel
                if hasattr(self, "_bubble_level_worker") and self._bubble_level_worker is not None:
                    self._bubble_level_worker.join(timeout=1.0)
        except Exception as e:
            log.debug("[SHUTDOWN] bubble level worker stop failed: %s", e)

        # Break the pystray event loop. Wrapped in try-except for
        # idempotency — a second call after the tray is already
        # stopped may raise, and we must not propagate.
        try:
            self.tray.stop()
        except Exception:
            log.debug("[CLEANUP] tray.stop() failed", exc_info=True)

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
        # P1-1.3: prefer the dedicated electron_launcher.terminate_electron
        # helper (which kills the entire process tree on Windows and uses
        # SIGTERM → SIGKILL on POSIX) when we have a tracked PID.  Fall
        # back to the legacy tray_window path for PID discovery so any
        # Electron launched via tray_window.open_electron_window() is also
        # cleaned up.
        try:
            from voice_typer.server import electron_launcher
            launched_pid = getattr(self, "_electron_pid", None)
            if launched_pid:
                log.info("[SHUTDOWN] Terminating Electron subprocess (PID=%s)", launched_pid)
                electron_launcher.terminate_electron(launched_pid)
                self._electron_pid = None
            else:
                from voice_typer.server.tray_window import get_electron_pid
                electron_pid = get_electron_pid()
                if electron_pid is not None:
                    import signal as _sig
                    log.info("[SHUTDOWN] Terminating Electron subprocess (PID=%s)", electron_pid)
                    with contextlib.suppress(OSError, ProcessLookupError):
                        os.kill(electron_pid, _sig.SIGTERM)
        except Exception:
            log.debug("[SHUTDOWN] Electron subprocess termination failed", exc_info=True)

        # P1-1.4: release the single-instance mutex and remove the PID
        # file so a subsequent launch isn't falsely blocked.
        try:
            _clear_backend_pid_file()
        except Exception:
            log.debug("[SHUTDOWN] could not clear backend PID file", exc_info=True)

        log.info("[SHUTDOWN] Shutdown complete, exiting")

        # PLAT-HLEAK: Close the mutex handle on shutdown
        try:
            if hasattr(self, '_mutex_handle') and self._mutex_handle:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
                self._mutex_handle = None
        except Exception:
            pass

        # Close devnull streams opened during logging setup
        try:
            _close_devnull_files()
        except Exception:
            log.debug("[CLEANUP] close devnull files failed", exc_info=True)

    def quit(self):
        """Shut down the application cleanly.

        PROD-003: ensures all threads, PortAudio streams, and
        subprocesses are properly stopped with timeouts. Previously
        thread joins had no timeout and PortAudio streams could be
        left open if quit() raced with the audio callback.

        RW-3: the cleanup body has been extracted into
        ``_do_cleanup()`` so ``restart_app()`` and ``_atexit_cleanup()``
        share the SAME audited shutdown path. This eliminates the
        silent data-loss bug where ``restart_app()`` skipped
        ``history_db.flush()``, ``_crash_recovery.flush()``,
        ``recorder.shutdown_mic_watcher()``, ``recorder.stop()``,
        ``_bubble_level_worker`` stop, ``_clear_backend_pid_file()``,
        and the Win32 mutex handle close — losing pending DB writes
        and leaking PortAudio streams + the mutex on every restart.
        """
        if self._shutting_down:
            log.debug("[SHUTDOWN] quit() already in progress, ignoring duplicate call")
            return

        is_main = threading.current_thread() is threading.main_thread()
        log.info("[SHUTDOWN] Shutting down")
        self._shutting_down = True
        # RACE-020: also set the Event version so executor tasks can check it
        self._shutting_down_event.set()

        # RW-3: delegate to the shared, idempotent cleanup body. The
        # _cleanup_done flag inside _do_cleanup() guarantees that a
        # later _atexit_cleanup() call (or a duplicate quit()) is a
        # no-op rather than double-flushing / double-stopping.
        self._do_cleanup()

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
        flush, history DB flush, recorder stop, PID file + mutex
        release) happens even if the daemon thread's finally block
        didn't run.  It is idempotent — calling it after ``quit()`` or
        ``restart_app()`` is a no-op because both set
        ``_shutting_down = True`` before delegating to ``_do_cleanup()``,
        and ``_do_cleanup()`` itself guards against double-execution
        via the ``_cleanup_done`` flag.

        RW-3: previously this method ran an ad-hoc subset of cleanup
        (volume restore + hotkey stop + crash recovery flush) that
        DIVERGED from ``quit()``'s path.  When the process was killed
        externally (no ``quit()`` / ``restart_app()``), the safety net
        skipped history DB flush, recorder stop, mic watcher shutdown,
        bubble level worker stop, PID file clear, and mutex handle
        close — leaking the same resources that the OLD
        ``restart_app()`` leaked.  It now delegates to
        ``_do_cleanup()`` so the safety net runs the SAME audited
        shutdown path as the regular flow.
        """
        try:
            if self._shutting_down:
                # quit() or restart_app() already ran (or is running)
                # _do_cleanup(); the _cleanup_done flag inside
                # _do_cleanup() makes a second call a no-op, but we
                # short-circuit here too to avoid the spurious
                # "[ATEXIT] Running emergency cleanup" log line on
                # every intentional shutdown.
                return
            log.info("[ATEXIT] Running emergency cleanup")
            self._do_cleanup()
        except Exception:
            # Never raise out of an atexit handler — that would mask
            # the original exit cause and produce confusing tracebacks.
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
        def _signal_handler(signum, frame):
            sig_name = signal.Signals(signum).name
            log.info("[SIGNAL] %s received, shutting down gracefully", sig_name)
            # Run quit on a separate thread to avoid deadlock.
            # RACE-016: daemon=True is acceptable because quit() is
            # idempotent and the atexit handler covers critical cleanup.
            threading.Thread(target=self.quit, daemon=True).start()

        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(OSError, ValueError):
                # SIGTERM not available on Windows; signal.signal can
                # raise if not in the main thread
                signal.signal(sig, _signal_handler)

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


            handler_routine = ctypes.CFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

            self._console_handler = handler_routine(self._win32_console_handler)
            self._kernel32 = ctypes.windll.kernel32
            kernel32 = self._kernel32
            kernel32.SetConsoleCtrlHandler.argtypes = [handler_routine, wintypes.BOOL]
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
        ctrl_c_event = 0
        ctrl_break_event = 1
        ctrl_close_event = 2
        ctrl_logoff_event = 5
        ctrl_shutdown_event = 6

        if ctrl_type == ctrl_close_event:
            log.info(
                "[WIN32] Console window closing -- "
                "keeping process alive (tray app survives)"
            )
            try:
                self._kernel32.FreeConsole()
                # PERF-004: reuse the existing devnull object instead of
                # opening a new one on every ctrl_close_event (would hit
                # Windows' 10,000 handle cap after ~250 RDP logout cycles).
                if getattr(self, "_devnull", None) is None or self._devnull.closed:
                    self._devnull = open(os.devnull, 'w')  # noqa: SIM115
                    _register_devnull_file(self._devnull)
                sys.stdout = self._devnull
                sys.stderr = self._devnull
                log.info("[WIN32] Detached from console (FreeConsole)")
            except Exception:
                log.warning("[WIN32] FreeConsole() failed")
            return True

        if ctrl_type in (ctrl_logoff_event, ctrl_shutdown_event):
            log.info("[WIN32] System event %d received, shutting down", ctrl_type)
            # RACE-016: daemon=True is acceptable because quit() is
            # idempotent and the atexit handler covers critical cleanup.
            threading.Thread(target=self.quit, daemon=True).start()
            return True

        if ctrl_type in (ctrl_c_event, ctrl_break_event):
            log.info("[WIN32] Ctrl+C received, shutting down")
            # RACE-016: daemon=True is acceptable because quit() is
            # idempotent and the atexit handler covers critical cleanup.
            threading.Thread(target=self.quit, daemon=True).start()
            return True

        return False




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
            class EXPLICIT_ACCESS(ctypes.Structure):  # noqa: N801
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
            class SECURITY_ATTRIBUTES(ctypes.Structure):  # noqa: N801
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


def _backend_pid_file() -> Path:
    """Return the path to the backend PID file (``<config_dir>/backend.pid``).

    P1-1.4: written by ``_ensure_single_instance`` after the mutex is
    acquired, removed by ``_clear_backend_pid_file`` during shutdown.
    Used as a belt-and-suspenders check: on Windows the named mutex is
    the authoritative single-instance guard, but if a previous instance
    crashed hard (BSOD, power loss) the OS may not have released the
    mutex yet when the next launch tries to acquire it.  The PID file
    lets us detect a stale lock and proceed.
    """
    return _config_dir() / "backend.pid"


def _write_backend_pid_file() -> None:
    """Write our PID to the backend PID file (best-effort)."""
    try:
        from voice_typer.server.config import _secure_atomic_write

        pid_file = _backend_pid_file()
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        _secure_atomic_write(pid_file, f"{os.getpid()}\n")
    except OSError as exc:
        log.warning("[STARTUP] could not write backend PID file: %s", exc)
    except Exception:
        log.debug("[STARTUP] could not write backend PID file", exc_info=True)


def _clear_backend_pid_file() -> None:
    """Remove the backend PID file (best-effort)."""
    try:
        pid_file = _backend_pid_file()
        if pid_file.exists():
            pid_file.unlink()
    except OSError as exc:
        log.debug("[SHUTDOWN] could not remove backend PID file: %s", exc)
    except Exception:
        log.debug("[SHUTDOWN] could not remove backend PID file", exc_info=True)


def _is_pid_alive(pid: int) -> bool:
    """Return True if a process with the given PID is currently running.

    Cross-platform: uses ``os.kill(pid, 0)`` on POSIX and ``OpenProcess``
    on Windows.  Returns False if the PID is invalid or the process has
    exited.  On Windows, error_access_denied (5) is treated as "alive"
    (the process exists but is owned by another session — better to
    block a duplicate than to proceed when unsure).
    """
    if pid <= 0:
        return False
    if is_windows():
        try:
            import ctypes
            from ctypes import wintypes

            process_query_limited_information = 0x1000
            kernel32 = ctypes.windll.kernel32
            still_active = wintypes.DWORD()
            handle = kernel32.OpenProcess(
                process_query_limited_information, False, pid,
            )
            if not handle:
                # error_access_denied (5) means the process exists but is
                # owned by another user/session — treat as alive.
                return kernel32.GetLastError() == 5
            try:
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(still_active)):
                    return False
                # STILL_ACTIVE == 259 means the process is running.
                return still_active.value == 259
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        return True


def _read_stale_backend_pid() -> int | None:
    """Return the PID from the backend PID file if it's stale, else None.

    A PID is "stale" if the file exists but no process with that PID is
    alive.  Returns None if the file doesn't exist, is unreadable, or
    the PID is still alive.
    """
    try:
        pid_file = _backend_pid_file()
        if not pid_file.exists():
            return None
        content = pid_file.read_text().strip()
        if not content:
            return None
        pid = int(content)
        if _is_pid_alive(pid):
            return None
        return pid
    except (OSError, ValueError):
        return None
    except Exception:
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

    On duplicate launch, Windows returns ``error_already_exists`` from
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

    error_already_exists = 183
    error_access_denied = 5

    # SEC-001: Create a SECURITY_ATTRIBUTES with a restrictive DACL that
    # only allows the current user to access the mutex. This prevents
    # other sessions/users from opening or manipulating our mutex.
    # PLAT-RUN-FIXED: The mutex name is now a fixed string so ALL
    # VoiceTyper processes (regardless of Python executable) share the
    # same mutex. Previously it included sys.executable hash, which let
    # different Python executables (python.exe vs pythonw.exe, dev venv
    # vs production install) run as separate instances.
    mutex_name = "Local\\VoiceTyperSingleInstance"

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

    if last_error == error_already_exists:
        # P1-1.4: belt-and-suspenders check.  Windows guarantees that
        # error_already_exists means another process holds the mutex
        # RIGHT NOW.  But if that process is actually a zombie (BSOD,
        # power loss, kill -9 leaving the mutex in a transitional state),
        # the PID file lets us detect the stale state and proceed.
        stale_pid = _read_stale_backend_pid()
        if stale_pid is not None:
            log.warning(
                "[STARTUP] mutex reports duplicate, but PID file points to dead "
                "process %d — clearing stale PID file and proceeding",
                stale_pid,
            )
            _clear_backend_pid_file()
        else:
            log.warning(
                "[STARTUP] mutex reports duplicate; PID file missing or PID "
                "still alive — retrying anyway in case mutex was abandoned",
            )
        # Use WaitForSingleObject with zero timeout to check if the
        # mutex is genuinely owned by another live process or was
        # abandoned (previous process crashed).  This is the correct
        # Windows API for distinguishing abandoned mutexes from live
        # ones — CloseHandle+CreateMutexW doesn't work because the
        # named kernel object persists in the \BaseNamedObjects        # namespace even after all handles are closed.
        #
        # WaitForSingleObject return values:
        #   wait_abandoned (0x00000080): previous owner died, WE now
        #     own the mutex → proceed.
        #   WAIT_TIMEOUT  (0x00000102): another live process owns it
        #     → genuine duplicate, exit.
        #   wait_object_0 (0x00000000): we acquired it (unexpected
        #     since CreateMutexW returned error_already_exists).
        wait_abandoned = 0x00000080
        wait_object_0 = 0x00000000
        if mutex:
            wait_result = ctypes.windll.kernel32.WaitForSingleObject(mutex, 0)
            if wait_result == wait_abandoned:
                # Previous instance crashed.  The mutex is now OURS.
                log.warning(
                    "[STARTUP] Mutex was abandoned (previous instance crashed) "
                    "— acquired ownership, proceeding"
                )
                _write_backend_pid_file()
                return mutex
            elif wait_result == wait_object_0:
                # Unexpectedly acquired the mutex.  Proceed anyway.
                log.warning(
                    "[STARTUP] Mutex unexpectedly acquired after error_already_exists"
                )
                _write_backend_pid_file()
                return mutex
            # WAIT_TIMEOUT (or any other result) → genuine duplicate.
            # Fall through to sys.exit(1) below.
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
                    0, msg, APP_NAME,
                    0x00000030 | 0x00000000,  # MB_ICONWARNING | MB_OK
                )
            except Exception:
                if sys.stderr is not None:
                    print(msg, file=sys.stderr)
        if mutex:
            ctypes.windll.kernel32.CloseHandle(mutex)
        sys.exit(1)
    elif last_error == error_access_denied:
        # Couldn't even open the mutex; bail safely.
        if not silent and sys.stderr is not None:
            print("Voice Typer: mutex access denied.", file=sys.stderr)
        sys.exit(1)
    # P1-1.4: mutex acquired — write our PID so the next launch can
    # detect a stale lock if we crash hard.
    _write_backend_pid_file()
    return mutex


# DEAD-013: _another_voice_typer_alive() deleted.
# The Win32 named mutex (VoiceTyperSingleInstance) already proves a
# duplicate exists when error_already_exists is returned — the scan
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
