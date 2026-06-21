"""Main application orchestrator."""

import atexit
import logging
import logging.handlers
import os
import re
import signal
import sys
import threading
import time
import queue
import uuid
from pathlib import Path
from typing import Optional

import numpy as np

from voice_typer.server.config import Config, _config_dir, _migrate_from_legacy
from voice_typer.server.recording import Recorder
from voice_typer.server.transcription import TranscriptionEngine
from voice_typer.server.streaming import StreamingConfig, StreamingTranscriptionSession
from voice_typer.server.text_cleanup import clean_transcribed_text, configure_corrections
from voice_typer.server.clipboard import ClipboardManager
from voice_typer.server.settings import SettingsController, SettingsWindow
from voice_typer.server.tray import TrayIcon, AppState
from voice_typer.server.platform import (
    create_launcher_shortcut,
    enable_autostart,
    disable_autostart,
    is_autostart_enabled,
    list_microphones,
)
from voice_typer.server.hotkeys import create_hotkey_backend, HotkeyBackend
from voice_typer.server.history_db import HistoryDB
from voice_typer.server.crash_recovery import CrashRecovery
from voice_typer.server.audio_quality import AudioQualityAnalyzer
from voice_typer.server.waveform import WaveformBubble
from voice_typer.server import task_scheduler
from voice_typer.server.duck_crash_recovery import DuckCrashRecovery
from voice_typer.server.volume_ducker import VolumeDucker
from voice_typer.server.audio_processor import AudioProcessor, AudioProcessorConfig

log = logging.getLogger("voice_typer")

# Module-level list of devnull file objects opened by _setup_logging()
# for pythonw.exe (where sys.stderr/stdout/stdin are None).
# Closed explicitly in VoiceTyperApp.quit() for clean shutdown.
# Item 3: module-level mutable globals replaced with a class instance.
# Previously _devnull_files and _session_id were module-level lists/strings
# that tests shared, causing FD leaks and cross-test contamination.
# Now they're encapsulated in _ProcessState, which is instantiated once
# per process (module-level singleton) but can be reset in tests.
class _ProcessState:
    """Encapsulates process-level mutable state that was previously
    module-level globals.  Item 3: prevents test cross-contamination."""
    def __init__(self):
        self.devnull_files: list = []
        self.session_id: str = ""

    def reset(self):
        """Reset state — called by tests to avoid cross-test contamination."""
        for f in self.devnull_files:
            try:
                f.close()
            except Exception:
                pass
        self.devnull_files.clear()
        self.session_id = ""


_process_state = _ProcessState()

# Session ID for structured logging (P5)


class _SessionFilter(logging.Filter):
    """Inject session_id and component into every log record."""

    def filter(self, record):
        if not hasattr(record, "session_id"):
            record.session_id = _process_state.session_id
        if not hasattr(record, "component"):
            record.component = record.name
        return True


