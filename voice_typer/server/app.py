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
import uuid
from pathlib import Path
from typing import Optional

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

log = logging.getLogger("voice_typer")

# Module-level list of devnull file objects opened by _setup_logging()
# for pythonw.exe (where sys.stderr/stdout/stdin are None).
# Closed explicitly in VoiceTyperApp.quit() for clean shutdown.
_devnull_files: list = []

# Session ID for structured logging (P5)
_session_id: str = ""


class _SessionFilter(logging.Filter):
    """Inject session_id and component into every log record."""

    def filter(self, record):
        if not hasattr(record, "session_id"):
            record.session_id = _session_id
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
        "DICTATION": "38;5;215",
        "TRANSCRIBE": "38;5;120",
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
    global _session_id

    # Under pythonw.exe (e.g. Windows autostart), sys.stderr/stdout/stdin
    # are None.  Redirect them to devnull immediately so any accidental
    # writes don't crash the process.
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _devnull_files.append(sys.stderr)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _devnull_files.append(sys.stdout)
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")
        _devnull_files.append(sys.stdin)

    # One-time migration from legacy platform config dir
    _migrate_from_legacy()

    # Generate session ID for structured logging (P5)
    _session_id = uuid.uuid4().hex[:8]

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

        self.recorder = Recorder(self.config)
        self.transcriber: Optional[TranscriptionEngine] = None
        self._qwen_engine = None
        self._parakeet_engine = None
        if self.config.asr_backend == "qwen" and self.config.qwen_model_path:
            self._init_qwen_engine()

        self.clipboard = ClipboardManager(
            paste_enabled=self.config.paste_on_stop,
        )
        self.tray = TrayIcon(
            controller=self,
            config=self.config,
        )

        self._hotkey_backend: Optional[HotkeyBackend] = None
        self._esc_backend: Optional[HotkeyBackend] = None
        self._repaste_backend: Optional[HotkeyBackend] = None
        self._streaming_session: Optional[StreamingTranscriptionSession] = None
        self._transcription_thread: Optional[threading.Thread] = None
        self._settings_window: Optional[SettingsWindow] = None
        self._microphones: list[dict] = []
        self._busy_event = threading.Event()
        self._busy_event.set()  # SET = not busy
        self._lock = threading.Lock()

        self._model_load_attempted = False  # True after first load() call
        self._shutting_down = False  # True once quit() starts
        self._pending_timers: list[threading.Timer] = []
        self._timer_generation: int = 0
        self._cycle_counter = 0      # monotonic counter for dictation cycles
        self._cycle_id: str = ""     # human-readable cycle id for log correlation
        # Background model-load thread (Step 4 of _do_startup).  Tracked so
        # toggle_dictation() can detect "loading in progress" and auto-start
        # recording once it finishes.
        self._model_load_thread: Optional[threading.Thread] = None
        self._pending_dictation: bool = False  # auto-start once loaded

        # ─── P1/P2 New Feature Components ────────────────────────────
        self.history_db = HistoryDB()
        self._crash_recovery = CrashRecovery()
        self._audio_quality = AudioQualityAnalyzer()
        self._waveform_bubble = WaveformBubble()
        self._wire_waveform_bubble()
        self._last_transcription: str = ""  # For repaste
        self._template_manager = None  # Lazy-init on first use
        self._vocabulary_manager = None  # Lazy-init on first use
        self._llm_polisher = None  # Lazy-init on first use
        self._cloud_engine = None  # Lazy-init if cloud backend selected

    # ─── Qwen Engine (P0) ────────────────────────────────────────────

    def _init_qwen_engine(self):
        """Conditionally initialise the Qwen ASR engine."""
        try:
            from voice_typer.server.qwen_engine import QwenEngine

            self._qwen_engine = QwenEngine(
                model_path=self.config.qwen_model_path,  # pyrefly: ignore[bad-argument-type]
                device=self.config.device,
                language=self.config.language,
            )
            log.info("[QWEN] QwenEngine created (will load on first use)")
        except ImportError:
            log.warning(
                "[QWEN] qwen-asr package not installed, Qwen backend unavailable"
            )
            self._qwen_engine = None
        except Exception as exc:
            log.error("[QWEN] Failed to initialise QwenEngine: %s", exc)
            self._qwen_engine = None

    # ─── Parakeet Engine (P0) ────────────────────────────────────────

    def _init_parakeet_engine(self):
        """Conditionally initialise the Parakeet ASR engine."""
        try:
            from voice_typer.server.parakeet_engine import ParakeetEngine

            self._parakeet_engine = ParakeetEngine(
                device=self.config.device,
                language=self.config.language,
            )
            log.info("[PARAKEET] ParakeetEngine created (will load on first use)")
        except ImportError:
            log.warning(
                "[PARAKEET] transformers package not installed, Parakeet backend unavailable"
            )
            self._parakeet_engine = None
        except Exception as exc:
            log.error("[PARAKEET] Failed to initialise ParakeetEngine: %s", exc)
            self._parakeet_engine = None

    def _get_active_transcriber(self):
        """Return the active transcriber: Parakeet, Qwen, or Whisper."""
        if (
            self.config.asr_backend == "parakeet"
            and self._parakeet_engine is not None
            and self._parakeet_engine.is_loaded
        ):
            return self._parakeet_engine
        if (
            self.config.asr_backend == "qwen"
            and self._qwen_engine is not None
            and self._qwen_engine.is_loaded
        ):
            return self._qwen_engine
        return self.transcriber

    # ─── Timer Tracking (P1) ─────────────────────────────────────────

    def _schedule_timer(self, delay: float, func) -> threading.Timer:
        """Create, track, and start a timer. Replaces fire-and-forget timers."""
        gen = self._timer_generation
        def guarded_func():
            if gen == self._timer_generation:
                func()
        timer = threading.Timer(delay, guarded_func)
        timer.daemon = True
        self._pending_timers.append(timer)
        timer.start()
        return timer

    def _cancel_pending_timers(self):
        """Cancel and clear all pending scheduled timers."""
        for timer in self._pending_timers:
            timer.cancel()
        self._pending_timers.clear()
        self._timer_generation += 1

    # ─── Thread-Safe Streaming Session Access (P2) ───────────────────

    def _get_streaming_session(self):
        """Thread-safe read of _streaming_session."""
        with self._lock:
            return self._streaming_session

    def _set_streaming_session(self, session_or_none):
        """Thread-safe write of _streaming_session."""
        with self._lock:
            self._streaming_session = session_or_none

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
            _push_event_now({
                "type": "bubble_level",
                "data": {"rms": float(rms), "peak": float(peak)},
            })

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

        # UX-006: Onboarding -- check if this is first run
        if not self.config.onboarding_completed:
            try:
                from voice_typer.server.onboarding import OnboardingController
                onboarding = OnboardingController()
                if onboarding.is_first_run():
                    log.info("[STARTUP] First run detected -- applying onboarding defaults")
                    onboarding.apply_settings(self.config)
                    onboarding.mark_complete()
                    self.config.onboarding_completed = True
                    self.config.save()
            except Exception as e:
                log.debug("[STARTUP] Onboarding check failed: %s", e)

        # Load external text corrections (if available) before any transcription
        try:
            configure_corrections(config_dir=self.config.config_dir)
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

        # PLAT-WAYLAND: Warn if running on Wayland
        if sys.platform.startswith("linux") and os.environ.get("XDG_SESSION_TYPE") == "wayland":
            if not self.config.wayland_warned:
                log.warning("[STARTUP] Wayland detected -- global hotkeys may not work")
                self.config.wayland_warned = True
                self.config.save()

        # 1. Sync autostart config with platform
        log.info("[STARTUP] Step 1: sync autostart")
        self._sync_autostart()
        self.tray.set_autostart_enabled(is_autostart_enabled())

        # 1b. Sync the OS-level prewarm scheduled task with config.fast_startup.
        #     Cheap (a single schtasks /Query) and self-healing: if the user
        #     deleted the task or moved machines, it gets re-registered.
        self._sync_prewarm_task()

        # 1b. Create desktop launcher shortcut on first run (if absent)
        self._ensure_desktop_shortcut()

        # 2. Enumerate microphones for the tray menu
        log.info("[STARTUP] Step 2: load microphones")
        self._load_microphones()

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
        log.info("[STARTUP] Step 4: create transcription engine (background)")
        self._model_load_thread = threading.Thread(
            target=self._load_transcription_engine_background,
            name="ModelLoad",
            daemon=True,
        )
        self._model_load_thread.start()

        # After restart: auto-open the Electron window so it appears fresh
        # once the new instance is fully ready.  The VOICE_TYPER_RESTART
        # env var is set by restart_app() before launching the new process.
        if os.environ.get("VOICE_TYPER_RESTART"):
            log.info("[STARTUP] Restart detected -- opening Electron window")
            try:
                self.tray.open_electron_window()
            except Exception as e:
                log.warning("[STARTUP] Failed to open Electron window after restart: %s", e)

        log.info("[STARTUP] initial setup complete -- model loading continues in background")

    def _load_transcription_engine_background(self) -> None:
        """Background worker: create + load the transcription engine.

        Runs in a daemon thread so the heavy torch/transformers import and
        weight download/read (off-disk on cold boot) do not block the app
        reaching an interactive state.  All tray state transitions happen
        here; if a dictation is pending (user pressed F2 during load), it
        is auto-started once loading succeeds.
        """
        try:
            if self.config.asr_backend == "parakeet":
                # Created here (not in __init__) so the heavy
                # torch+transformers import runs in this BG thread, not on
                # the main startup thread or UI.
                if self._parakeet_engine is None:
                    self._init_parakeet_engine()
                if self._parakeet_engine is not None:
                    log.info("[STARTUP] Parakeet backend active, loading Parakeet model")
                    self.transcriber = None
                    # Set state before heavy import so user sees progress
                    self.tray.set_state(
                        AppState.LOADING, "Loading model -- press F2 to queue…"
                    )
                    self._parakeet_engine.load()
                    if self._parakeet_engine.is_loaded:
                        self.tray.set_state(AppState.IDLE, "Ready -- Parakeet ASR")
                    else:
                        log.warning("[STARTUP] Parakeet load failed")
                        if self._shutting_down:
                            return
                        log.warning("[STARTUP] Parakeet load failed, falling back to Whisper")
                        self._fallback_to_whisper(notify_on_failure=False)
                else:
                    log.warning("[STARTUP] Parakeet engine init failed, falling back to Whisper")
                    self._fallback_to_whisper(notify_on_failure=True)
            elif self.config.asr_backend == "qwen" and self._qwen_engine is not None:
                log.info("[STARTUP] Qwen backend active, loading Qwen model")
                self.tray.set_state(
                    AppState.LOADING, "Loading model -- press F2 to queue…"
                )
                self._qwen_engine.load()
                if self._qwen_engine.is_loaded:
                    self.tray.set_state(AppState.IDLE, "Ready -- Qwen ASR")
                else:
                    log.warning("[STARTUP] Qwen load failed")
                    if self._shutting_down:
                        return
                    log.warning("[STARTUP] Qwen load failed, falling back to Whisper")
                    self._fallback_to_whisper(notify_on_failure=False)
            else:
                self.transcriber = TranscriptionEngine(
                    model_size=self.config.model_size,
                    device=self.config.device,
                    language=self.config.language,
                    beam_size=self.config.beam_size,
                    best_of=self.config.best_of,
                    condition_on_previous_text=self.config.condition_on_previous_text,
                )
                log.info("[STARTUP] Whisper backend active, loading Whisper model")
                self._try_load_model(notify_on_failure=True)
        except Exception:
            log.exception("[STARTUP] Background model load crashed")
            self.tray.set_state(
                AppState.ERROR, "Model load failed -- press F2 to retry"
            )
        finally:
            self._model_load_thread = None
            # If the user pressed F2 during load, honour it now.
            if self._pending_dictation and not self._shutting_down:
                log.info("[STARTUP] Pending dictation -- auto-starting now")
                self._pending_dictation = False
                # Schedule off this loader thread to avoid nesting
                self._schedule_timer(0, self._start_dictation)

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
            if legacy_bat.exists() and "-m voice_typer" in legacy_bat.read_text():
                legacy_bat.unlink()
                log.info("[STARTUP] Removed legacy backend-only shortcut: %s", legacy_bat)
        except OSError:
            pass

        # 2. Ensure the universal-launcher shortcut exists.
        if lnk_path.exists():
            return
        try:
            result = create_launcher_shortcut()
            if result:
                log.info("[STARTUP] Desktop shortcut created: %s", result)
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
        """Fallback to Whisper tiny.en after Parakeet/Qwen backend failed."""
        self.config.model_size = "tiny.en"
        if self.transcriber is None:
            self.transcriber = TranscriptionEngine(
                model_size="tiny.en",
                device=self.config.device,
                language=self.config.language,
                beam_size=self.config.beam_size,
                best_of=self.config.best_of,
                condition_on_previous_text=self.config.condition_on_previous_text,
            )
        else:
            self.transcriber.model_size = "tiny.en"
            self.transcriber._configured_model_size = "tiny.en"
        self._try_load_model(notify_on_failure=notify_on_failure)

    def _try_load_model(self, notify_on_failure: bool = False):
        """Attempt to load the transcription model."""
        self._model_load_attempted = True
        try:
            log.info("[MODEL] Loading model (size=%s, device=%s)...",
                     self.config.model_size, self.config.device)

            def on_progress(message: str):
                self.tray.set_state(AppState.LOADING, message)

            self.transcriber.load(progress_callback=on_progress)
            self.tray.set_state(
                AppState.IDLE, f"Ready -- {self.transcriber.device_info}"
            )
            log.info("[MODEL] Loaded successfully via %s", self.transcriber.loaded_via)
        except Exception as e:
            log.exception("[MODEL] Load FAILED")
            self.tray.set_state(
                AppState.ERROR, "Model failed to load -- press F2 to retry"
            )
            if notify_on_failure:
                self.tray.notify(
                    "Voice Typer",
                    f"Could not load the speech model.\n{e}\n\n"
                    "The app will keep running. Press F2 to retry loading.",
                )

    # ─── Hotkey ────────────────────────────────────────────────────────

    def _register_hotkey(self):
        """Register global hotkey using the platform-appropriate backend."""
        hotkey_str = self.config.hotkey
        log.info("[HOTKEY] Registering: %r -> toggle_dictation", hotkey_str)

        try:
            self._hotkey_backend = create_hotkey_backend(hotkey_str)
            log.info("[HOTKEY] Backend created: %s", type(self._hotkey_backend).__name__)
            self._hotkey_backend.start(self.toggle_dictation)
            # P1: Push-to-talk mode -- set release callback
            if self.config.recording_mode == "push_to_talk":
                self._hotkey_backend.set_on_release(self._stop_dictation)
            log.info(
                "[HOTKEY] Registration OK (alive=%s, backend=%s)",
                self._hotkey_backend.is_alive(),
                type(self._hotkey_backend).__name__,
            )
        except Exception:
            log.warning("[HOTKEY] Registration FAILED -- %s", hotkey_str)
            log.debug("Hotkey registration error", exc_info=True)
            self.tray.notify(
                "Voice Typer",
                "Hotkey registration failed. Use the tray menu to toggle dictation.",
            )

        # Feature: ESC to cancel -- register ESC hotkey when enabled
        if self.config.esc_cancel_enabled:
            try:
                self._esc_backend = create_hotkey_backend("<esc>")
                self._esc_backend.start(self._cancel_dictation)
                log.info("[HOTKEY] ESC cancel hotkey registered")
            except Exception:
                log.warning("[HOTKEY] ESC cancel hotkey registration failed")

        # Feature: Repaste hotkey
        if self.config.repaste_hotkey:
            try:
                self._repaste_backend = create_hotkey_backend(self.config.repaste_hotkey)
                self._repaste_backend.start(self.repaste_last)
                log.info("[HOTKEY] Repaste hotkey registered: %s", self.config.repaste_hotkey)
            except Exception:
                log.warning("[HOTKEY] Repaste hotkey registration failed")

    # ─── Dictation ─────────────────────────────────────────────────────

    def toggle_dictation(self):
        """Toggle recording on/off."""
        # Generate cycle correlation ID for this dictation
        self._cycle_counter += 1
        self._cycle_id = f"#{self._cycle_counter}"

        active = self._get_active_transcriber()
        model_loaded = active is not None and active.is_loaded
        log.info(
            "[HOTKEY FIRED] toggle_dictation called "
            "(recording=%s, busy=%s, model_loaded=%s, thread=%s, cycle=%s)",
            self.recorder.recording, self._busy_event.is_set(),
            model_loaded,
            threading.current_thread().name,
            self._cycle_id,
        )
        if not self._busy_event.is_set():  # busy
            log.warning("[F2 BLOCKED] Busy transcribing, ignoring toggle (cycle=%s)", self._cycle_id)
            return

        # Model still loading in the background (post-fast-startup).  Queue
        # the request and let the loader auto-start recording once it
        # finishes.  This makes F2 feel responsive even on a cold boot.
        #
        # Capture the loader reference ONCE: the background loader's finally
        # block sets self._model_load_thread = None, and since toggle_dictation
        # runs on the hotkey thread while the loader runs on its own thread,
        # re-reading the attribute in the is_alive() check is a TOCTOU race
        # that can dereference None.
        loader = self._model_load_thread
        if loader is not None and loader.is_alive():
            log.info(
                "[HOTKEY FIRED] Model still loading -- queuing dictation (cycle=%s)",
                self._cycle_id,
            )
            self._pending_dictation = True
            self.tray.set_state(
                AppState.LOADING,
                "Loading model -- your dictation will start automatically…",
            )
            return

        if active is None:
            self.tray.set_state(AppState.LOADING, "Starting up -- please wait...")
            return

        if self.recorder.recording:
            self._stop_dictation()
        else:
            self._start_dictation()

    def _start_dictation(self):
        """Start a recording session."""
        if self.recorder.recording:
            log.info("[DICTATION] _start_dictation: already recording, no-op")
            return

        # Cancel any stale pending timers from previous sessions
        self._cancel_pending_timers()

        # Lazy-init engines if backend was changed via Electron UI after startup
        if self.config.asr_backend == "parakeet" and self._parakeet_engine is None:
            self._init_parakeet_engine()
        if self.config.asr_backend == "qwen" and self._qwen_engine is None:
            self._init_qwen_engine()

        # Guard: refuse to record if no model is loaded
        qwen_active = (
            self.config.asr_backend == "qwen"
            and self._qwen_engine is not None
            and self._qwen_engine.is_loaded
        )
        parakeet_active = (
            self.config.asr_backend == "parakeet"
            and self._parakeet_engine is not None
            and self._parakeet_engine.is_loaded
        )
        whisper_loaded = self.transcriber is not None and self.transcriber.is_loaded

        if not qwen_active and not parakeet_active and not whisper_loaded:
            if self.config.asr_backend == "qwen" and self._qwen_engine is not None:
                log.warning("[DICTATION] Qwen not loaded, lazy-loading Whisper as fallback")
                self.config.model_size = "tiny.en"
                if self.transcriber is not None:
                    self.transcriber.model_size = "tiny.en"
                    self.transcriber._configured_model_size = "tiny.en"
                self.tray.set_state(AppState.LOADING, "Retrying model load (may take 30s)...")
                self._try_load_model(notify_on_failure=True)
                if not self.transcriber.is_loaded:
                    log.error("[DICTATION] Whisper fallback also failed, cannot record")
                    self._schedule_timer(
                        3.0, lambda: self.tray.set_state(
                            AppState.ERROR, "Model failed to load -- press F2 to retry"
                        )
                    )
                    return
            elif self.config.asr_backend == "parakeet" and self._parakeet_engine is not None:
                log.warning("[DICTATION] Parakeet not loaded, lazy-loading Whisper as fallback")
                self.config.model_size = "tiny.en"
                if self.transcriber is not None:
                    self.transcriber.model_size = "tiny.en"
                    self.transcriber._configured_model_size = "tiny.en"
                self.tray.set_state(AppState.LOADING, "Retrying model load (may take 30s)...")
                self._try_load_model(notify_on_failure=True)
                if not self.transcriber.is_loaded:
                    log.error("[DICTATION] Whisper fallback also failed, cannot record")
                    self._schedule_timer(
                        3.0, lambda: self.tray.set_state(
                            AppState.ERROR, "Model failed to load -- press F2 to retry"
                        )
                    )
                    return
            elif not self.transcriber.is_loaded:
                log.warning("[DICTATION] Model not loaded, attempting reload")
                self.tray.set_state(AppState.LOADING, "Retrying model load (may take 30s)...")
                self._try_load_model(notify_on_failure=True)
                if not self.transcriber.is_loaded:
                    log.error("[DICTATION] Model reload failed, cannot record")
                    self._schedule_timer(
                        3.0, lambda: self.tray.set_state(
                            AppState.ERROR, "Model failed to load -- press F2 to retry"
                        )
                    )
                    return

        log.info("[DICTATION] Starting recording... (cycle=%s)", self._cycle_id)
        try:
            # H12: Wire silence detection callbacks
            self.recorder.on_silence_warning = self._on_silence_warning
            self.recorder.on_silence_auto_stop = self._on_silence_auto_stop
            self.recorder.on_max_duration_auto_stop = self._on_max_duration_auto_stop

            # Waveform bubble: feed RMS levels from the audio callback
            self.recorder.on_rms_level = self._on_recorder_rms

            self.recorder.start()
            self._start_streaming_session_if_enabled()
            self.tray.set_state(AppState.RECORDING, "Recording...")
            # Show the floating bubble once we know the stream is open
            self._waveform_bubble.show()
            log.info("[DICTATION] Recording started OK (cycle=%s)", self._cycle_id)
        except Exception as e:
            log.exception("[DICTATION] Failed to start recording: %s", e)
            self._cancel_streaming_session()
            self.tray.set_state(AppState.ERROR, "Recording failed")
            self.tray.notify(
                "Voice Typer",
                f"Could not start recording.\n{e}\n\n"
                "Check voice-typer.log for traceback.",
            )
            self._schedule_timer(3.0, lambda: self.tray.set_state(AppState.IDLE))

    def _on_recorder_rms(self, rms: float, peak: float) -> None:
        """Forward per-chunk RMS from the audio callback to the bubble."""
        self._waveform_bubble.update_level(rms, peak)

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
        self._waveform_bubble.hide()

        try:
            audio = self.recorder.stop()
        except Exception as e:
            log.exception("[DICTATION] Failed to stop recording")
            self._cancel_streaming_session()
            self.tray.set_state(AppState.ERROR, "Stop failed")
            self.tray.notify("Voice Typer", f"Could not stop recording.\n{e}")
            self._busy_event.set()  # busy = False
            self._schedule_timer(3.0, lambda: self.tray.set_state(AppState.IDLE))
            return

        # Audio has already been resampled to config.sample_rate by Recorder.stop()
        duration = len(audio) / self.config.sample_rate if len(audio) > 0 else 0
        # Capture RMS before starting transcription thread (race-safe)
        recorded_rms = self.recorder.last_rms
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

        # Safety watchdog: if transcription hangs for >60s, force-recover.
        watchdog = threading.Timer(
            60.0,
            lambda: self._force_recover_from_stuck_transcription(),
        )
        watchdog.daemon = True
        watchdog.start()

        _captured_cycle_id = self._cycle_id

        def transcribe_thread():
            _t0 = time.perf_counter()
            try:
                log.info("[TRANSCRIBE] Starting transcription... (cycle=%s)", _captured_cycle_id)
                session = self._get_streaming_session()
                if session is not None:
                    log.info("[STREAMING] Finalizing streaming transcript (cycle=%s)", _captured_cycle_id)
                    text = session.finalize(audio)
                    self._set_streaming_session(None)
                else:
                    active = self._get_active_transcriber()
                    text = active.transcribe_with_fallback(audio)
                _elapsed = time.perf_counter() - _t0
                log.info("[TRANSCRIBE] Transcription complete (len=%d, took=%.1fs, cycle=%s)", len(text) if text else 0, _elapsed, _captured_cycle_id)

                active = self._get_active_transcriber()
                _device_info = active.device_info if active is not None and hasattr(active, "device_info") else "Parakeet ASR"

                if not text:
                    log.info("[TRANSCRIBE] No speech detected (cycle=%s)", _captured_cycle_id)
                    if recorded_rms < 0.005:
                        self.tray.set_state(
                            AppState.IDLE,
                            "No speech -- check microphone",
                        )
                        self.tray.notify(
                            "Voice Typer",
                            "No speech was detected and audio was near-silence.\n"
                            "Your microphone may not be capturing audio.\n"
                            "Check that the correct mic is selected and is active.",
                        )
                    else:
                        self.tray.set_state(AppState.IDLE, "No speech detected")
                    self._busy_event.set()  # busy = False
                    self._schedule_timer(2.0, lambda: self.tray.set_state(AppState.IDLE))
                    return

                raw_text = text
                if self.config.text_cleanup_enabled:
                    text = clean_transcribed_text(text, auto_punctuation=False)  # auto-punct after templates
                    if text != raw_text:
                        log.info(
                            "[CLEANUP] Text cleaned: len %d -> %d",
                            len(raw_text),
                            len(text),
                        )
                else:
                    log.info("[CLEANUP] Text cleanup disabled (raw mode)")

                # P1/P2: Vocabulary correction
                try:
                    if self._vocabulary_manager is None:
                        from voice_typer.server.vocabulary import VocabularyManager
                        self._vocabulary_manager = VocabularyManager()
                    text = self._vocabulary_manager.apply_to_text(text)
                except Exception:
                    log.debug("[PIPELINE] Vocabulary correction failed")

                # P1: Template matching
                try:
                    if self._template_manager is None:
                        from voice_typer.server.templates import TemplateManager
                        self._template_manager = TemplateManager()
                    expanded = self._template_manager.match(text)
                    if expanded is not None:
                        log.info("[TEMPLATE] Matched template, expanded %d -> %d chars", len(text), len(expanded))
                        text = expanded
                except Exception:
                    log.debug("[PIPELINE] Template matching failed")

                # P1: Auto-punctuation (after template matching)
                if self.config.auto_punctuation:
                    from voice_typer.server.text_cleanup import _add_safe_terminal_punctuation
                    text = _add_safe_terminal_punctuation(text)

                # P2: LLM text polishing
                if self.config.llm_polish and self.config.llm_api_key:
                    try:
                        if self._llm_polisher is None:
                            from voice_typer.server.llm_polish import LLMPolisher
                            self._llm_polisher = LLMPolisher(
                                api_key=self.config.llm_api_key,
                                api_url=self.config.llm_api_url or None,
                                model=self.config.llm_model or None,
                                preset=self.config.llm_preset,
                                enabled=True,
                            )
                        text = self._llm_polisher.polish(text)
                    except Exception as exc:
                        log.warning("[LLM_POLISH] Polish failed: %s", exc)

                # P2: Store in history and crash recovery
                try:
                    self.history_db.add_transcription(
                        text,
                        duration=duration,
                        model=self.config.model_size,
                        device=self.config.device,
                    )
                except Exception:
                    log.debug("[PIPELINE] History DB add failed")

                if self.config.crash_recovery_enabled:
                    try:
                        self._crash_recovery.add(text, pasted=False)
                    except Exception:
                        log.debug("[PIPELINE] Crash recovery add failed")

                # Save for repaste
                self._last_transcription = text

                if self.config.log_transcriptions:
                    log.info("[TRANSCRIBE] Transcription: %s", text[:200])
                else:
                    log.info("[TRANSCRIBE] Transcription: %d chars", len(text))

                # Copy to clipboard -- only attempt paste if copy succeeded.
                if not self.clipboard.copy(text):
                    log.error("[CLIPBOARD] Clipboard copy failed -- not attempting paste (cycle=%s)", _captured_cycle_id)
                    self.tray.set_state(AppState.IDLE, "Done -- clipboard unavailable")
                    self.tray.notify(
                        "Voice Typer",
                        "Transcription complete, but clipboard was unavailable.\n"
                        "Text was not pasted. Check the log for details.",
                    )
                    self._busy_event.set()  # busy = False
                    self._schedule_timer(
                        3.0,
                        lambda di=_device_info: self.tray.set_state(
                            AppState.IDLE,
                            f"Ready -- {di}",
                        ),
                    )
                    return

                # Attempt safe paste (only if paste_on_stop AND a text input is focused)
                pasted = False
                if self.config.paste_on_stop:
                    pasted = self.clipboard.paste()

                # P2: Mark as pasted in crash recovery
                if pasted and self.config.crash_recovery_enabled:
                    try:
                        self._crash_recovery.mark_latest_pasted()
                    except Exception:
                        pass

                if pasted:
                    status = f"Done -- {len(text)} chars (pasted)"
                else:
                    status = f"Done -- {len(text)} chars (in clipboard)"

                self.tray.set_state(AppState.IDLE, status)
                self.tray.notify("Voice Typer", f"Transcribed {len(text)} characters")

                # Reset to plain "Ready" after a few seconds
                self._schedule_timer(
                    3.0,
                    lambda di=_device_info: self.tray.set_state(
                        AppState.IDLE,
                        f"Ready -- {di}",
                    ),
                )

            except Exception as e:
                log.exception("[TRANSCRIBE] Transcription FAILED (cycle=%s)", _captured_cycle_id)
                self.tray.set_state(AppState.ERROR, "Transcription failed")
                self.tray.notify("Voice Typer Error", f"Transcription failed.\n{e}")
                self._schedule_timer(3.0, lambda: self.tray.set_state(AppState.IDLE))

            finally:
                watchdog.cancel()
                session = self._get_streaming_session()
                if session is not None and not self.recorder.recording:
                    self._set_streaming_session(None)
                self._busy_event.set()  # busy = False
                self._transcription_thread = None
                # Force garbage collection to release audio arrays and inference buffers
                import gc
                gc.collect()
                log.info("[TRANSCRIBE] busy reset to False (cycle=%s)", _captured_cycle_id)

        self._transcription_thread = threading.Thread(
            target=transcribe_thread,
            name="Transcription",
            daemon=True,
        )
        self._transcription_thread.start()

    def _streaming_enabled(self) -> bool:
        """Return whether hidden streaming should run for the next recording."""
        if os.environ.get("VOICE_TYPER_STREAMING") == "0":
            return False
        # pyrefly: ignore [unnecessary-type-conversion]
        return bool(self.config.streaming_transcription)

    def _streaming_config(self) -> StreamingConfig:
        return StreamingConfig(
            enabled=self._streaming_enabled(),
            chunk_seconds=self.config.streaming_chunk_seconds,
            step_seconds=self.config.streaming_step_seconds,
            left_overlap_seconds=self.config.streaming_left_overlap_seconds,
            right_guard_seconds=self.config.streaming_right_guard_seconds,
            min_first_chunk_seconds=self.config.streaming_min_first_chunk_seconds,
            silence_threshold=self.config.streaming_silence_threshold,
        )

    def _start_streaming_session_if_enabled(self):
        """Start hidden streaming work for the active recording if enabled."""
        self._set_streaming_session(None)
        if not self._streaming_enabled():
            return

        # Streaming requires transcribe_words (word-level timestamps).
        # Only Whisper supports this; skip for Parakeet/Qwen.
        active = self._get_active_transcriber()
        if active is not None:
            log.info(
                "[STREAMING] Checking transcriber: %s has transcribe_words=%s",
                type(active).__name__,
                hasattr(active, "transcribe_words"),
            )
            if not hasattr(active, "transcribe_words"):
                log.info("[STREAMING] Transcriber lacks transcribe_words, skipping streaming (cycle=%s)", self._cycle_id)
                return
        else:
            log.info("[STREAMING] No active transcriber, skipping streaming (cycle=%s)", self._cycle_id)
            return

        try:
            session = StreamingTranscriptionSession(
                recorder=self.recorder,
                transcriber=self._get_active_transcriber(),
                config=self._streaming_config(),
                sample_rate=self.config.sample_rate,
            )
            session.start()
            self._set_streaming_session(session)
            log.info("[STREAMING] Hidden streaming session started (cycle=%s)", self._cycle_id)
        except Exception as e:
            log.exception("[STREAMING] Failed to start streaming session: %s", e)
            self._set_streaming_session(None)

    def _cancel_streaming_session(self):
        """Cancel any active hidden streaming session."""
        session = self._get_streaming_session()
        self._set_streaming_session(None)
        if session is not None:
            try:
                session.cancel()
            except Exception:
                log.exception("[STREAMING] Failed to cancel streaming session")

    def _force_recover_from_stuck_transcription(self):
        """Safety net: recover from stuck transcription state."""
        if self._busy_event.is_set():  # not busy
            return  # Already recovered, nothing to do
        if (
            self._transcription_thread is not None
            and self._transcription_thread.is_alive()
        ):
            log.warning(
                "Transcription watchdog fired, but worker is still alive; "
                "leaving app busy to avoid overlapping model calls"
            )
            self.tray.set_state(AppState.TRANSCRIBING, "Still transcribing...")
            self.tray.notify(
                "Voice Typer",
                "Transcription is still running.\n"
                "Long recordings or CPU fallback can take extra time.",
            )
            return

        log.warning("[RECOVERY] FORCE RECOVER: transcription watchdog fired, resetting state")
        self._busy_event.set()  # busy = False
        self.tray.set_state(AppState.IDLE, "Recovered -- transcription timed out")
        self.tray.notify(
            "Voice Typer",
            "Transcription took too long and was cancelled.\n"
            "Press F2 to try again.",
        )
        self._schedule_timer(5.0, lambda: self.tray.set_state(AppState.IDLE))

    # ─── Silence Detection Callbacks (H12) ────────────────────────────────

    def _on_silence_warning(self):
        """Handle silence warning from recorder."""
        log.warning("[DICTATION] Silence warning: no audio detected for a while")
        try:
            self.tray.notify_safety(
                "Voice Typer",
                "No audio detected. Check your microphone is connected and working.",
            )
        except Exception:
            pass

    def _on_silence_auto_stop(self):
        """Handle silence auto-stop from recorder."""
        log.warning("[DICTATION] Silence auto-stop: stopping recording due to prolonged silence")
        try:
            self.tray.notify_safety(
                "Voice Typer",
                "Recording stopped: no audio detected for an extended period.",
            )
        except Exception:
            pass
        # Must NOT call _stop_dictation() directly here -- this callback runs
        # inside the audio callback while Recorder._lock is held.  Calling
        # recorder.stop() would deadlock on the same lock.  Schedule it on a
        # separate thread instead.
        self._schedule_timer(0, self._stop_dictation)

    def _on_max_duration_auto_stop(self):
        """Handle max duration auto-stop from recorder."""
        log.warning("[DICTATION] Max duration auto-stop: stopping recording")
        try:
            self.tray.notify_safety(
                "Voice Typer",
                "Recording stopped: maximum recording duration reached.",
            )
        except Exception:
            pass
        # Same reason as _on_silence_auto_stop: avoid deadlock on Recorder._lock.
        self._schedule_timer(0, self._stop_dictation)

    # ─── Settings / Microphone ─────────────────────────────────────────

    def repaste_last(self) -> None:
        """Feature: Repaste last transcription (tray menu + hotkey)."""
        if self._last_transcription:
            try:
                self.clipboard.copy(self._last_transcription)
                self.clipboard.paste()
                log.info("[REPASTE] Repasted last transcription (%d chars)", len(self._last_transcription))
                self.tray.notify("Voice Typer", "Last transcription re-pasted")
            except Exception as e:
                log.warning("[REPASTE] Failed: %s", e)
                self.tray.notify("Voice Typer", "Could not re-paste. Check clipboard.")
        else:
            self.tray.notify("Voice Typer", "No previous transcription to re-paste.")

    def _cancel_dictation(self):
        """Feature: ESC to cancel -- cancel current recording/transcription."""
        log.info("[CANCEL] Cancelling current dictation (cycle=%s)", self._cycle_id)
        self._cancel_pending_timers()
        if self.recorder.recording:
            try:
                self.recorder.discard()
                log.info("[CANCEL] Recording discarded (cycle=%s)", self._cycle_id)
            except Exception as e:
                log.warning("[CANCEL] Failed to discard recording: %s (cycle=%s)", e, self._cycle_id)
        self._cancel_streaming_session()
        self.tray.set_state(AppState.IDLE, "Cancelled")
        self._busy_event.set()

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

        self.recorder = Recorder(self.config)  # re-create with new mic
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
        """Re-register the global hotkey after settings change."""
        self.config.hotkey = hotkey
        self.config.save()
        if self._hotkey_backend:
            try:
                self._hotkey_backend.stop()
            except Exception:
                log.exception("[HOTKEY] Failed to stop previous backend")
            self._hotkey_backend = None
        self._register_hotkey()
        self.tray.set_hotkey(self.config.hotkey)

    def _change_model(self, model_size: str):
        """Apply a model change for future dictation sessions.

        Handles Whisper, Parakeet, and Qwen backends.
        Unloads the old engine and loads the new one immediately (unless
        currently recording).
        """
        # Determine backend from model name
        if model_size == "parakeet":
            new_backend = "parakeet"
        elif model_size == "qwen":
            new_backend = "qwen"
        else:
            new_backend = "whisper"

        old_backend = self.config.asr_backend

        self.config.asr_backend = new_backend
        self.config.model_size = model_size
        self.config.save()

        if self.recorder.recording or not self._busy_event.is_set():
            log.info("[CONFIG] Model changed to %s (%s); applying after active work", model_size, new_backend)
            self.tray.notify(
                "Voice Typer",
                f"Model will change to {model_size} after current recording",
            )
            return

        # Unload old backend
        if old_backend == "parakeet" and self._parakeet_engine is not None:
            try:
                self._parakeet_engine = None
            except Exception:
                pass
        if old_backend == "qwen" and self._qwen_engine is not None:
            try:
                self._qwen_engine = None
            except Exception:
                pass
        if self.transcriber is not None:
            try:
                self.transcriber.unload()
            except Exception:
                pass
            self.transcriber = None

        # Load new backend
        self._model_load_attempted = False

        if new_backend == "parakeet":
            self._init_parakeet_engine()
            if self._parakeet_engine is not None:
                def on_progress(msg: str):
                    self.tray.set_state(AppState.LOADING, msg)
                try:
                    self._parakeet_engine.load(progress_callback=on_progress)
                except Exception as e:
                    log.exception("[MODEL] Parakeet load raised: %s", e)
                    self.tray.set_state(AppState.ERROR, f"Parakeet load failed: {e}")
                    return
                if self._parakeet_engine.is_loaded:
                    self.tray.set_state(AppState.IDLE, f"Ready -- Parakeet ASR")
                    self.tray.invalidate_menu_cache()
                else:
                    log.warning("[MODEL] Parakeet model failed to load")
                    self.tray.set_state(AppState.ERROR, "Parakeet model failed to load")
            return

        if new_backend == "qwen":
            self._init_qwen_engine()
            if self._qwen_engine is not None:
                def on_progress(msg: str):
                    self.tray.set_state(AppState.LOADING, msg)
                try:
                    self._qwen_engine.load(progress_callback=on_progress)
                except Exception as e:
                    log.exception("[MODEL] Qwen load raised: %s", e)
                    self.tray.set_state(AppState.ERROR, f"Qwen load failed: {e}")
                    return
                if self._qwen_engine.is_loaded:
                    self.tray.set_state(AppState.IDLE, f"Ready -- Qwen ASR")
                    self.tray.invalidate_menu_cache()
                else:
                    log.warning("[MODEL] Qwen model failed to load")
                    self.tray.set_state(AppState.ERROR, "Qwen model failed to load")
            return

        # Whisper
        self.transcriber = TranscriptionEngine(
            model_size=self.config.model_size,
            device=self.config.device,
            language=self.config.language,
            beam_size=self.config.beam_size,
            best_of=self.config.best_of,
            condition_on_previous_text=self.config.condition_on_previous_text,
        )
        try:
            def on_progress(msg: str):
                self.tray.set_state(AppState.LOADING, msg)
            self.transcriber.load(progress_callback=on_progress)
            self.tray.set_state(
                AppState.IDLE, f"Ready -- {self.transcriber.device_info}"
            )
            self.tray.invalidate_menu_cache()
        except Exception as exc:
            log.exception("[MODEL] Whisper model load failed: %s", exc)
            self.tray.set_state(AppState.ERROR, f"Model failed: {exc}")

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

        Uses inline cleanup + ``os._exit(0)`` instead of ``self.quit()`` →
        ``sys.exit(0)`` because the tray menu callback wrapper
        (``_wrap`` in ``tray.py``) catches ``SystemExit`` and silently
        swallows it -- meaning the process would never actually terminate,
        leaving the terminal stuck with a zombie process.
        """
        log.info("[QUIT] Quitting Voice Typer...")

        # 1. Quick cleanup before force-exit.
        #    NOTE: self.tray.stop() / self._icon.stop() is NOT called because
        #    it can hang when called from within a pystray callback (the tray
        #    menu's _wrap handler catches SystemExit, and _icon.stop() may not
        #    return reliably).  os._exit(0) terminates the process immediately,
        #    and the OS cleans up the tray icon.
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

        log.info("[QUIT] Force-exiting")
        os._exit(0)

    def restart_app(self) -> None:
        """TrayController protocol: restart the app.

        Launches a fresh VoiceTyper subprocess and force-exits the current instance.

        NOTE: Uses ``os._exit(0)`` instead of ``self.quit()`` → ``sys.exit(0)``
        because the tray menu callback wrapper (``_wrap`` in ``tray.py``)
        catches ``SystemExit`` and silently swallows it -- meaning the process
        would never actually terminate.
        """
        log.info("[RESTART] Restarting Voice Typer...")
        import subprocess
        import time

        time.sleep(0.5)

        # 2. Launch the new instance
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

        # 3. Minimal cleanup before force-exit.
        #    self.quit() is NOT called because its sys.exit(0) gets swallowed
        #    by tray.py's _wrap SystemExit handler, leaving the process alive
        #    as a zombie with no tray icon.
        #    NOTE: self.tray.stop() / self._icon.stop() is also NOT called
        #    because it can hang when called from within a pystray callback,
        #    preventing os._exit(0) from ever running.
        try:
            self._cancel_pending_timers()
        except Exception:
            pass
        try:
            if self._hotkey_backend:
                self._hotkey_backend.stop()
        except Exception:
            pass

        log.info("[RESTART] Force-exiting old process")
        os._exit(0)

    def toggle_autostart(self) -> None:
        """TrayController protocol: toggle autostart on/off."""
        self._toggle_autostart()

    def create_desktop_shortcut(self) -> None:
        """Create (or overwrite) the desktop launcher .bat shortcut."""
        result = create_launcher_shortcut()
        if result:
            self.tray.notify("Voice Typer", f"Desktop shortcut created:\n{result}")
        else:
            self.tray.notify(
                "Voice Typer",
                "Could not create desktop shortcut.\nCheck the log for details.",
            )

    def set_notifications(self, enabled: bool) -> None:
        """TrayController protocol: enable/disable notifications."""
        self._set_notifications(enabled)

    def set_silence_warning_seconds(self, seconds: float) -> None:
        """TrayController protocol: set silence warning timeout."""
        self.config.silence_warning_seconds = seconds
        self.config.save()
        self.tray.invalidate_menu_cache()

    def set_silence_auto_stop_seconds(self, seconds: float) -> None:
        """TrayController protocol: set silence auto-stop timeout."""
        self.config.silence_auto_stop_seconds = seconds
        self.config.save()
        self.tray.invalidate_menu_cache()

    def set_max_recording_seconds(self, seconds: int) -> None:
        """TrayController protocol: set max recording duration override."""
        self.config.max_recording_seconds = seconds
        self.config.save()
        self.tray.invalidate_menu_cache()

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

        # Wait for any running transcription thread to finish (short timeout).
        t = self._transcription_thread
        if t is not None and t.is_alive():
            log.info("[SHUTDOWN] Waiting for transcription thread to finish...")
            t.join(timeout=3.0)
            if t.is_alive():
                log.warning("[SHUTDOWN] Transcription thread did not finish in time, continuing shutdown")

        if self._hotkey_backend:
            self._hotkey_backend.stop()

        self.tray.stop()
        log.info("[SHUTDOWN] Shutdown complete, exiting")

        # Close devnull streams
        for f in _devnull_files:
            try:
                f.close()
            except Exception:
                pass
        _devnull_files.clear()

        if is_main:
            sys.exit(0)

    def _atexit_log(self) -> None:
        """Log when the process exits, even if quit() was not called."""
        if not self._shutting_down:
            log.warning("[ATEXIT] Process exiting without quit() -- "
                        "likely killed externally (console close, task manager, etc.)")

    def _install_win32_console_handler(self):
        """On Windows, install a console control handler to survive console closure."""
        if sys.platform != "win32":
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
                self._devnull = open(os.devnull, 'w')
                _devnull_files.append(self._devnull)
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
        # RIGHT NOW.  Trust it — do not second-guess with a process scan.
        # Best-effort log of who owns it (informational only).
        try:
            if _another_voice_typer_alive():
                log.info("[STARTUP] Duplicate launch blocked: another Voice Typer is running")
            else:
                log.info("[STARTUP] Duplicate launch blocked (mutex already held)")
        except Exception:
            pass
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


def _another_voice_typer_alive() -> bool:
    """Best-effort check whether another voice_typer process is alive.

    Returns True if a python.exe OR pythonw.exe process whose command
    line mentions voice_typer is currently running (other than us).
    Used by ``_ensure_single_instance`` to decide whether to actually
    exit.

    Checks both executables because the autostart launcher runs as
    ``pythonw.exe`` -- scanning only ``python.exe`` would miss it and let
    two instances race for the mutex.
    """
    import subprocess as _sp
    # WMIC's WHERE clause doesn't support OR cleanly with the Name=
    # predicate on all builds, so we query each image name separately
    # and merge the output.
    chunks: list[str] = []
    for image in ("python.exe", "pythonw.exe"):
        try:
            out = _sp.check_output(
                ['wmic', 'process', 'where', f'Name="{image}"',
                 'get', 'ProcessId,CommandLine', '/format:list'],
                stderr=_sp.DEVNULL, text=True, timeout=3,
            )
            chunks.append(out)
        except Exception:
            # wmic may be deprecated/absent on newer Windows builds;
            # if either query works we can still decide.
            continue
    if not chunks:
        return False

    import os as _os
    for out in chunks:
        for chunk in out.split("\n\n"):
            m_pid = re.search(r"ProcessId=(\d+)", chunk)
            m_cmd = re.search(r"CommandLine=(.*)", chunk)
            if not (m_pid and m_cmd):
                continue
            try:
                pid = int(m_pid.group(1))
            except ValueError:
                continue
            if pid == _os.getpid():
                continue
            if pid == _os.getppid():
                continue
            cmd = m_cmd.group(1).strip()
            if re.search(r"voice[_-]?typer", cmd, re.IGNORECASE):
                return True
    return False


def main():
    """Entry point for the ``voice-typer`` console script (pyproject).

    Delegates to ``voice_typer.server.ipc_server.main`` so there is exactly
    ONE backend entry point in the project.  Both ``voice-typer`` and the
    Electron-spawned ``python -m voice_typer.server.ipc_server`` run the
    identical code path; the only difference is whether ``--port`` is passed
    (Electron passes it for TCP mode; the console script runs without it).

    Previously this function duplicated ~20 lines of IPC/setup logic that
    diverged from ``ipc_server.main`` (notably: it never called
    ``start_tcp()``, so push events had no sink).  The duplication caused
    the two entry points to behave differently for no good reason.
    """
    from voice_typer.server.ipc_server import main as ipc_main
    ipc_main()
