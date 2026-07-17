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
from voice_typer.server import task_scheduler

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
from voice_typer.server.env_validation import _validate_env_vars
from voice_typer.server.logging_setup import _setup_logging


class VoiceTyperApp:
    """The main application."""

    def __init__(self):
        self.config = Config.load()

        # THREAD-REGISTRY: create the central registry FIRST so all
        # subsystems constructed below (Recorder, CrashRecovery,
        # StreamingTranscriptionSession via RecordingController, and the
        # bubble-level-pusher spawned in _wire_waveform_bubble) can
        # register their threads with it. ``quit()`` calls
        # ``shutdown_all()`` before the existing _do_cleanup() sequence
        # so the registry's signal-and-join runs first; the per-site
        # shutdown methods then run as a safety net (they're idempotent).
        self._thread_registry = ThreadRegistry()

        # Install Python-level excepthook for unhandled Python exceptions
        _crash_handler.install_python_excepthook()

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

        # RW-9 Phase 6: settings side-effects (autostart, notifications,
        # microphone selection) extracted to SettingsController. The app
        # keeps thin delegate methods (``_toggle_autostart``,
        # ``_set_autostart``, ``_set_notifications``, ``_select_microphone``)
        # so tray menu callbacks and tests calling ``app._select_microphone``
        # keep working unchanged. ``_open_config_file`` stays on
        # VoiceTyperApp because source-level structure tests
        # (test_b4_config_editor_lock.py) pin its body via inspect.getsource.
        from voice_typer.server.settings_controller import SettingsController

        self.settings: SettingsController = SettingsController(self)

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
        # ARCH-022: _pending_timers is appended to from the tray thread,
        # the transcription thread, and the timer thread itself; the
        # `for timer in self._pending_timers` iteration in
        # _cancel_pending_timers can race with concurrent appends and
        # raise RuntimeError("list changed size during iteration").
        # Guard the list with a dedicated lock.
        self._pending_timers: list[threading.Timer] = []
        self._pending_timers_lock = threading.Lock()
        self._timer_generation: int = 0
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
                f"System volume was restored after a crash (to {int(state.linear * 100)}%).",
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
        from voice_typer.server import event_bus

        def _push_bubble_show() -> None:
            sent = event_bus.publish({"type": "bubble_show"})
            log.info("[WAVEFORM] bubble.show() fired; push=%s", "OK" if sent else "NO IPC")

        def _push_bubble_hide() -> None:
            event_bus.publish({"type": "bubble_hide"})

        def _push_bubble_level(rms: float, peak: float) -> None:
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
                q.put_nowait(
                    {
                        "type": "bubble_level",
                        "data": {"rms": float(rms), "peak": float(peak)},
                    }
                )

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
                event_bus.publish(item)
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
            # THREAD-REGISTRY: register the bubble-level-pusher so
            # ``shutdown_all()`` can signal and join it during
            # ``quit()``. This closes the "leaked daemon" gap noted at
            # app.py:1377 — the worker is now tracked centrally and
            # joined on shutdown (with a 1.0s timeout matching the
            # existing _do_cleanup() join). The existing
            # _do_cleanup() path still sets the stop event + enqueues
            # the None sentinel as a safety net; both paths are
            # idempotent.
            self._thread_registry.register(
                name="bubble-level-pusher",
                thread=self._bubble_level_worker,
                stop_event=self._bubble_level_worker_stop,
                join_timeout=1.0,
            )

        def _push_bubble_set_state(state: str) -> None:
            event_bus.publish(
                {
                    "type": "bubble_set_state",
                    "data": {"state": state},
                }
            )

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
            # PERF-02 (R8): refresh the recorder's _vad_enabled cache so the
            # next audio chunk sees the new VAD config without re-evaluating
            # 6 getattr calls per access on the RT thread. The recorder has a
            # 5-second TTL safety net, but explicit refresh gives sub-second
            # visibility on config changes.
            recorder_on_config_changed = getattr(self.recorder, "on_config_changed", None)
            if callable(recorder_on_config_changed):
                recorder_on_config_changed()
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
        pasted = self.clipboard.paste(snapshot, pasted_text=text, force=True)
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
                kb.press("\x08")  # Backspace
                kb.release("\x08")
            self._last_transcription = ""
            self.tray.notify(APP_NAME, f"Undid last transcription ({char_count} chars)")
        except ImportError:
            log.warning("[UNDO] pynput not available for undo")
            self.tray.notify(APP_NAME, "Undo not available (pynput missing)")
        except Exception as e:
            log.warning("[UNDO] Failed: %s", e)
            self.tray.notify(APP_NAME, f"Undo failed: {e}")

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

        XPLAT-01: on Windows the file opens in the user's ``.json`` file
        association (e.g. VS Code, Notepad++, Sublime) instead of being
        forced into Notepad. We obtain the editor process handle via
        ``ShellExecuteEx`` so we can still block until it exits and reload
        afterwards — ``os.startfile`` cannot do this (it returns
        immediately with no handle, which is what caused the old
        reload-after-close / lock-coverage regressions).

        SEC-audit-011 / B-4: ``_config_mutation_lock`` is acquired BEFORE
        spawning the editor and held for the entire editor session (until
        the editor process exits), so a concurrent IPC ``set_config``
        cannot atomically replace ``config.json`` via ``_secure_atomic_write``
        while the user is mid-edit (a TOCTOU race). After the editor exits
        we reload the config from disk so the user's saved edits take
        effect.

        On the rare Windows path where no ``.json`` handler is associated,
        we fall back to the SystemRoot-validated Notepad path (never a bare
        PATH-resolved ``notepad``). macOS uses ``open -W`` and Linux uses
        ``xdg-open``; both block on the editor and reload afterwards.
        """
        config_file = self.config.config_dir / "config.json"
        # Save current in-memory config so the editor sees the latest state
        if not self.config.save():
            log.warning("[CONFIG] Failed to save config before opening editor")
        import subprocess

        try:
            if is_windows():
                # XPLAT-01 + SEC-audit-011 / B-4: open with the user's
                # default editor (respects .json associations — VS Code,
                # Notepad++, Sublime) and obtain a process handle so we can
                # block until it exits and reload afterward. ``os.startfile``
                # returns immediately with no handle (the cause of the old
                # reload/lock regression), so we use ShellExecuteEx instead.
                # Hold _config_mutation_lock for the whole editor session so
                # a concurrent IPC set_config cannot atomically clobber
                # config.json mid-edit (TOCTOU, SEC-audit-011).
                with self._config_mutation_lock:
                    handle = _windows_open_with_default_app(str(config_file))
                    if handle is not None:
                        try:
                            _windows_wait_for_process_exit(handle)
                        finally:
                            _windows_close_process_handle(handle)
                    else:
                        # No associated handler for .json: use the
                        # SystemRoot-validated Notepad path (SEC-audit-011),
                        # never a bare PATH-resolved "notepad" (cwd tamperable).
                        notepad = _systemroot_notepad_path()
                        if notepad is not None:
                            subprocess.Popen([str(notepad), str(config_file)]).wait()
                        else:
                            # Last resort: no Notepad at the validated path.
                            # os.startfile is non-blocking, so the reload below
                            # runs immediately; the user can re-trigger a reload
                            # via the UI after editing.
                            os.startfile(str(config_file))  # type: ignore[attr-defined]
                    # Reload config from disk after the editor closes / launches.
                    try:
                        self.config = type(self.config).load()
                    except Exception as exc:
                        log.warning("[CONFIG] Failed to reload config after editor: %s", exc)
            elif is_macos():
                # B-4: ``open -W`` blocks until the editor exits (vanilla
                # ``open`` returns immediately after launching). Hold the
                # lock for the full editor session so a concurrent IPC
                # ``set_config`` call (which goes through
                # ``service.apply_config`` → ``with app._config_mutation_lock``)
                # blocks until the user finishes editing.
                with self._config_mutation_lock:
                    with contextlib.suppress(Exception):
                        subprocess.run(
                            ["open", "-W", str(config_file)],
                            check=False,
                        )
                    try:
                        self.config = type(self.config).load()
                    except Exception as exc:
                        log.warning("[CONFIG] Failed to reload config after editor: %s", exc)
            else:
                # B-4: Linux. ``xdg-open`` may return before the editor
                # closes (depends on the desktop environment — some DEs
                # spawn the editor as a detached process), but we still
                # block on its exit and hold the lock during that window
                # so a concurrent IPC ``set_config`` call can't interleave
                # with the launch. After the spawn returns we reload the
                # config from disk so any saved edits are picked up.
                with self._config_mutation_lock:
                    with contextlib.suppress(Exception):
                        subprocess.run(
                            ["xdg-open", str(config_file)],
                            check=False,
                        )
                    try:
                        self.config = type(self.config).load()
                    except Exception as exc:
                        log.warning("[CONFIG] Failed to reload config after editor: %s", exc)
        except Exception as e:
            log.warning("[CONFIG] Could not open editor: %s", e)
            self.tray.notify(APP_NAME, f"Config file:\n{config_file}")

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
        from voice_typer.server import event_bus

        event_bus.publish({"type": "quit_app"})

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
        if not self.config.save():
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
        # 1. Push relaunch_electron BEFORE marking _shutting_down.
        from voice_typer.server import event_bus

        try:
            event_bus.publish({"type": "relaunch_electron"})
            log.info("[RESTART] relaunch_electron pushed to Electron via TCP")
        except Exception as e:
            log.warning("[RESTART] failed to push relaunch_electron: %s", e)

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
        self._restore_volume(fade_ms=0)
        _relaunch_ack_event = (
            getattr(self._ipc_server, "_relaunch_ack_event", None) if self._ipc_server is not None else None
        )
        if _relaunch_ack_event is not None:
            _relaunch_ack_event.clear()
            log.info("[RESTART] Waiting for relaunch_ack from Electron (timeout 2.0s)")
            _relaunch_ack_event.wait(timeout=2.0)
        else:
            log.info("[RESTART] No IPC server available; pausing 300ms for Electron")
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
            if hasattr(self, "recording") and self.recording is not None:
                self.recording._stop_watchdog_thread()
        except Exception:
            log.debug("[CLEANUP] _stop_watchdog_thread failed", exc_info=True)

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
            if hasattr(self, "recording") and self.recording is not None:
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
        # RELIABILITY-006-FIX-11: also close() the DB so the writer thread
        # is joined and SQLite connections are closed cleanly. flush()
        # already drained the queue, so the writer join in close() should
        # be fast.
        try:
            if self.history_db is not None:
                self.history_db.flush()
                self.history_db.close()
        except Exception as e:
            log.warning("[SHUTDOWN] history DB flush/close failed: %s", e)

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
            log.debug("[CLEANUP] sd.stop() failed", exc_info=True)

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
            if hasattr(self, "_mutex_handle") and self._mutex_handle:
                import ctypes

                ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
                self._mutex_handle = None
        except Exception:
            log.debug("[CLEANUP] CloseHandle failed", exc_info=True)

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

        THREAD-REGISTRY: ``shutdown_all()`` runs BEFORE the existing
        ``_do_cleanup()`` sequence so the registry's centralized
        signal-and-join runs first. This closes the "leaked daemon"
        gap for the bubble-level-pusher (noted at app.py:1377) and
        gives every registered thread a chance to exit gracefully via
        its stop_event. The per-site shutdown methods in
        ``_do_cleanup()`` then run as a safety net — they're all
        idempotent (Event.set is a no-op if already set; join on a
        dead thread returns immediately), so the redundant calls are
        harmless. ``shutdown_all()`` is itself idempotent, so a
        subsequent call from ``_atexit_cleanup()`` is a no-op.
        """
        if self._shutting_down:
            log.debug("[SHUTDOWN] quit() already in progress, ignoring duplicate call")
            return

        is_main = threading.current_thread() is threading.main_thread()
        log.info("[SHUTDOWN] Shutting down")
        self._shutting_down = True
        # RACE-020: also set the Event version so executor tasks can check it
        self._shutting_down_event.set()

        # THREAD-REGISTRY: signal all registered threads to stop and
        # join them with their per-thread timeouts. Runs BEFORE
        # _do_cleanup() so the registry's centralized shutdown is the
        # first pass; the per-site methods in _do_cleanup() then run
        # as a safety net. Best-effort — failures here don't prevent
        # the rest of shutdown from running.
        try:
            self._thread_registry.shutdown_all()
        except Exception:
            log.debug(
                "[SHUTDOWN] thread_registry.shutdown_all() failed",
                exc_info=True,
            )

        # RW-3: delegate to the shared, idempotent cleanup body. The
        # _cleanup_done flag inside _do_cleanup() guarantees that a
        # later _atexit_cleanup() call (or a duplicate quit()) is a
        # no-op rather than double-flushing / double-stopping.
        self._do_cleanup()

        if is_main:
            sys.exit(0)

    def _atexit_log(self) -> None:
        """Log when the process exits, even if quit() was not called."""
        if not self._shutting_down_event.is_set():
            log.warning(
                "[ATEXIT] Process exiting without quit() -- "
                "likely killed externally (console close, task manager, etc.)"
            )

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
            # CR-21: previously this was a bare ``except Exception: pass``
            # which silently swallowed cleanup failures and left no trace
            # in the log — making post-mortem debugging of crash-loop
            # exits effectively impossible. We still never re-raise out
            # of an atexit handler (that would mask the original exit
            # cause and produce confusing tracebacks), but we now log
            # the exception with traceback so operators can see what
            # broke in the emergency cleanup path.
            log.exception("[ATEXIT] _do_cleanup() raised — emergency cleanup incomplete")

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
            log.info("[WIN32] Console window closing -- keeping process alive (tray app survives)")
            try:
                self._kernel32.FreeConsole()
                # PERF-004: reuse the existing devnull object instead of
                # opening a new one on every ctrl_close_event (would hit
                # Windows' 10,000 handle cap after ~250 RDP logout cycles).
                if getattr(self, "_devnull", None) is None or self._devnull.closed:
                    self._devnull = open(os.devnull, "w")  # noqa: SIM115
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
from voice_typer.server.single_instance import (  # noqa: F401
    _backend_pid_file,
    _clear_backend_pid_file,
    _ensure_single_instance,
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

    ipc_main()


# REF-3: extraction — Windows editor-launch helpers moved to
# voice_typer.server.platform_launch. Re-exported here so callers
# (VoiceTyperApp._open_config_file) and tests that monkeypatch
# voice_typer.server.app._windows_open_with_default_app /
# _windows_wait_for_process_exit / _windows_close_process_handle /
# _systemroot_notepad_path keep working unchanged (test_api_doc_accuracy.py,
# test_b4_config_editor_lock.py). The bare PATH-resolved "notepad" pattern
# is intentionally NOT used — _systemroot_notepad_path validates the path
# via %SYSTEMROOT%\\System32\\notepad.exe (SEC-audit-011 / XPLAT-01).
from voice_typer.server.platform_launch import (  # noqa: F401
    _systemroot_notepad_path,
    _windows_close_process_handle,
    _windows_open_with_default_app,
    _windows_wait_for_process_exit,
)