class _ColorFormatter(logging.Formatter):
    """ANSI-colored formatter for stderr. No extra dependencies.

    Design:
      - timestamp dimmed to recede visually
      - INFO level label omitted (redundant on ~every line)
      - WARN/ERR full-line colored with level label
      - Lines with bracketed prefix (e.g. [PARAKEET]) get topic color
      - Lines without brackets infer topic from message content
    """

    _DIM = "38;5;242"  # grey for timestamp
    _LVL_COLOR = {
        logging.WARNING: "38;5;214",
        logging.ERROR: "38;5;196",
        logging.CRITICAL: "38;5;196;1",
    }
    _LVL_SYM = {
        logging.WARNING: "WARN",
        logging.ERROR: "ERR",
        logging.CRITICAL: "FATAL",
    }
    _TOPIC_COLOR = {
        "PARAKEET": "38;5;69",
        "QWEN": "38;5;69",          # same blue -- ASR engine family
        "MODEL": "38;5;69",
        "CUDA-PROBE": "38;5;75",      # lighter blue -- hardware verification
        "HOTKEY": "38;5;141",
        "HOTKEY FIRED": "38;5;141",
        "HOTKEY FALLBACK": "38;5;141",
        "RECORDING": "38;5;79",
        "AUDIO_QUALITY": "38;5;215",
        "DICTATION": "38;5;215",
        "TRANSCRIBE": "38;5;120",
        "VOLUME": "38;5;111",
        "VAD": "38;5;245",
        "CLIPBOARD": "38;5;120",    # paste pipeline -- same green as TRANSCRIBE
        "STARTUP": "38;5;103",
        "STREAMING": "38;5;110",
        "CLOUD": "38;5;110",        # cloud engines -- same light blue as STREAMING
        "FOCUS": "38;5;102",
        "CLEANUP": "38;5;102",
        "TEMPLATE": "38;5;102",
        "WIN32": "38;5;102",
        "SIGNAL": "38;5;102",
        "HISTORY": "38;5;102",
        "HISTORY_DB": "38;5;102",
        "SHUTDOWN": "38;5;95",      # brownish dim -- distinct from infra grey
        "QUIT": "38;5;95",
        "RESTART": "38;5;95",
        "CANCEL": "38;5;215",       # same orange as DICTATION
        "REPASTE": "38;5;120",      # same green as TRANSCRIBE
        "CONFIG": "38;5;102",
        "TRAY": "38;5;102",
        "LLM_POLISH": "38;5;140",
        "ASR_SETUP": "38;5;102",
        "WAVEFORM": "38;5;102",
        "ONBOARDING": "38;5;102",
        "RECOVERY": "38;5;102",
        "ATEXIT": "38;5;102",
        "PIPELINE": "38;5;102",
        "IPC": "38;5;102",
        "TCP": "38;5;244",
    }
    # For unlabeled lines, infer topic from keyword presence.
    # First match wins -- order matters (list narrower keywords first).
    _TOPIC_KEYWORDS: dict[str, list[str]] = {
        "PARAKEET": ["parakeet", "loading model", "model loaded", "loaded successfully"],
        "STARTUP": ["voice typer starting", "startup", "tray icon created",
                    "tray event loop", "entering tray", "found microphone"],
        "HOTKEY": ["hotkey", "register", "unregister", "polling", "getasynckeystate",
                   "platform is win32", "key-down", "vk=0x"],
        "DICTATION": ["dictation", "recording started", "recording stopped",
                      "starting recording", "stopping recording"],
        "RECORDING": ["microphone", "device query", "native rate", "resampl",
                      "buffer telemetry", "audio captured", "silence", "chunk"],
        "TRANSCRIBE": ["transcrib", "transcription thread", "transcription complete",
                       "transcription failed", "clipboard", "paste"],
        "SHUTDOWN": ["shutdown", "stopping", "stopped", "exiting", "tray icon stopped",
                     "unregisterhotkey"],
        "WIN32": ["console control handler"],
        "HISTORY": ["history database"],
    }

    def _infer_topic(self, msg: str) -> str | None:
        lower = msg.lower()
        for topic, keywords in self._TOPIC_KEYWORDS.items():
            for kw in keywords:
                if kw in lower:
                    return topic
        return None

    def format(self, record):
        ts = self.formatTime(record, "%I:%M:%S")
        if ts[0] == "0":
            ts = ts[1:]
        msg = record.getMessage()

        # Extract bracketed topic prefix (e.g. [PARAKEET])
        topic = None
        content = msg
        if msg.startswith("[") and "]" in msg:
            close = msg.index("]")
            topic = msg[1:close]
            content = msg[close + 1:].lstrip()

        if record.levelno >= logging.WARNING:
            c = self._LVL_COLOR.get(record.levelno, "0")
            sym = self._LVL_SYM.get(record.levelno, "????")
            return f"\033[{c}m{ts}  {sym} {msg}\033[0m"

        # INFO -- dim timestamp, no level label, full line colored by topic
        prefix = f"\033[{self._DIM}m{ts}\033[0m"
        tc = None
        if topic:
            tc = self._TOPIC_COLOR.get(topic)
        elif not topic:
            inferred = self._infer_topic(msg)
            if inferred:
                tc = self._TOPIC_COLOR.get(inferred)

        indent = "  "
        if tc:
            body = f"\033[{tc}m{msg}\033[0m"
        else:
            body = msg
        return f"{prefix}{indent}{body}"


