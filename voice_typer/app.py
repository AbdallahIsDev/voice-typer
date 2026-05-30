"""Main application orchestrator."""

import atexit
import logging
import logging.handlers
import os
import signal
import sys
import threading
import time
import uuid
from typing import Optional

from voice_typer.config import Config, _config_dir
from voice_typer.recording import Recorder
from voice_typer.transcription import TranscriptionEngine
from voice_typer.streaming import StreamingConfig, StreamingTranscriptionSession
from voice_typer.text_cleanup import clean_transcribed_text, configure_corrections
from voice_typer.clipboard import ClipboardManager
from voice_typer.settings import SettingsController, SettingsWindow
from voice_typer.tray import TrayIcon, AppState
from voice_typer.platform import (
    enable_autostart,
    disable_autostart,
    is_autostart_enabled,
    list_microphones,
)
from voice_typer.hotkeys import create_hotkey_backend, HotkeyBackend

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

    # Generate session ID for structured logging (P5)
    _session_id = uuid.uuid4().hex[:8]

    config_dir = _config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    log_file = config_dir / "voice-typer.log"

    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=2,
    )
    fmt = "%(asctime)s [%(levelname)s] [%(session_id)s] %(component)s: %(message)s"
    handler.setFormatter(
        logging.Formatter(fmt, defaults={"session_id": ""})
    )

    root = logging.getLogger("voice_typer")
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    root.addFilter(_SessionFilter())

    # Also log to stderr when running interactively (debugging)
    if sys.stderr.isatty():
        stream = logging.StreamHandler()
        stream.setLevel(logging.INFO)
        stream.setFormatter(
            logging.Formatter(fmt, defaults={"session_id": ""})
        )
        root.addHandler(stream)