def _setup_logging():
    """Configure logging to file (not console, since we run as tray app)."""

    # Under pythonw.exe (e.g. Windows autostart), sys.stderr/stdout/stdin
    # are None.  Redirect them to devnull immediately so any accidental
    # writes don't crash the process.
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _process_state.devnull_files.append(sys.stderr)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _process_state.devnull_files.append(sys.stdout)
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")
        _process_state.devnull_files.append(sys.stdin)

    # One-time migration from legacy platform config dir
    _migrate_from_legacy()

    # Generate session ID for structured logging (P5)
    _process_state.session_id = uuid.uuid4().hex[:8]

    config_dir = _config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    # Point huggingface_hub cache under .voice-typer/ instead of ~/.cache/
    os.environ.setdefault("HF_HOME", str(config_dir / "huggingface"))

    log_file = config_dir / "voice-typer.log"

    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=2,
    )
    fmt = "%(asctime)s [%(levelname)s] [%(session_id)s] %(component)s: %(message)s"
    handler.setFormatter(
        logging.Formatter(fmt, defaults={"session_id": "", "component": ""})
    )

    root = logging.getLogger("voice_typer")
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    root.addFilter(_SessionFilter())

    # Fix stderr encoding error handler so Unicode chars (Cyrillic, emoji,
    # etc.) don't crash logging.  Windows console uses cp1252 by default
    # which can't encode most non-Latin scripts.
    if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(errors="backslashreplace")
        except OSError:
            pass

    # Also log to stderr with ANSI colors when the output reaches a
    # terminal.  In Electron dev mode, electron-vite spawns Electron with
    # piped stdio; Python inherits the pipe so isatty() returns False
    # even though the output goes to a terminal.  We detect this case by
    # checking for ``--port`` in argv (Electron passes --port, standalone
    # ``voice-typer`` does not) and add colors then too.
    do_color = sys.stderr.isatty() or ("--port" in sys.argv)
    if sys.stderr is not None and do_color:
        stream = logging.StreamHandler()
        stream.setLevel(logging.INFO)
        stream.setFormatter(_ColorFormatter())
        root.addHandler(stream)


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

        # Audio processor: real-time noise filtering (high-pass, gate,
        # optional RNNoise) + post-capture spectral gating.  Constructed
        # from the noise_filter_* config fields.  If disabled or if
        # filter libraries are missing, the processor is a passthrough.
        self._audio_processor = AudioProcessor(
            AudioProcessorConfig.from_config(self.config),
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
        # `self.models` and the property delegates below
        # (`self.transcriber`, `self._qwen_engine`, etc.) for back-compat
        # with tests that read those fields directly.
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
        # register/restart logic. The 3 legacy fields (_hotkey_backend,
        # _esc_backend, _repaste_backend) are exposed via @property
        # delegates below for back-compat with tests that read them.
        from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher
        self.hotkeys: HotkeyDispatcher = HotkeyDispatcher(self)
        # #2 (Round 9): _streaming_session and _transcription_thread now
        # live in RecordingController. Exposed via @property delegates
        # below for back-compat with code that reads them directly.
        self._settings_window: Optional[SettingsWindow] = None
        self._microphones: list[dict] = []
        self._busy_event = threading.Event()
        self._busy_event.set()  # SET = not busy
        self._lock = threading.Lock()

        # #2 (Round 9): _model_load_attempted / _model_load_thread /
        # _pending_dictation now live in ModelManager. They're exposed
        # on VoiceTyperApp via @property delegates below for back-compat
        # with code that reads them directly.
        self._shutting_down = False  # True once quit() starts
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
        """Duck system volume at the start of dictation."""
        if not getattr(self.config, "volume_duck_enabled", True):
            return
        try:
            # Sync the smart-duck flag + poll interval from config on
            # every duck() call so a Settings UI toggle takes effect on
            # the next dictation without requiring an app restart.
            self._volume_ducker.set_smart_duck_enabled(
                getattr(self.config, "volume_duck_smart", True)
            )
            self._volume_ducker.set_smart_duck_poll_interval(
                getattr(self.config, "volume_duck_smart_poll_interval_ms", 500)
            )
            if self._volume_ducker.initialize():
                self._volume_ducker.duck(
                    level=getattr(self.config, "volume_duck_level", 0.25),
                    fade_ms=getattr(self.config, "volume_duck_fade_ms", 150),
                    per_session=getattr(self.config, "volume_duck_per_session", False)
                        and self._volume_ducker.supports_per_session,
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
                fade_ms = getattr(self.config, "volume_duck_fade_ms", 150)
            self._volume_ducker.restore(
                fade_ms=fade_ms,
                per_session=getattr(self.config, "volume_duck_per_session", False)
                    and self._volume_ducker.supports_per_session,
            )
        except Exception:
            log.debug("[VOLUME] restore failed", exc_info=True)

    # ─── #2 (Round 9): ASR backend delegates to ModelManager ───────────
    #
    # The following @property delegates expose the engine fields and
    # model-lifecycle state that previously lived directly on
    # VoiceTyperApp. They read/write through to ``self.models`` so
    # existing code paths (and tests) that do ``app.transcriber = X``
    # or ``app._model_load_thread`` keep working without modification.
    #
    # The actual logic lives in voice_typer/server/model_manager.py.

    @property
    def transcriber(self):
        """Whisper engine (legacy field; mirrored from ModelManager)."""
        return self.models.transcriber

    @transcriber.setter
    def transcriber(self, value):
        # Tests sometimes do `app.transcriber = MagicMock()` directly.
        # Mirror the assignment into ModelManager and re-sync the registry.
        self.models.transcriber = value
        self.models._sync_registry_from_fields()

    @property
    def _qwen_engine(self):
        return self.models._qwen_engine

    @_qwen_engine.setter
    def _qwen_engine(self, value):
        self.models._qwen_engine = value
        self.models._sync_registry_from_fields()

    @property
    def _parakeet_engine(self):
        return self.models._parakeet_engine

    @_parakeet_engine.setter
    def _parakeet_engine(self, value):
        self.models._parakeet_engine = value
        self.models._sync_registry_from_fields()

    @property
    def _asr_registry(self):
        return self.models.registry

    @_asr_registry.setter
    def _asr_registry(self, value):
        # Allow tests to swap the registry if needed.
        self.models._registry = value

    @property
    def _model_load_thread(self):
        return self.models._model_load_thread

    @_model_load_thread.setter
    def _model_load_thread(self, value):
        self.models._model_load_thread = value

    @property
    def _model_load_attempted(self):
        return self.models._model_load_attempted

    @_model_load_attempted.setter
    def _model_load_attempted(self, value):
        self.models._model_load_attempted = value

    @property
    def _pending_dictation(self):
        return self.models._pending_dictation

    @_pending_dictation.setter
    def _pending_dictation(self, value):
        self.models._pending_dictation = value

    @property
    def _transcription_thread(self):
        """#2 (Round 9): delegate to RecordingController._transcription_thread."""
        return self.recording._transcription_thread

    @_transcription_thread.setter
    def _transcription_thread(self, value):
        self.recording._transcription_thread = value

    @property
    def _streaming_session(self):
        """#2 (Round 9): delegate to RecordingController._streaming_session."""
        return self.recording._streaming_session

    @_streaming_session.setter
    def _streaming_session(self, value):
        self.recording._streaming_session = value

    @property
    def _hotkey_backend(self):
        """#2 (Round 9): delegate to HotkeyDispatcher._hotkey_backend."""
        return self.hotkeys._hotkey_backend

    @_hotkey_backend.setter
    def _hotkey_backend(self, value):
        self.hotkeys._hotkey_backend = value

    @property
    def _esc_backend(self):
        """#2 (Round 9): delegate to HotkeyDispatcher._esc_backend."""
        return self.hotkeys._esc_backend

    @_esc_backend.setter
    def _esc_backend(self, value):
        self.hotkeys._esc_backend = value

    @property
    def _repaste_backend(self):
        """#2 (Round 9): delegate to HotkeyDispatcher._repaste_backend."""
        return self.hotkeys._repaste_backend

    @_repaste_backend.setter
    def _repaste_backend(self, value):
        self.hotkeys._repaste_backend = value

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
        """Create, track, and start a timer. Replaces fire-and-forget timers."""
        gen = self._timer_generation
        def guarded_func():
            if gen == self._timer_generation:
                func()
        timer = threading.Timer(delay, guarded_func)
        timer.daemon = True
        # ARCH-022: guard the append so a concurrent _cancel_pending_timers
        # iteration doesn't see a half-updated list.
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

        if not hasattr(self, "_bubble_level_worker") or self._bubble_level_worker is None or not self._bubble_level_worker.is_alive():
            self._bubble_level_worker = threading.Thread(
                target=_bubble_level_worker,
                name="bubble-level-pusher",
                daemon=True,
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

        # Register signal handlers on the main thread (safe before run())
        def signal_handler(sig, frame):
            log.info("[SIGNAL] Received signal %d, quitting", sig)
            self.quit()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # On Windows: install a console control handler
        self._install_win32_console_handler()

        # Register atexit handler to log any unexpected process exit
        atexit.register(self._atexit_log)

        # Enter pystray event loop -- MUST be on the main thread
        log.info("[TRAY] Entering tray event loop on main thread")
        self.tray.run()

    def _do_startup(self):
        """Background work: sync autostart, load mics, load model, register hotkey."""
        log.info("[STARTUP] _do_startup begin")

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
                    if self._onboarding_fail_count >= 3 and self.config.show_notifications:
                        self.config.onboarding_completed = True
                        self.config.onboarding_failed = True
                        try:
                            self.config.save()
                        except Exception:
                            log.exception("[STARTUP] Could not save onboarding_failed flag")
                        try:
                            self.tray.notify(
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
            if err is not None and self.config.show_notifications:
                try:
                    self.tray.notify(
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
                    self.tray.notify(
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
        if sys.platform.startswith("linux") and os.environ.get("XDG_SESSION_TYPE") == "wayland":
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
                    self.tray.notify(
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

        # XPLAT-002: macOS accessibility permission check.
        # On macOS, global hotkeys require Accessibility permission.
        # The app can't request it directly, but we can detect it's
        # missing and notify the user.
        if sys.platform == "darwin":
            try:
                import subprocess as _sp
                # AXIsProcessTrusted returns True if the process has
                # Accessibility permission.  We call it via Swift bridge
                # or via the `osascript` shell command as a fallback.
                # The simplest check: try to use pynput's GlobalHotKeys
                # — if it fails with a permission error, we know.
                # For now, just warn the user.
                result = _sp.run(
                    ["osascript", "-e",
                     'tell application "System Events" to keystroke " "'],
                    capture_output=True, text=True, timeout=3,
                )
                if result.returncode != 0:
                    log.warning("[STARTUP] macOS Accessibility permission may be missing")
                    self.tray.notify(
                        "Voice Typer — Accessibility Permission",
                        "Global hotkeys require Accessibility permission. "
                        "Open System Settings → Privacy & Security → Accessibility "
                        "and add Voice Typer (or Terminal).",
                    )
            except Exception:
                log.debug("[STARTUP] macOS accessibility check failed")

        # 1. Sync autostart config with platform
        log.info("[STARTUP] Step 1: sync autostart")
        self._sync_autostart()
        self.tray.set_autostart_enabled(is_autostart_enabled())

        # 1b. Sync the OS-level prewarm scheduled task with config.fast_startup.
        #     Cheap (a single schtasks /Query) and self-healing: if the user
        #     deleted the task or moved machines, it gets re-registered.
        #
        # PERF-NEW-030: prewarm sync + mic enumeration are independent
        # I/O-bound tasks. Run them in parallel on a ThreadPoolExecutor
        # so the total startup time is max(t_prewarm, t_mics) instead
        # of t_prewarm + t_mics.
        import concurrent.futures

        def _startup_parallel_work() -> None:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                prewarm_future = pool.submit(self._sync_prewarm_task)
                mic_future = pool.submit(self._load_microphones)
                # Wait for both so we can log + handle errors.
                for label, fut in [("prewarm", prewarm_future), ("mic", mic_future)]:
                    try:
                        fut.result(timeout=30)
                    except Exception as exc:
                        log.warning("[STARTUP] %s task failed: %s", label, exc)

        # 1b. Create desktop launcher shortcut on first run (if absent)
        # (Run before parallel work so the shortcut exists before mic
        # enumeration — they're independent but shortcut creation is
        # fast and quick to fail.)
        self._ensure_desktop_shortcut()

        log.info("[STARTUP] Step 1c/2: parallel prewarm + mic enumeration")
        _startup_parallel_work()

        # 3. Register hotkey BEFORE model load so F2 works even if model fails
        log.info("[STARTUP] Step 3: register hotkey")
        self._register_hotkey()

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
        """Ensure config.autostart matches the actual platform autostart state."""
        try:
            actual = is_autostart_enabled()
            if self.config.autostart and not actual:
                log.info("[CONFIG] Config says autostart=true but it is disabled -- enabling")
                enable_autostart()
            elif not self.config.autostart and actual:
                log.info("[CONFIG] Config says autostart=false but it is enabled -- disabling")
                disable_autostart()
        except Exception as e:
            log.warning("[CONFIG] Autostart sync failed: %s", e)

    def _sync_prewarm_task(self) -> None:
        """Ensure the OS prewarm scheduled task matches config.fast_startup.

        Like ``_sync_autostart``, this reconciles the user's setting with
        the actual platform state.  On non-Windows platforms it is a no-op.
        """
        if not task_scheduler.is_supported():
            return
        try:
            registered = task_scheduler.is_prewarm_registered()
            if self.config.fast_startup and not registered:
                log.info("[CONFIG] fast_startup enabled -- registering prewarm task")
                task_scheduler.register_prewarm_task()
            elif not self.config.fast_startup and registered:
                log.info("[CONFIG] fast_startup disabled -- removing prewarm task")
                task_scheduler.unregister_prewarm_task()
        except Exception as e:
            log.warning("[CONFIG] Prewarm task sync failed: %s", e)

    def _ensure_desktop_shortcut(self) -> None:
        """Create the Desktop + Start Menu shortcuts on first run.

        Also migrates away the legacy backend-only ``Voice Typer.bat`` that
        pointed at ``pythonw -m voice_typer`` (which started the backend
        with no Electron, so the bubble overlay never worked).  That .bat
        is removed so the user is left with only the correct universal
        launcher shortcut.
        """
        if sys.platform != "win32":
            return
        desktop = Path.home() / "Desktop"
        lnk_path = desktop / "Voice Typer.lnk"
        legacy_bat = desktop / "Voice Typer.bat"

        # 1. Migrate: remove the legacy backend-only .bat so the broken
        #    "no bubble" shortcut stops shadowing the correct one.
        try:
            if legacy_bat.exists() and "-m voice_typer" in legacy_bat.read_text(
                encoding="utf-8", errors="replace"
            ):
                legacy_bat.unlink()
                log.info("[STARTUP] Removed legacy backend-only shortcut: %s", legacy_bat)
        except OSError:
            pass

        # 2. Ensure the universal-launcher shortcut exists (always recreate
        #    so old .lnk files pointing at the legacy -m voice_typer backend
        #    get upgraded to the universal launcher).
        try:
            result = create_launcher_shortcut()
            if result:
                log.info("[STARTUP] Desktop shortcut synced: %s", result)
        except Exception as e:
            log.debug("[STARTUP] Desktop shortcut creation skipped: %s", e)

    def _load_microphones(self) -> None:
        """Enumerate microphones and update the tray menu."""
        try:
            mics = list_microphones()
            self._microphones = mics
            self.tray.set_microphones(mics)
            log.info("[RECORDING] Found %d microphone(s)", len(mics))
        except Exception as e:
            log.warning("[RECORDING] Could not enumerate microphones: %s", e)

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

    def _finalize_audio_quality_report(self, audio: np.ndarray) -> None:
        """Run final audio-quality analysis and surface warnings.

        Called from :meth:`_stop_dictation` after ``recorder.stop()``
        returns the (already filtered + resampled) audio.  Produces an
        :class:`AudioQualityReport` and, if user-facing warnings are
        enabled in config, notifies via the tray.

        This is the "revived" path for the previously-dead
        ``audio_quality.py`` module — see architecture doc §6.4 and
        §13 ("AudioQualityAnalyzer → Revived").
        """
        if not getattr(self.config, "audio_quality_warnings", True):
            return
        try:
            report = self._audio_quality.analyze_full_audio(audio)
            if report.has_issues:
                summary = report.get_summary()
                log.info("[AUDIO_QUALITY] Issues detected: %s", summary)
                try:
                    self.tray.notify("Voice Typer", summary)
                except Exception:
                    log.debug("[AUDIO_QUALITY] tray notify failed", exc_info=True)
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
            self.tray.notify("Voice Typer", f"Could not stop recording.\n{e}")
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

        # Safety watchdog: if transcription hangs for >60s, force-recover.
        watchdog = threading.Timer(
            60.0,
            lambda: self._force_recover_from_stuck_transcription(),
        )
        watchdog.daemon = True
        watchdog.start()

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
                watchdog=watchdog,
            )

        self._transcription_thread = threading.Thread(
            target=transcribe_thread,
            name="Transcription",
            daemon=True,
        )
        self._transcription_thread.start()

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
        """Open raw settings file for troubleshooting."""
        config_file = self.config.config_dir / "config.json"
        if not config_file.exists():
            self.config.save()

        import shutil
        import subprocess
        try:
            if sys.platform == "win32":
                editor = shutil.which("notepad") or "notepad"
                subprocess.Popen([editor, str(config_file)])
            elif sys.platform == "darwin":
                editor = shutil.which("open") or "open"
                subprocess.Popen([editor, str(config_file)])
            else:
                editor = shutil.which("xdg-open") or "xdg-open"
                subprocess.Popen([editor, str(config_file)])
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
        """TrayController protocol: change hotkey."""
        self._restart_hotkey(hotkey)

    def set_hotkey(self, hotkey: str) -> None:
        """TrayController protocol: set hotkey (alias for change_hotkey)."""
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

        Now that ``_wrap`` re-raises ``SystemExit`` (see RELIABILITY-001
        fix in ``tray.py``), we delegate to ``self.quit()`` which does
        the full cleanup (cancel timers, signal streaming cancel,
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

        Launches a fresh VoiceTyper subprocess and exits the current
        instance via the clean ``sys.exit(0)`` path.

        RELIABILITY-001: previously this method ended with
        ``os._exit(0)`` because ``_wrap`` in ``tray.py`` swallowed
        ``SystemExit``, preventing ``sys.exit(0)`` from terminating
        the process.  ``os._exit(0)`` skips Python atexit handlers,
        ``__del__`` methods, and ``finally`` blocks, leaking the same
        set of resources as ``quit_app`` did (Win32 mutex, PortAudio
        handles, ``RegisterHotKey`` registrations).

        RELIABILITY-003: this method now also stops ``_esc_backend``
        and ``_repaste_backend`` (previously only ``_hotkey_backend``
        was stopped, leaving ESC and repaste hotkeys registered on the
        old process while the new process tried to register them
        again — causing "hotkey busy" errors until the OS reaped the
        old registrations).
        """
        log.info("[RESTART] Restarting Voice Typer...")
        import subprocess
        import time

        # Restore volume BEFORE launching the new process to avoid
        # ping-pong (new process ducks before old process restores).
        # Use fade_ms=0 for instant restore on the restart path.
        self._restore_volume(fade_ms=0)

        time.sleep(0.5)

        # 1. Launch the new instance
        env = os.environ.copy()
        env["VOICE_TYPER_RESTART"] = "1"
        # IMPORTANT: the restarter spawns the SAME module the Electron
        # main process spawns.  If we used a different module here
        # we'd end up with two Python processes that don't share the
        # same IPC channel -- the Electron main would be reading the
        # old process's stdout, the new process's bubble pushes
        # would go nowhere, and the bubble window would never appear.
        # Forward the --port argument so the restarted backend listens on
        # the SAME TCP port Electron is connected to.  Without this the new
        # process starts in stdin/stdout mode and Electron (still connected
        # to the old port) never receives push events - including waveform
        # bubble show/hide/level events.
        restart_args = [sys.executable, "-m", "voice_typer.server.ipc_server"]
        try:
            if "--port" in sys.argv:
                _pidx = sys.argv.index("--port")
                if _pidx + 1 < len(sys.argv):
                    restart_args += ["--port", sys.argv[_pidx + 1]]
        except (ValueError, IndexError):
            pass
        subprocess.Popen(
            restart_args,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env,
            start_new_session=True,
        )

        # 2. Stop all three hotkey backends so the new instance can
        #    re-register them without "hotkey busy" errors.
        #    RELIABILITY-003: previously only _hotkey_backend was stopped.
        try:
            self._cancel_pending_timers()
        except Exception:
            pass
        try:
            if self._hotkey_backend:
                self._hotkey_backend.stop()
        except Exception:
            pass
        try:
            if self._esc_backend:
                self._esc_backend.stop()
        except Exception:
            pass
        try:
            if self._repaste_backend:
                self._repaste_backend.stop()
        except Exception:
            pass

        # 3. Break the pystray event loop so the main thread can exit.
        #    _icon.stop() is safe to call from within a pystray callback
        #    on all supported backends (Win32 message loop, GTK, AppKit):
        #    it sets a flag that the loop checks after the current
        #    callback returns.  Combined with the SystemExit re-raise
        #    in _wrap (RELIABILITY-001), this lets the process exit
        #    via sys.exit(0) instead of os._exit(0).
        try:
            self.tray.stop()
        except Exception as e:
            log.warning("[RESTART] tray.stop() failed: %s", e)

        # 4. Exit via the clean path.  sys.exit(0) raises SystemExit,
        #    which _wrap re-raises, which lets pystray unwind (because
        #    tray.stop() was called in step 3).  Python atexit handlers
        #    and __del__ methods run, releasing the Win32 mutex,
        #    PortAudio handles, and any other resources held by C
        #    extensions.
        log.info("[RESTART] Exiting old process via sys.exit(0)")
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
        """Shut down the application cleanly."""
        if self._shutting_down:
            log.info("[SHUTDOWN] quit() already in progress, ignoring duplicate call")
            return

        is_main = threading.current_thread() is threading.main_thread()
        log.info("[SHUTDOWN] Shutting down (quit() called from thread=%s, is_main=%s)",
                 threading.current_thread().name, is_main)
        self._shutting_down = True

        # Cancel all pending timers
        self._cancel_pending_timers()

        # Signal streaming session to cancel without blocking on join.
        # The old code called _cancel_streaming_session() → session.cancel()
        # → thread.join(timeout=10) which blocked quit for up to 10 seconds.
        # Instead, just signal the cancel event; the daemon thread will die
        # when the process exits.
        session = self._get_streaming_session()
        self._set_streaming_session(None)
        if session is not None:
            session._cancel_event.set()

        if self.recorder.recording:
            self.recorder.discard()

        # Restore volume if we were ducked when the app quit.
        # Without this, a quit-during-recording leaves volume stuck low.
        # Use fade_ms=0 for instant restore — the app is exiting.
        self._restore_volume(fade_ms=0)

        # Wait for any running transcription thread to finish (short timeout).
        t = self._transcription_thread
        if t is not None and t.is_alive():
            log.info("[SHUTDOWN] Waiting for transcription thread to finish...")
            t.join(timeout=3.0)
            if t.is_alive():
                log.warning("[SHUTDOWN] Transcription thread did not finish in time, continuing shutdown")

        if self._hotkey_backend:
            self._hotkey_backend.stop()

        # RELIABILITY-003: also stop ESC cancel and repaste hotkey
        # backends so their RegisterHotKey / GlobalHotKeys registrations
        # are released before the next instance tries to claim them.
        if self._esc_backend:
            try:
                self._esc_backend.stop()
            except Exception as e:
                log.warning("[SHUTDOWN] ESC backend stop failed: %s", e)
        if self._repaste_backend:
            try:
                self._repaste_backend.stop()
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
        log.info("[SHUTDOWN] Shutdown complete, exiting")

        # Close devnull streams
        for f in _process_state.devnull_files:
            try:
                f.close()
            except Exception:
                pass
        _process_state.devnull_files.clear()

        if is_main:
            sys.exit(0)

    def _atexit_log(self) -> None:
        """Log when the process exits, even if quit() was not called."""
        if not self._shutting_down:
            log.warning("[ATEXIT] Process exiting without quit() -- "
                        "likely killed externally (console close, task manager, etc.)")

    def _install_win32_console_handler(self):
        """On Windows, install a console control handler to survive console closure.

        ARCH-046: skip when running under ``pythonw.exe`` — there's no
        console attached, so SetConsoleCtrlHandler is a no-op that
        spews "no console" warnings in the log.
        """
        if sys.platform != "win32":
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
                # PERF-004: previously this opened a NEW devnull file
                # object on every CTRL_CLOSE_EVENT and appended it to
                # ``_devnull_files`` without ever closing the previous
                # one.  After ~250 events (e.g. RDP logout cycles),
                # the process would hit Windows' per-process handle
                # cap (10,000) and ``open()`` would start failing.
                #
                # Fix: reuse the existing devnull object if one was
                # already opened for this handler.  We track it on
                # ``self._devnull`` so subsequent events are no-ops.
                if getattr(self, "_devnull", None) is None or self._devnull.closed:
                    self._devnull = open(os.devnull, 'w')
                    _process_state.devnull_files.append(self._devnull)
                sys.stdout = self._devnull
                sys.stderr = self._devnull
                log.info("[WIN32] Detached from console (FreeConsole)")
            except Exception:
                log.warning("[WIN32] FreeConsole() failed")
            return True

        if ctrl_type in (CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT):
            log.info("[WIN32] System event %d received, shutting down", ctrl_type)
            threading.Thread(target=self.quit, daemon=True).start()
            return True

        if ctrl_type in (CTRL_C_EVENT, CTRL_BREAK_EVENT):
            log.info("[WIN32] Ctrl+C received, shutting down")
            threading.Thread(target=self.quit, daemon=True).start()
            return True

        return False


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
    """
    if sys.platform != "win32":
        return None

    # Skip mutex check during restart -- old instance releases mutex on quit
    if os.environ.get("VOICE_TYPER_RESTART"):
        return None

    import ctypes
    from ctypes import wintypes

    ERROR_ALREADY_EXISTS = 183
    ERROR_ACCESS_DENIED = 5

    # Use CreateMutexW with bInitialOwner=True so WE own the handle.
    # The Windows mutex handle is inheritable across CreateProcess /
    # subprocess.Popen, so a child spawned by the parent will see the
    # mutex as already owned.  We can't disable handle inheritance from
    # Python; the inheritance concern is real but handled separately:
    # Electron's main process kills stale backends before spawning, and
    # the restart path sets VOICE_TYPER_RESTART to skip this check.
    mutex = ctypes.windll.kernel32.CreateMutexW(
        None, True, "VoiceTyperSingleInstance"
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
        sys.exit(0)
    elif last_error == ERROR_ACCESS_DENIED:
        # Couldn't even open the mutex; bail safely.
        if not silent and sys.stderr is not None:
            print("Voice Typer: mutex access denied.", file=sys.stderr)
        sys.exit(0)
    return mutex


# DEAD-013: _another_voice_typer_alive() deleted.
# The Win32 named mutex (VoiceTyperSingleInstance) already proves a
# duplicate exists when ERROR_ALREADY_EXISTS is returned — the scan
# had zero decision power (its result only affected a log message).


def main() -> None:
    """Entry point for the ``voice-typer`` console script (pyproject).

    ERR-IPC-001 (fix): the ``def main()`` line was accidentally deleted
    in a prior refactor. pyproject.toml now points to
    ``voice_typer.server.ipc_server:main`` as the canonical entry point;
    this function is kept as a thin re-export for backward compat.
    """
    from voice_typer.server.ipc_server import main as ipc_main
    ipc_main()