class VoiceTyperApp:
    """The main application."""

    def __init__(self):
        self.config = Config.load()
        self.recorder = Recorder(self.config)
        self.transcriber = TranscriptionEngine(
            model_size=self.config.model_size,
            device=self.config.device,
            language=self.config.language,
            beam_size=self.config.beam_size,
            best_of=self.config.best_of,
            condition_on_previous_text=self.config.condition_on_previous_text,
        )
        self._qwen_engine = None
        if self.config.asr_backend == "qwen" and self.config.qwen_model_path:
            self._init_qwen_engine()

        self.clipboard = ClipboardManager(
            paste_enabled=self.config.paste_on_stop,
            unsafe_paste_on_unknown_focus=self.config.unsafe_paste_on_unknown_focus,
        )
        self.tray = TrayIcon(
            controller=self,
            config=self.config,
        )

        self._hotkey_backend: Optional[HotkeyBackend] = None
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
        self._non_windows_paste_notified = False

    # ─── Qwen Engine (P0) ────────────────────────────────────────────

    def _init_qwen_engine(self):
        """Conditionally initialise the Qwen ASR engine."""
        try:
            from voice_typer.qwen_engine import QwenEngine

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

    def _get_active_transcriber(self):
        """Return the Qwen engine (if active) or Whisper engine."""
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

    # ─── Startup ───────────────────────────────────────────────────────

    def start(self):
        """Initialize and run the application."""
        log.info(
            "Voice Typer starting -- model=%s, hotkey=%s, mic=%s, sample_rate=%s",
            self.config.model_size, self.config.hotkey,
            self.config.microphone or "default", self.config.sample_rate,
        )

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

        # Enter pystray event loop — MUST be on the main thread
        log.info("Entering tray event loop on main thread")
        self.tray.run()

    def _do_startup(self):
        """Background work: sync autostart, load mics, load model, register hotkey."""
        log.info("[STARTUP] _do_startup begin")

        # Load external text corrections (if available) before any transcription
        try:
            configure_corrections(config_dir=self.config.config_dir)
        except Exception:
            log.debug("[STARTUP] External corrections load failed, using built-in defaults")

        # 1. Sync autostart config with platform
        log.info("[STARTUP] Step 1: sync autostart")
        self._sync_autostart()
        self.tray.set_autostart_enabled(is_autostart_enabled())

        # 2. Enumerate microphones for the tray menu
        log.info("[STARTUP] Step 2: load microphones")
        self._load_microphones()

        # 3. Register hotkey BEFORE model load so F2 works even if model fails
        log.info("[STARTUP] Step 3: register hotkey")
        self._register_hotkey()

        # Warmup handled synchronously in recording.py on first recording start.

        # 4. Load the appropriate model
        if self.config.asr_backend == "qwen" and self._qwen_engine is not None:
            log.info("[STARTUP] Step 4: Qwen backend active, loading Qwen model")
            self._qwen_engine.load()
            if self._qwen_engine.is_loaded:
                self.tray.set_state(AppState.IDLE, "Ready — Qwen ASR")
            else:
                log.warning("[STARTUP] Qwen load failed, falling back to Whisper")
                self._try_load_model(notify_on_failure=False)
        else:
            log.info("[STARTUP] Step 4: load Whisper model")
            self._try_load_model(notify_on_failure=True)

        log.info("[STARTUP] _do_startup complete")

    def _sync_autostart(self) -> None:
        """Ensure config.autostart matches the actual platform autostart state."""
        try:
            actual = is_autostart_enabled()
            if self.config.autostart and not actual:
                log.info("Config says autostart=true but it is disabled -- enabling")
                enable_autostart()
            elif not self.config.autostart and actual:
                log.info("Config says autostart=false but it is enabled -- disabling")
                disable_autostart()
        except Exception as e:
            log.warning("Autostart sync failed: %s", e)

    def _load_microphones(self) -> None:
        """Enumerate microphones and update the tray menu."""
        try:
            mics = list_microphones()
            self._microphones = mics
            self.tray.set_microphones(mics)
            log.info("Found %d microphone(s)", len(mics))
        except Exception as e:
            log.warning("Could not enumerate microphones: %s", e)

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
                AppState.IDLE, f"Ready — {self.transcriber.device_info}"
            )
            log.info("[MODEL] Loaded successfully via %s", self.transcriber.loaded_via)
        except Exception as e:
            log.exception("[MODEL] Load FAILED")
            self.tray.set_state(
                AppState.ERROR, "Model failed to load — press F2 to retry"
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
            log.info(
                "[HOTKEY] Registration OK (alive=%s, backend=%s)",
                self._hotkey_backend.is_alive(),
                type(self._hotkey_backend).__name__,
            )
        except Exception:
            log.exception("[HOTKEY] Registration FAILED")
            self.tray.notify(
                "Voice Typer",
                "Hotkey registration failed. Use the tray menu to toggle dictation.",
            )

    # ─── Dictation ─────────────────────────────────────────────────────

    def toggle_dictation(self):
        """Toggle recording on/off."""
        log.info(
            "[HOTKEY FIRED] toggle_dictation called "
            "(recording=%s, busy=%s, model_loaded=%s, thread=%s)",
            self.recorder.recording, self._busy_event.is_set(),
            self.transcriber.is_loaded, threading.current_thread().name,
        )
        if not self._busy_event.is_set():  # busy
            log.warning("[F2 BLOCKED] Busy transcribing, ignoring toggle")
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

        # Guard: refuse to record if no model is loaded
        qwen_active = (
            self.config.asr_backend == "qwen"
            and self._qwen_engine is not None
            and self._qwen_engine.is_loaded
        )
        whisper_loaded = self.transcriber.is_loaded

        if not qwen_active and not whisper_loaded:
            if self.config.asr_backend == "qwen" and self._qwen_engine is not None:
                log.warning("[DICTATION] Qwen not loaded, lazy-loading Whisper as fallback")
                self.tray.set_state(AppState.LOADING, "Retrying model load (may take 30s)...")
                self._try_load_model(notify_on_failure=True)
                if not self.transcriber.is_loaded:
                    log.error("[DICTATION] Whisper fallback also failed, cannot record")
                    self._schedule_timer(
                        3.0, lambda: self.tray.set_state(
                            AppState.ERROR, "Model failed to load — press F2 to retry"
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
                            AppState.ERROR, "Model failed to load — press F2 to retry"
                        )
                    )
                    return

        log.info("[DICTATION] Starting recording...")
        try:
            # H12: Wire silence detection callbacks
            self.recorder.on_silence_warning = self._on_silence_warning
            self.recorder.on_silence_auto_stop = self._on_silence_auto_stop
            self.recorder.on_max_duration_auto_stop = self._on_max_duration_auto_stop

            self.recorder.start()
            self._start_streaming_session_if_enabled()
            self.tray.set_state(AppState.RECORDING, "Recording...")
            log.info("[DICTATION] Recording started OK")
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

    def _stop_dictation(self):
        """Stop recording and transcribe in background."""
        if not self.recorder.recording:
            log.info("[DICTATION] _stop_dictation: not recording, no-op")
            return

        # Cancel any stale pending timers
        self._cancel_pending_timers()

        log.info("[DICTATION] Stopping recording...")
        self._busy_event.clear()  # busy = True

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
        log.info("[DICTATION] Recording stopped -- %.1fs of audio, busy=True", duration)

        if duration < 0.5:
            log.info("[DICTATION] Audio too short, skipping transcription")
            self._cancel_streaming_session()
            self.tray.set_state(AppState.IDLE, "Too short — ignored")
            self._busy_event.set()  # busy = False
            self._schedule_timer(2.0, lambda: self.tray.set_state(AppState.IDLE))
            return

        log.info("[DICTATION] Starting transcription thread...")
        self.tray.set_state(AppState.TRANSCRIBING, "Transcribing...")

        # Safety watchdog: if transcription hangs for >60s, force-recover.
        watchdog = threading.Timer(
            60.0,
            lambda: self._force_recover_from_stuck_transcription(),
        )
        watchdog.daemon = True
        watchdog.start()

        def transcribe_thread():
            try:
                log.info("[TRANSCRIBE] Starting transcription...")
                session = self._get_streaming_session()
                if session is not None:
                    log.info("[STREAMING] Finalizing streaming transcript")
                    text = session.finalize(audio)
                    self._set_streaming_session(None)
                else:
                    active = self._get_active_transcriber()
                    text = active.transcribe_with_fallback(audio)
                log.info("[TRANSCRIBE] Transcription complete (len=%d)", len(text) if text else 0)

                if not text:
                    log.info("[TRANSCRIBE] No speech detected")
                    if recorded_rms < 0.005:
                        self.tray.set_state(
                            AppState.IDLE,
                            "No speech — check microphone",
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
                    text = clean_transcribed_text(text)
                    if text != raw_text:
                        log.info(
                            "[CLEANUP] Text cleaned: len %d -> %d",
                            len(raw_text),
                            len(text),
                        )
                else:
                    log.info("[CLEANUP] Text cleanup disabled (raw mode)")

                if self.config.log_transcriptions:
                    log.info("Transcription: %s", text[:200])
                else:
                    log.info("Transcription: %d chars", len(text))

                # Copy to clipboard — only attempt paste if copy succeeded.
                if not self.clipboard.copy(text):
                    log.error("Clipboard copy failed -- not attempting paste")
                    self.tray.set_state(AppState.IDLE, "Done — clipboard unavailable")
                    self.tray.notify(
                        "Voice Typer",
                        "Transcription complete, but clipboard was unavailable.\n"
                        "Text was not pasted. Check the log for details.",
                    )
                    self._busy_event.set()  # busy = False
                    self._schedule_timer(
                        3.0,
                        lambda di=self.transcriber.device_info: self.tray.set_state(
                            AppState.IDLE,
                            f"Ready — {di}",
                        ),
                    )
                    return

                # Attempt safe paste (only if paste_on_stop AND a text input is focused)
                pasted = False
                if self.config.paste_on_stop:
                    pasted = self.clipboard.paste()

                if pasted:
                    status = f"Done — {len(text)} chars (pasted)"
                else:
                    status = f"Done — {len(text)} chars (in clipboard)"
                    if sys.platform != "win32" and self.config.paste_on_stop and not self._non_windows_paste_notified:
                        if not self.config.unsafe_paste_on_unknown_focus:
                            self._non_windows_paste_notified = True
                            self.tray.notify(
                                "Voice Typer",
                                "Auto-paste skipped (focus detection unavailable).\n"
                                "Enable 'unsafe_paste_on_unknown_focus' in config to paste anyway.",
                            )

                self.tray.set_state(AppState.IDLE, status)
                self.tray.notify("Voice Typer", f"Transcribed {len(text)} characters")

                # Reset to plain "Ready" after a few seconds
                self._schedule_timer(
                    3.0,
                    lambda di=self.transcriber.device_info: self.tray.set_state(
                        AppState.IDLE,
                        f"Ready — {di}",
                    ),
                )

            except Exception as e:
                log.exception("[TRANSCRIBE] Transcription FAILED")
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
                log.info("[TRANSCRIBE] busy reset to False")

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

        try:
            session = StreamingTranscriptionSession(
                recorder=self.recorder,
                transcriber=self.transcriber,
                config=self._streaming_config(),
                sample_rate=self.config.sample_rate,
            )
            session.start()
            self._set_streaming_session(session)
            log.info("[STREAMING] Hidden streaming session started")
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

        log.warning("FORCE RECOVER: transcription watchdog fired, resetting state")
        self._busy_event.set()  # busy = False
        self.tray.set_state(AppState.IDLE, "Recovered — transcription timed out")
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
            self._stop_dictation()
        except Exception:
            pass

    def _on_max_duration_auto_stop(self):
        """Handle max duration auto-stop from recorder."""
        log.warning("[DICTATION] Max duration auto-stop: stopping recording")
        try:
            self.tray.notify_safety(
                "Voice Typer",
                "Recording stopped: maximum recording duration reached.",
            )
            self._stop_dictation()
        except Exception:
            pass

    # ─── Settings / Microphone ─────────────────────────────────────────

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
            log.info("Autostart set to %s", enabled)
        except Exception as e:
            log.exception("Failed to set autostart")
            self.tray.notify("Voice Typer", f"Could not change autostart setting.\n{e}")

    def _set_notifications(self, enabled: bool):
        """Set notification behavior from the settings window."""
        self.config.show_notifications = enabled
        self.config.save()
        self.tray.set_notifications_enabled(enabled)
        log.info("Notifications set to %s", enabled)

    def _select_microphone(self, mic_name: str | None):
        """Handle microphone selection from tray menu."""
        self.config.microphone = mic_name
        self.config.save()
        label = mic_name if mic_name else "System Default"

        if self.recorder.recording:
            log.info("Microphone changed to %s; applying after active recording", label)
            self.tray.notify("Voice Typer", f"Microphone next recording: {label}")
            return

        self.recorder = Recorder(self.config)  # re-create with new mic
        log.info("Microphone changed to: %s", label)
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
            log.warning("Could not open editor: %s", e)
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
        """Apply a model change for future dictation sessions."""
        self.config.model_size = model_size
        self.config.save()
        if self.recorder.recording or not self._busy_event.is_set():  # busy
            log.info("Model changed to %s; applying after active work", model_size)
            self.tray.notify(
                "Voice Typer",
                f"Model will change to {model_size} after current recording",
            )
            return
        try:
            self.transcriber.unload()
        except Exception:
            log.exception("[MODEL] Failed to unload previous model")
        self.transcriber = TranscriptionEngine(
            model_size=self.config.model_size,
            device=self.config.device,
            language=self.config.language,
            beam_size=self.config.beam_size,
            best_of=self.config.best_of,
            condition_on_previous_text=self.config.condition_on_previous_text,
        )
        self._model_load_attempted = False
        self.tray.set_state(AppState.IDLE, "Model changed — press F2 to load")

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
        """TrayController protocol: quit the app."""
        self.quit()

    def toggle_autostart(self) -> None:
        """TrayController protocol: toggle autostart on/off."""
        self._toggle_autostart()

    def set_notifications(self, enabled: bool) -> None:
        """TrayController protocol: enable/disable notifications."""
        self._set_notifications(enabled)

    # ─── Shutdown ──────────────────────────────────────────────────────

    def quit(self):
        """Shut down the application cleanly."""
        if self._shutting_down:
            log.info("quit() already in progress, ignoring duplicate call")
            return

        is_main = threading.current_thread() is threading.main_thread()
        log.info("Shutting down (quit() called from thread=%s, is_main=%s)",
                 threading.current_thread().name, is_main)
        self._shutting_down = True

        # Cancel all pending timers
        self._cancel_pending_timers()

        self._cancel_streaming_session()

        if self.recorder.recording:
            self.recorder.discard()

        if self._hotkey_backend:
            self._hotkey_backend.stop()

        self.tray.stop()
        log.info("Shutdown complete, exiting")

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


def main():
    """Entry point."""
    _setup_logging()

    try:
        app = VoiceTyperApp()
    except Exception as e:
        log.exception("Fatal error during initialization")
        if sys.stderr is not None:
            print(f"Voice Typer failed to start: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        app.start()  # blocks on main thread (tray event loop)
    except Exception as e:
        log.exception("Fatal error")
        if sys.stderr is not None:
            print(f"Voice Typer crashed: {e}", file=sys.stderr)
        sys.exit(1)
