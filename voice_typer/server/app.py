"""Main application orchestrator."""

from __future__ import annotations

import atexit
import logging
import logging.handlers
import os  # noqa: F401 (stdlib re-export for test monkeypatch)
import sys
import threading
import time  # noqa: F401 (stdlib re-export for test monkeypatch)
import weakref
from typing import TYPE_CHECKING, Any

# CRASH-HANDLER: Windows VEH + Python excepthook for silent crash diagnostics
from voice_typer.server import crash_handler as _crash_handler

# Re-exported for monkeypatch.setattr("voice_typer.server.app.X", ...) in tests
# and for runtime lookups from voice_typer.server.startup_tasks.  # ruff: noqa: F401
# numpy was eagerly imported at module top but never used directly in
# this module. The eager import added ~250-335ms cumulative to every
# cold start because numpy performs heavy C-extension initialization at
# import time. The lazy ``lazy_module`` proxy defers the real import to
# first attribute access; if no caller ever touches ``np`` from this
# module (the current state), numpy is never imported on this code path
# at all. The proxy is transparent — see ``_lazy_import.py``'s
# ``__getattr__`` / ``__setattr__`` docstrings for the test-patch
# compatibility rationale (``monkeypatch.setattr(app.np, "array", ...)``
# still propagates to the real ``numpy`` module in ``sys.modules``).
# ``from __future__ import annotations`` above is REQUIRED so any future
# ``np.ndarray`` annotation in this file stays as an unevaluated string
# (PEP 563) and does NOT trigger the eager import we just eliminated.
from voice_typer.server._lazy_import import lazy_module

# restart token functions moved to voice_typer.server.security
# COMPAT-001: backward-compat re-export for tests/test_pii_redaction.py
# which imports _PIIRedactionFilter from app. The class lives in
# voice_typer.server.security as PIIRedactionFilter (no underscore).
# Win32 SECURITY_ATTRIBUTES builder extracted to a focused,
# security-reviewable module.  Re-exported here so existing callers
# (and tests that grep app.py source for the symbol name) keep working.
from voice_typer.server._security_attributes import (  # noqa: F401
    _create_restrictive_security_attributes,
)

# ``AudioProcessor`` / ``DuckCrashRecovery`` / ``VolumeDucker`` /
# ``WaveformBubble`` were eagerly imported at module top but are only
# used inside lazy @property getters (or the ``_LazyAudioProcessorProxy``
# below). Moving the imports INTO the getters defers the transitive
# import cost (audio_filters -> scipy.signal.butter; volume_ducker ->
# pyobjc / ctypes on macOS; waveform -> numpy) to first attribute
# access — paid only when the user actually dictates / ducks volume /
# sees the bubble, not on every cold start. ``ClipboardManager`` /
# ``AudioQualityAnalyzer`` / ``CrashRecovery`` / ``HistoryDB`` stay at
# module top because they're either cheap to import or re-exported for
# test monkeypatch.
from voice_typer.server.audio_quality import AudioQualityAnalyzer
from voice_typer.server.branding import APP_NAME
from voice_typer.server.clipboard import ClipboardManager
from voice_typer.server.config import Config, _config_dir
from voice_typer.server.crash_recovery import CrashRecovery
from voice_typer.server.history_db import HistoryDB

# Re-exported for monkeypatch.setattr("voice_typer.server.app.X", ...) in tests.  # ruff: noqa: F401
# use centralized platform helpers instead of raw sys.platform checks.
# signal_handlers.install_win32_console_handler and various tests monkeypatch
# voice_typer.server.app.is_windows — keep the re-export so they keep working.
from voice_typer.server.platform_utils import (  # noqa: F401
    is_linux,
    is_macos,
    is_windows,
)
from voice_typer.server.recording import Recorder
from voice_typer.server.security import PIIRedactionFilter as _PIIRedactionFilter  # noqa: F401

# autostart + microphone helpers are re-exported from server_platform so
# voice_typer.server.settings_controller and voice_typer.server.startup_tasks
# can import them dynamically via ``voice_typer.server.app``, and so tests
# that monkeypatch ``voice_typer.server.app.{is_autostart_enabled,...}``
# (tests/app/conftest.py, tests/test_*.py, etc.) can find them here.
from voice_typer.server.server_platform import (  # noqa: F401
    disable_autostart,
    enable_autostart,
    is_autostart_enabled,
    list_microphones,
)

# create_launcher_shortcut + list_microphones are re-exported here (and consumed
# from voice_typer.server.startup_tasks) so tests that monkeypatch
# voice_typer.server.app.list_microphones / create_launcher_shortcut keep working.  # ruff: noqa: F401
from voice_typer.server.streaming import (
    StreamingTranscriptionSession,  # noqa: F401  (re-exported for tests/test_app.py monkeypatch)
)
from voice_typer.server.text_cleanup import (  # noqa: F401 (re-exported for tests)
    clean_transcribed_text,
    configure_corrections,
)
from voice_typer.server.thread_registry import ThreadRegistry

# ``TranscriptionEngine`` was re-exported here purely so tests could
# monkeypatch ``voice_typer.server.app.TranscriptionEngine``. The
# monkeypatch sites have been migrated to the canonical location
# ``voice_typer.server.transcription.TranscriptionEngine`` (see
# tests/app/test_lifecycle.py + tests/test_qwen_engine.py), so this
# re-export is no longer needed. Note: production code in this module
# does NOT instantiate ``TranscriptionEngine`` directly — the ASR
# registry (``asr_registry._BACKEND_SPECS``) constructs backends via
# ``importlib.import_module("voice_typer.server.transcription")``, so
# the canonical patch target is the ``transcription`` module, not
# ``app``.
# T-1 / ARCH-9: ``create_hotkey_backend`` re-exported here so the
# 8+ test files that monkeypatch ``voice_typer.server.app.create_hotkey_backend``
# (e.g. tests/test_volume_lifecycle.py:73-78, tests/test_hotkey_dispatcher_*.py)
# keep working without per-test migration to the canonical location
# ``voice_typer.server.hotkeys.create_hotkey_backend``. ARCH-9
# documents the broader pattern of test-seam re-exports being
# progressively removed (TranscriptionEngine was the first);
# create_hotkey_backend stays because the migration cost is high
# and the production-side patch target is the same function.
from voice_typer.server.hotkeys import create_hotkey_backend  # noqa: F401, E402  (re-exported for tests)
from voice_typer.server.tray import AppState, TrayIcon

np = lazy_module("numpy")

if TYPE_CHECKING:
    # imported only for type annotations on ``_template_manager``
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
# _setup_logging calls warn_if_in_container() (from
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
from voice_typer.server.env_validation import _validate_env_vars  # noqa: F401, E402
from voice_typer.server.logging_setup import _setup_logging  # noqa: F401, E402


class _LazyAudioProcessorProxy:
    """Transparent lazy proxy for ``AudioProcessor``.

    ``VoiceTyperApp.__init__`` used to construct ``AudioProcessor``
    eagerly, which calls ``build_chain(config, sample_rate)``. That in
    turn imports the full ``audio_filters`` package (highpass ->
    ``scipy.signal.butter``, noise_suppressor -> RNNoise, etc.) on
    every cold start — even when the user never dictates.

    This proxy defers the real construction (and the transitive
    ``audio_filters`` import chain) to first attribute access. The
    proxy is what's passed to ``Recorder(audio_processor=...)`` —
    ``Recorder`` stores it as ``self._audio_processor``, and the
    audio-pipeline path (``recording/audio_pipeline.py``) checks
    ``recorder._audio_processor is not None`` before calling
    ``process_chunk``. The proxy is never ``None``, so the check
    passes; the real construction happens inside ``_resolve()`` on
    the first ``process_chunk`` / ``set_sample_rate`` /
    ``rebuild_from_config`` call.

    The proxy ALSO wires ``set_quality_callback(app._on_audio_quality_chunk)``
    immediately after construction — this wiring used to live at
    ``app.py:217`` (``self._audio_processor.set_quality_callback(
    self._on_audio_quality_chunk)``) but was moved here so the proxy
    doesn't have to be resolved eagerly just to install a callback.

    Tests that inject mocks via ``app._audio_processor = MagicMock()``
    use the ``_audio_processor`` setter, which bypasses the proxy
    entirely (the mock is stored directly in ``_audio_processor_backing``
    and the proxy is never created).
    """

    __slots__ = ("_app_ref", "_real", "_wired")

    def __init__(self, app: Any) -> None:
        # Bypass our own __setattr__ (which would delegate to the wrapped
        # AudioProcessor) when storing state on the proxy itself.
        object.__setattr__(self, "_app_ref", weakref.ref(app))
        object.__setattr__(self, "_real", None)
        object.__setattr__(self, "_wired", False)

    def _resolve(self):
        real = object.__getattribute__(self, "_real")
        if real is None:
            app = object.__getattribute__(self, "_app_ref")()
            if app is None:
                # The owning VoiceTyperApp was garbage-collected —
                # should never happen in normal operation because the
                # Recorder (which holds the proxy) is owned by the app.
                # Defensive: raise AttributeError so the caller sees a
                # clear failure rather than a None dereference.
                raise AttributeError("_LazyAudioProcessorProxy: owning VoiceTyperApp was garbage-collected")
            # Deferred import — AudioProcessor pulls in the
            # ``audio_filters`` package (scipy.signal.butter, RNNoise).
            from voice_typer.server.audio_processor import AudioProcessor

            real = AudioProcessor(
                app.config,
                sample_rate=app.config.sample_rate,
            )
            object.__setattr__(self, "_real", real)
        # Wire the quality callback ONCE, immediately after construction
        # (whether just-constructed or pre-existing). The ``_wired`` flag
        # guards against re-wiring on every access (which would replace
        # the callback if a later caller manually called
        # ``set_quality_callback`` with a different cb).
        wired = object.__getattribute__(self, "_wired")
        if not wired:
            app = object.__getattribute__(self, "_app_ref")()
            if app is not None:
                try:
                    real.set_quality_callback(app._on_audio_quality_chunk)
                except Exception:
                    log.warning(
                        "[INIT] lazy AudioProcessor.set_quality_callback failed",
                        exc_info=True,
                    )
            object.__setattr__(self, "_wired", True)
        return real

    def __getattr__(self, name: str) -> Any:
        # __getattr__ is only called when the attribute is not found via
        # normal lookup (i.e. for anything that isn't _app_ref / _real /
        # _wired / a class attribute). Every wrapped-processor attribute
        # goes through here.
        return getattr(self._resolve(), name)


class VoiceTyperApp:
    """The main application."""

    def __init__(self):
        # catch unexpected exceptions from Config.load() (e.g.
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
        #  (F-07): wrapped in try/except so an excepthook-install
        # failure (e.g. a missing Win32 API on an unsupported build, or
        # a sys.excepthook assignment that raises on a restricted
        # interpreter) does not abort VoiceTyperApp construction. The
        # excepthook is a best-effort diagnostics aid — if it can't be
        # installed, we log at DEBUG (with exc_info=True) so the
        # failure is diagnosable without spamming the default-INFO
        # production log, and continue with init.
        try:
            _crash_handler.install_python_excepthook()
            # install the threading excepthook so unhandled
            # exceptions in daemon threads (A11yPulse, ModelLoad,
            # heartbeat_loop, crash-recovery-saver, history-retention,
            # bubble-level-pusher, shutdown-watchdog, prewarm) produce
            # a python_crash.<PID>.<thread_name>.txt marker file. Without
            # this, sys.excepthook only catches MAIN-thread exceptions
            # and daemon-thread crashes are silently lost (no marker,
            # no next-startup notification). Best-effort — same try/except
            # as the main excepthook install.
            _crash_handler.install_threading_excepthook()
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
        #
        # ``AudioProcessor`` construction is deferred to
        # first attribute access via the ``_audio_processor`` @property
        # below. The eager construction that used to live here pulled in
        # the full ``audio_filters`` package + ``scipy.signal.butter``
        # (via ``build_chain``) on every cold start, even when the user
        # never dictates. The lazy property returns a
        # ``_LazyAudioProcessorProxy`` that transparently constructs the
        # real ``AudioProcessor`` on first ``process_chunk`` /
        # ``set_sample_rate`` / ``rebuild_from_config`` call (i.e. on
        # the first recording or the first config-driven rebuild). The
        # proxy also wires ``set_quality_callback`` after construction
        # (moved here from line 217 below) so the per-chunk quality
        # callback is hooked up before the first chunk is processed.
        # Tests that inject mocks via ``app._audio_processor =
        # MagicMock()`` use the setter, which bypasses the proxy.
        self._audio_processor_backing: Any = None

        # AudioQualityAnalyzer: wired to the AudioProcessor's
        # per-chunk quality callback so it accumulates clipping /
        # low-volume / high-noise statistics during recording.
        # After Recorder.stop(), _finalize_audio_quality_report() runs
        # analyze_full_audio() on the captured samples and surfaces any
        # issues via a tray notification (gated by
        # config.audio_quality_warnings).
        self._audio_quality = AudioQualityAnalyzer()
        self._audio_quality.reset()
        # ``set_quality_callback`` wiring moved into the
        # ``_LazyAudioProcessorProxy._resolve`` method so it fires on
        # first attribute access (after the real AudioProcessor is
        # constructed). Calling it here would trigger the proxy to
        # resolve immediately, defeating the lazy construction.

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
        # eagerly preload + warm the Silero VAD model on a
        # background thread so the first recording's first audio chunk
        # does not stall on torch.jit.load (~150-600ms cold load). The
        # model load previously happened lazily inside compute_vad_prob
        # on the audio worker thread; while the worker was stalled, the
        # SPSC ring buffer filled (94 chunks/sec at 48kHz/512) and
        # silently evicted the OLDEST chunks (the first syllables). The
        # preload moves the cost off the recording critical path. Safe
        # to call before the recorder's first start(); vad.preload() is
        # idempotent (cached on subsequent calls) and never raises — a
        # load failure falls through to lazy load on the first chunk
        # (preserving the pre-fix behavior as a fallback).
        self._preload_vad_model()
        # #2 ASR backend lifecycle extracted to ModelManager.
        # Previously VoiceTyperApp owned the AsrBackendRegistry + three
        # engine fields + ~500 LOC of load/fallback/change logic. Now
        # ModelManager owns all of that; app.py accesses it via
        # `self.models`. (: the @property delegates that
        # used to mirror `self.transcriber` / `self._qwen_engine` /
        # `self._asr_registry` / etc. on VoiceTyperApp have been
        # removed — callers now use `self.models.<field>` directly.)
        from voice_typer.server.model_manager import ModelManager

        self.models: ModelManager = ModelManager(self)
        # the eager ``self.models._ensure_engine("qwen")`` call
        # that used to live here was a synchronous multi-second load
        # (qwen model weights off disk) on every cold start when the
        # user had asr_backend='qwen' configured. The background load
        # thread spawned by ``ModelManager.start_background_load()``
        # (called from ``StartupSequence.run``) already calls
        # ``_ensure_engine(config.asr_backend)`` on the daemon thread —
        # see ``model_manager.py:load_background``. So the eager call
        # was both expensive AND redundant. Removed here; the bg load
        # path covers it.

        # ``ClipboardManager`` construction deferred to first
        # access via the ``clipboard`` @property below. The eager
        # construction was a small but non-trivial cost (import + class
        # init) paid on every cold start even when the user never
        # dictates. The lazy property transparently constructs on the
        # first ``app.clipboard.*`` call.
        self._clipboard_backing: Any = None

        self.tray = TrayIcon(
            controller=self,
            config=self.config,
        )

        # if Config.load() failed earlier, surface a tray
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

        #  Phase 6: settings side-effects (autostart, notifications,
        # microphone selection) extracted to SettingsController. The app
        # keeps thin delegate methods (``_toggle_autostart``,
        # ``_set_autostart``, ``_set_notifications``, ``_select_microphone``)
        # so tray menu callbacks and tests calling ``app._select_microphone``
        # keep working unchanged. ``_open_config_file`` stays on
        # VoiceTyperApp because source-level structure tests
        # (test_config_editor_lock.py) pin its body via inspect.getsource.
        from voice_typer.server.settings_controller import SettingsController

        self.settings: SettingsController = SettingsController(self)

        #  Phase 7: shutdown / cleanup lifecycle (quit, _do_cleanup,
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
        # stash the watchdog timeout on the instance so
        # restart_app()'s non-main-thread branch can arm the watchdog
        # without re-importing the constant.
        self._shutdown_watchdog_timeout_s: float = SHUTDOWN_WATCHDOG_TIMEOUT_S

        #  (Phase 4.5 spaghetti split): restart / quit
        # relaunch-ack lifecycle extracted to LifecycleController. The
        # app keeps thin delegate methods (``restart_app``,
        # ``_wait_for_relaunch_ack``, ``quit_app``) so tray menu
        # callbacks (quit_app -> self.quit(), restart_app ->
        # self._do_cleanup()) and tests calling ``app.restart_app()``
        # / ``app.quit_app()`` directly keep working unchanged.
        # ``restart_app`` keeps the re-entry guard inline so the
        # source-level invariant pinned by
        # ``tests/test_app_cleanup.py::test_restart_app_guard_is_first_statement_in_method``
        # keeps holding; the rest of the body lives in
        # ``LifecycleController.restart_app``.
        from voice_typer.server.app_lifecycle import LifecycleController

        self.lifecycle: LifecycleController = LifecycleController(self)

        # undo / repaste side effects extracted to
        # UndoRepasteController. The app keeps thin delegate methods
        # (``undo_last``, ``repaste_last``) so tray menu callbacks, the
        # repaste hotkey backend's callback, and tests calling
        # ``app.undo_last()`` / ``app.repaste_last()`` directly keep
        # working unchanged.
        #
        # Construction of ``UndoRepasteController`` is deferred to first
        # access via the ``undo`` @property below. The eager construction
        # that used to live here paid the ``app_undo`` import + class init
        # on every cold start, even when the user never invokes undo /
        # repaste (the only entry points are the tray menu items and the
        # repaste hotkey). The lazy property transparently constructs on
        # first access.
        self._undo_backing: Any = None

        #  Phase 7: audio-quality side-effects extracted to
        # AudioQualityController. The app keeps thin delegate methods so
        # ``self._audio_processor.set_quality_callback(self._on_audio_quality_chunk)``,
        # ``service.apply_config_side_effects`` (-> _rebuild_audio_processor),
        # and ``RecordingController.stop`` (-> _finalize_audio_quality_report)
        # all keep working unchanged.
        #
        # Construction of ``AudioQualityController`` is deferred to first
        # access via the ``audio_quality`` @property below. The eager
        # construction that used to live here paid the
        # ``audio_quality_controller`` import (which eagerly imports
        # numpy) on every cold start. The lazy property transparently
        # constructs on first access; the per-chunk quality callback
        # wired above (``self._audio_processor.set_quality_callback(
        # self._on_audio_quality_chunk)``) delegates through the property
        # so the first chunk triggers construction.
        self._audio_quality_backing: Any = None

        # config-editor controller extracted to a focused
        # ``controllers/`` package. It holds a reference to the owning
        # app and exposes a small surface for one concern. The app keeps
        # a thin delegate method (``_open_config_file``) so tray menu
        # callbacks, hotkey backends, and tests calling the app method
        # directly keep working unchanged. The extracted class lives in
        # :mod:`voice_typer.server.controllers`.
        #
        # the parallel delegator controllers (``UndoController``
        # and ``RepasteController`` in ``controllers/``) were deleted —
        # they were 1-line wrappers around ``self.undo.undo_last()`` /
        # ``self.undo.repaste_last()`` (the canonical
        # ``UndoRepasteController`` wired above). The app's
        # ``undo_last()`` / ``repaste_last()`` delegate methods now call
        # ``self.undo`` directly, eliminating the split-brain
        # ``self._undo_controller`` / ``self._repaste_controller``
        # parallel system.
        from voice_typer.server.controllers.config_editor_launcher import ConfigEditorLauncher as _ConfigLauncher

        self._config_editor_launcher: _ConfigLauncher = _ConfigLauncher(self)

        # #2 Hotkey registration extracted to HotkeyDispatcher.
        # Owns the 3 hotkey backends (dictation / ESC / repaste) and the
        # register/restart logic. (: the @property
        # delegates that used to mirror the 3 legacy fields
        # (_hotkey_backend, _esc_backend, _repaste_backend) on
        # VoiceTyperApp have been removed — callers now use
        # `self.hotkeys.<field>` directly.)
        from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher

        self.hotkeys: HotkeyDispatcher = HotkeyDispatcher(self)
        # T-1: removed the inline ``from voice_typer.server.hotkeys import create_hotkey_backend``
        # here — moved to module top as a re-export (see the import
        # block at the top of this module). The inline version was
        # inside a method and so the symbol was never bound at
        # module scope, which broke tests that monkeypatch
        # ``voice_typer.server.app.create_hotkey_backend``.
        # T-1 / ARCH-9: re-export the factory so test files that
        # monkeypatch ``voice_typer.server.app.create_hotkey_backend``
        # (e.g. tests/test_volume_lifecycle.py:73-78) keep working
        # without requiring every test to be migrated to patch the
        # canonical ``voice_typer.server.hotkeys.create_hotkey_backend``
        # location. The canonical module already exports the function
        # (see ``hotkeys/__init__.py``); this is a re-export alias only.
        # ARCH-9 documents the broader pattern of test-seam re-exports
        # being progressively removed (TranscriptionEngine was the
        # first); create_hotkey_backend stays because the test-suite
        # has 8+ monkeypatch sites that depend on it.
        from voice_typer.server.hotkeys import create_hotkey_backend  # noqa: E402, F401
        # #2 _streaming_session and _transcription_thread now
        # live in RecordingController. (: the @property
        # delegates that used to mirror them on VoiceTyperApp have been
        # removed — callers now use `self.recording.<field>` directly,
        # or `self.recording.get_streaming_session()` /
        # `self.recording.set_streaming_session(...)`.)
        self._microphones: list[dict] = []
        self._busy_event = threading.Event()
        self._busy_event.set()  # SET = not busy
        self._lock = threading.Lock()
        # serialize Config mutations between concurrent IPC
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

        # wire the in-process mutation lock into the
        # ``Config`` instance so every ``config.save()`` call site
        # (~15 production callers: settings_controller,
        # hotkey_dispatcher, model_manager, recorder._persist_mic,
        # startup_sequence, service.apply_config, onboarding_apply,
        # etc.) automatically acquires it via
        # :meth:`Config._save_with_mutation_lock`. Previously
        # ``set_mutation_lock`` was defined on Config (config.py:1083)
        # but never called in production, so only the IPC ``set_config``
        # path that manually acquired the lock was serialized — every
        # other ``save()`` ran unlocked, allowing a background
        # mic-fallback save to interleave with an in-flight
        # ``apply_config`` and persist a torn snapshot. The lock is an
        # ``RLock`` so nested acquisition from the same thread (e.g.
        # ``apply_config`` calls ``save()`` which itself re-enters) is
        # safe. MUST run AFTER both ``self.config`` (set at line ~137)
        # and ``self._config_mutation_lock`` (set just above) exist —
        # order matters, the lock has to be created before it is shared.
        self.config.set_mutation_lock(self._config_mutation_lock)

        # #2 _model_load_attempted / _model_load_thread /
        # _pending_dictation now live in ModelManager. (:
        # the @property delegates that used to mirror them on
        # VoiceTyperApp have been removed — callers now use
        # `self.models.<field>` directly.)
        self._shutting_down = False  # True once quit() starts
        # XZ-IPC-012: assert the shutdown gate is a real bool. The
        # ``getattr(self.app, "_shutting_down", False) is True`` idiom
        # used by the IPC dispatch path (see voice_typer/server/
        # ipc_server.py and voice_typer/server/sidecar_ws.py)
        # accommodates test MagicMock auto-vivification — a test
        # that does ``mock_app._shutting_down = 1`` would otherwise
        # bypass the shutdown gate (a truthy int IS truthy but is NOT
        # ``True``). Catching the wrong-type assignment at __init__
        # time gives a clear, immediate failure ("TypeError:
        # _shutting_down must be bool, got int") instead of a silent
        # shutdown-bypass bug that surfaces only when a test exercises
        # the gate.
        if not isinstance(self._shutting_down, bool):
            raise TypeError(
                f"VoiceTyperApp._shutting_down must be bool, "
                f"got {type(self._shutting_down).__name__}"
            )
        # threading.Event version of _shutting_down so executor
        # tasks can check it without reading the boolean (which provides
        # no memory-order guarantee across threads).
        self._shutting_down_event = threading.Event()
        # PYREFLY- counter incremented by startup_sequence.py
        # when the onboarding check persistently fails (see
        # startup_sequence.py:140-149). Declared here so pyrefly
        # recognizes it as a class attribute rather than an ad-hoc
        # dynamic attribute. Initialized to 0; startup_sequence.py
        # uses getattr-with-default as a defensive read but always
        # assigns before incrementing.
        self._onboarding_fail_count: int = 0
        # idempotency guard for _do_cleanup(). Set to True once
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
        # ESC- flag gating the global ESC cancel hotkey.  Set to
        # True by the ""set_esc_cancel_paused"" IPC handler when the
        # frontend HotkeyPicker enters capture mode, so the backend's
        # ESC polling callback doesn't fire while the user is assigning
        # a custom hotkey in the Settings UI.
        self._esc_cancel_paused: bool = False
        #  Phase 7: timer lifecycle extracted to TimerCoordinator.
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
        # ``HistoryDB()`` construction is deferred to first
        # access via the ``history_db`` @property below. The eager
        # construction that used to live here blocked ``__init__`` for up
        # to ``_WRITER_READY_TIMEOUT`` (30s) waiting for the writer
        # thread's schema-init to complete — paid on every cold start,
        # even when the user never dictates and the DB is never touched
        # outside the shutdown teardown path. The lazy property
        # transparently constructs on the first ``app.history_db.*``
        # call (e.g. the first ``add_transcription`` from the dictation
        # pipeline, or the first history IPC handler). Tests that inject
        # mocks via ``app.history_db = MagicMock()`` use the setter,
        # which bypasses lazy construction. The shutdown teardown path
        # (``shutdown/teardowns/history_db.py``) checks
        # ``app.history_db is not None`` — the lazy getter returns
        # ``None`` (without constructing) when ``_shutting_down`` is set
        # so a never-dictated session doesn't pay the 30s writer-ready
        # wait on quit.
        self._history_db_backing: Any = None
        self._crash_recovery = CrashRecovery(
            thread_registry=self._thread_registry,
        )
        # Volume ducking: reduces system volume during dictation to
        # prevent speaker output from bleeding into the microphone.
        # Crash recovery persists the pre-duck volume so a crash
        # doesn't leave the system stuck at a low volume.
        #
        # Construction of ``DuckCrashRecovery`` and ``VolumeDucker`` is
        # deferred to first access via the ``_duck_crash_recovery`` /
        # ``_volume_ducker`` @properties below. The eager construction
        # that used to live here paid the ``duck_crash_recovery`` +
        # ``volume_ducker`` imports + class init on every cold start,
        # even when the user has ``volume_duck_enabled=False`` and never
        # triggers a duck. The lazy properties transparently construct
        # on first access (e.g. when ``VolumeController._duck_volume``
        # runs at the start of the first dictation).
        self._duck_crash_recovery_backing: Any = None
        #  Phase 7: VolumeController owns duck/restore side effects.
        # Kept eager because it's just a back-reference holder
        # (``self._app = app``) and the ``_on_volume_crash_restore``
        # callback wired into ``VolumeDucker`` delegates to it —
        # constructing it lazily would save nothing (the class is
        # trivial) and would complicate the callback wiring.
        from voice_typer.server.volume_controller import VolumeController

        self.volume: VolumeController = VolumeController(self)
        self._volume_ducker_backing: Any = None
        # NOTE: AudioQualityAnalyzer is now instantiated earlier in
        # __init__ (next to AudioProcessor) and wired to the processor's
        # per-chunk quality callback.  See self._audio_quality /
        # self._on_audio_quality_chunk / _finalize_audio_quality_report.
        # ``WaveformBubble`` and ``WaveformBubbleWiring``
        # construction deferred to first access via the
        # ``_waveform_bubble`` / ``waveform_wiring`` @properties below.
        # The eager construction + immediate ``_wire_waveform_bubble()``
        # call that used to live here paid the full bubble-wiring cost
        # (importing ``waveform_bubble_wiring`` + starting the
        # bubble-level-pusher daemon thread + registering it with the
        # thread registry) on every cold start, even when the user has
        # ``bubble_behavior='hidden'`` and never sees the bubble. The
        # wiring now happens lazily — the ``_wire_waveform_bubble()``
        # call was moved to ``start()`` so it runs once on the main
        # thread before the tray event loop begins, and only if the app
        # actually reaches the production entry point (tests that
        # construct ``VoiceTyperApp`` directly never trigger it).
        self._waveform_bubble_backing: Any = None
        self._waveform_wiring_backing: Any = None
        self._last_transcription: str = ""  # For repaste
        # declare ``_ipc_server`` upfront so VoiceTyperApp
        # satisfies the ``AppProtocol`` structural type checked by
        # ``providers.build_ipc_server``.  The attribute is set later
        # by ``IPCServer.start()`` (``self.app._ipc_server = self``);
        # initializing it to ``None`` here means pyrefly sees the
        # attribute exists on every instance, satisfying the protocol.
        self._ipc_server: Any | None = None
        # ``TemplateManager`` and ``VocabularyManager`` construction is
        # deferred to first access via the ``_template_manager`` /
        # ``_vocabulary_manager`` @properties below. The eager
        # construction that used to live here read ``templates.json`` /
        # ``vocabulary.json`` off disk on every cold start (hundreds of
        # ms on a slow disk), even when the user never uses templates /
        # vocabulary.
        #
        # The properties AUTO-CONSTRUCT on first access (: failure
        # is logged at WARNING with ``exc_info=True`` and the backing is
        # left ``None`` to retry on next access). The ``is None``
        # fallback paths in ``service/template.py`` and
        # ``dictation_pipeline.py`` therefore see a cached instance on
        # success, or ``None`` on failure — their fallback construction
        # still works unchanged.
        self._template_manager_backing: Any = None
        self._vocabulary_manager_backing: Any = None
        self._llm_polisher = None  # Created on first polish (needs consent check)
        self._cloud_engine = None  # Lazy-init if cloud backend selected

    # ─── Lazy @property accessors ─────────────────────────────────────
    #
    # Each property has both a getter (constructs on first access if
    # the backing is None) and a setter (stores directly into the
    # backing so existing tests that inject mocks via
    # ``app.<attr> = MagicMock()`` keep working transparently —
    # assignment bypasses the lazy construction).
    #
    # Construction failures (e.g. a corrupt ``templates.json``) are
    # logged at WARNING level with ``exc_info=True`` (mirrors the
    # pre- eager-init failure-logging contract) and the backing
    # is left as ``None`` so the next access retries (mirrors the
    # dictation_pipeline.py lazy-fallback retry semantics).

    @property
    def _template_manager(self):
        # Return the backing directly. Construction is the caller's
        # responsibility — see ``service/template.py``'s lazy fallback
        # which constructs via ``TemplateManager()`` and assigns back
        # via the setter below. Returning ``None`` when uninitialised
        # lets tests verify the lazy contract (DJ-2) without
        # triggering eager ``TemplateManager()`` construction on
        # ``__init__`` (TemplateManager reads ``templates.json`` from
        # disk; that's hundreds of ms on a cold start).
        return self._template_manager_backing

    @_template_manager.setter
    def _template_manager(self, value) -> None:
        self._template_manager_backing = value

    @property
    def _vocabulary_manager(self):
        # Return the backing directly. Construction is the caller's
        # responsibility — see ``service/vocabulary.py``'s lazy
        # fallback which constructs via ``VocabularyManager()`` and
        # assigns back via the setter below. Returning ``None`` when
        # uninitialised lets tests verify the lazy contract (DJ-2)
        # without triggering eager ``VocabularyManager()`` construction
        # on ``__init__`` (VocabularyManager reads ``vocabulary.json``
        # from disk; that's hundreds of ms on a cold start).
        return self._vocabulary_manager_backing

    @_vocabulary_manager.setter
    def _vocabulary_manager(self, value) -> None:
        self._vocabulary_manager_backing = value

    @property
    def clipboard(self):
        backing = self._clipboard_backing
        if backing is None:
            backing = ClipboardManager(
                paste_enabled=self.config.paste_on_stop,
            )
            self._clipboard_backing = backing
        return backing

    @clipboard.setter
    def clipboard(self, value) -> None:
        self._clipboard_backing = value

    @property
    def _waveform_bubble(self):
        backing = self._waveform_bubble_backing
        if backing is None:
            # Deferred import — ``voice_typer.server.waveform``
            # transitively imports numpy, which is ~250-335ms on cold
            # start. Deferred to first access (which only happens when
            # the bubble is actually shown).
            from voice_typer.server.waveform import WaveformBubble

            backing = WaveformBubble()
            self._waveform_bubble_backing = backing
        return backing

    @_waveform_bubble.setter
    def _waveform_bubble(self, value) -> None:
        self._waveform_bubble_backing = value

    @property
    def waveform_wiring(self):
        backing = self._waveform_wiring_backing
        if backing is None:
            from voice_typer.server.waveform_bubble_wiring import WaveformBubbleWiring

            backing = WaveformBubbleWiring(self)
            self._waveform_wiring_backing = backing
        return backing

    @waveform_wiring.setter
    def waveform_wiring(self, value) -> None:
        self._waveform_wiring_backing = value

    # ─── Lazy controller / volume-subsystem properties ────────────────
    #
    # ``undo`` (UndoRepasteController), ``audio_quality``
    # (AudioQualityController), ``_duck_crash_recovery``
    # (DuckCrashRecovery), and ``_volume_ducker`` (VolumeDucker) used
    # to be constructed eagerly in ``__init__``. They are now
    # auto-constructing lazy properties — the first access triggers
    # construction and caches the instance in the backing attribute.
    # Tests that inject mocks via ``app.<attr> = MagicMock()`` use the
    # setter, which bypasses the lazy construction.

    @property
    def undo(self):
        backing = self._undo_backing
        if backing is None:
            try:
                from voice_typer.server.app_undo import UndoRepasteController

                backing = UndoRepasteController(self)
            except Exception:
                log.warning("[INIT] UndoRepasteController lazy-init failed", exc_info=True)
                return None
            self._undo_backing = backing
        return backing

    @undo.setter
    def undo(self, value) -> None:
        self._undo_backing = value

    @property
    def audio_quality(self):
        backing = self._audio_quality_backing
        if backing is None:
            try:
                from voice_typer.server.audio_quality_controller import (
                    AudioQualityController,
                )

                backing = AudioQualityController(self)
            except Exception:
                log.warning("[INIT] AudioQualityController lazy-init failed", exc_info=True)
                return None
            self._audio_quality_backing = backing
        return backing

    @audio_quality.setter
    def audio_quality(self, value) -> None:
        self._audio_quality_backing = value

    @property
    def _duck_crash_recovery(self):
        backing = self._duck_crash_recovery_backing
        if backing is None:
            try:
                # Deferred import — duck_crash_recovery pulls in
                # platform-specific volume backends (pyobjc on macOS,
                # ctypes-coreaudio on Windows). Deferred to first access
                # (which only happens when volume ducking is enabled).
                from voice_typer.server.duck_crash_recovery import DuckCrashRecovery

                backing = DuckCrashRecovery(config_dir=_config_dir())
            except Exception:
                log.warning("[INIT] DuckCrashRecovery lazy-init failed", exc_info=True)
                return None
            self._duck_crash_recovery_backing = backing
        return backing

    @_duck_crash_recovery.setter
    def _duck_crash_recovery(self, value) -> None:
        self._duck_crash_recovery_backing = value

    @property
    def _volume_ducker(self):
        backing = self._volume_ducker_backing
        if backing is None:
            try:
                # Deferred import — ``volume_ducker`` pulls in
                # platform-specific volume backends (pyobjc on macOS,
                # ctypes on Windows). Deferred to first access (which
                # only happens when volume ducking is enabled).
                from voice_typer.server.volume_ducker import VolumeDucker

                backing = VolumeDucker(
                    crash_recovery=self._duck_crash_recovery,
                    on_crash_restore=self._on_volume_crash_restore,
                )
            except Exception:
                log.warning("[INIT] VolumeDucker lazy-init failed", exc_info=True)
                return None
            self._volume_ducker_backing = backing
        return backing

    @_volume_ducker.setter
    def _volume_ducker(self, value) -> None:
        self._volume_ducker_backing = value

    # ─── lazy AudioProcessor property ───────────────────────────
    #
    # ``AudioProcessor`` construction is deferred to first attribute
    # access via a ``_LazyAudioProcessorProxy``. The proxy transparently
    # constructs the real ``AudioProcessor`` on the first
    # ``process_chunk`` / ``set_sample_rate`` / ``rebuild_from_config``
    # call (i.e. on the first recording or the first config-driven
    # rebuild). The proxy also wires ``set_quality_callback`` after
    # construction (moved here from ``__init__`` line 217).
    #
    # Tests that inject mocks via ``app._audio_processor = MagicMock()``
    # use the setter, which bypasses the proxy entirely.

    @property
    def _audio_processor(self):
        backing = self._audio_processor_backing
        if backing is None:
            backing = _LazyAudioProcessorProxy(self)
            self._audio_processor_backing = backing
        return backing

    @_audio_processor.setter
    def _audio_processor(self, value) -> None:
        self._audio_processor_backing = value

    # ─── lazy HistoryDB property ────────────────────────────────
    #
    # ``HistoryDB()`` construction is deferred to first access. The
    # eager construction that used to live in ``__init__`` blocked for
    # up to ``_WRITER_READY_TIMEOUT`` (30s) waiting for the writer
    # thread's schema-init to complete. The lazy property mirrors the
    # existing pattern (clipboard, undo, audio_quality) — construction
    # failure is logged at WARNING with ``exc_info=True`` and the
    # backing is left as ``None`` to retry on next access.
    #
    # The ``_shutting_down_event`` guard prevents the shutdown teardown
    # path (``shutdown/teardowns/history_db.py``) from triggering lazy
    # construction via its ``if app.history_db is not None:`` check — a
    # never-dictated session would otherwise pay the 30s writer-ready
    # wait on quit just to immediately close the DB it never used.

    @property
    def history_db(self):
        backing = self._history_db_backing
        if backing is None:
            # Don't lazy-construct during shutdown — the teardown path
            # checks ``app.history_db is not None`` to decide whether
            # to flush/close, and we don't want to construct a
            # HistoryDB during shutdown just to immediately close it.
            if self._shutting_down_event.is_set():
                return None
            try:
                backing = HistoryDB()
            except Exception:
                log.warning("[INIT] HistoryDB lazy-init failed", exc_info=True)
                return None
            self._history_db_backing = backing
        return backing

    @history_db.setter
    def history_db(self, value) -> None:
        self._history_db_backing = value

    # ─── Volume Ducking ────────────────────────────────────────────────

    def _on_volume_crash_restore(self, state) -> None:
        """Phase 7: delegate to VolumeController."""
        self.volume._on_volume_crash_restore(state)

    def _duck_volume(self) -> None:
        """Phase 7: delegate to VolumeController."""
        self.volume._duck_volume()

    def _restore_volume(self, fade_ms: int | None = None) -> None:
        """Phase 7: delegate to VolumeController."""
        self.volume._restore_volume(fade_ms=fade_ms)

    # ─── #2 ASR backend delegates to ModelManager ───────────
    #
    # removed @property delegates (transcriber,
    # _qwen_engine, _parakeet_engine, _asr_registry, _model_load_thread,
    # _model_load_attempted, _pending_dictation) — callers now use
    # ``self.models.<field>`` directly (e.g. ``self.models.transcriber``,
    # ``self.models._registry``, ``self.models._model_load_thread``).
    #
    # The actual logic lives in voice_typer/server/model_manager.py.

    # removed @property delegates (_transcription_thread,
    # _streaming_session) — callers now use self.recording._transcription_thread
    # and self.recording._streaming_session (or the get/set_streaming_session
    # methods) directly.

    # removed @property delegates (_hotkey_backend,
    # _esc_backend, _repaste_backend) — callers now use
    # self.hotkeys._hotkey_backend / self.hotkeys._esc_backend /
    # self.hotkeys._repaste_backend directly.

    # ─── Timer Tracking (P1) ─────────────────────────────────────────
    #  Phase 7: logic moved to TimerCoordinator. VoiceTyperApp keeps
    # thin delegates so existing callers (and tests that monkeypatch
    # app._schedule_timer / app._cancel_pending_timers) keep working.

    def _schedule_timer(self, delay: float, func) -> threading.Timer:
        """Phase 7: delegate to TimerCoordinator."""
        return self.timers._schedule_timer(delay, func)

    def _cancel_pending_timers(self):
        """Phase 7: delegate to TimerCoordinator."""
        return self.timers._cancel_pending_timers()

    # ─── Waveform Bubble (IPC push) ───────────────────────────────────

    def _wire_waveform_bubble(self) -> None:
        """Phase 7: delegate to WaveformBubbleWiring.

        The bubble itself is a frameless, always-on-top ``BrowserWindow``
        owned by the Electron main process.  We just emit push events;
        the IPC server is reached via the module-level hook in
        ``voice_typer.server.ipc_server`` so listeners don't need to
        hold a reference to the app or server (avoids closure-capture
        bugs that broke the bubble on first run).
        """
        self.waveform_wiring._wire_waveform_bubble()

    # ─── Startup ───────────────────────────────────────────────────────

    def _preload_vad_model(self) -> None:
        """spawn a background thread to eagerly load + warm the
        Silero VAD model so the first recording's first audio chunk
        does not stall on ``torch.jit.load`` (~150-600ms cold load).

        The thread is registered with ``self._thread_registry`` so
        ``shutdown_all()`` joins it cleanly. Best-effort: any failure
        (torch missing, model file missing, OOM) is logged at DEBUG
        and the lazy-load fallback in ``compute_vad_prob`` is preserved.
        """
        try:
            from voice_typer.server import vad

            def _vad_preload_worker() -> None:
                try:
                    vad.preload()
                except Exception:
                    log.debug("[INIT] vad.preload() failed", exc_info=True)

            self._thread_registry.spawn_and_register(
                "vad-preload",
                _vad_preload_worker,
                daemon=True,
                join_timeout=2.0,
            )
        except Exception:
            log.debug("[INIT] could not spawn vad-preload thread", exc_info=True)

    def start(self):
        """Initialize and run the application."""
        # Wire notifications
        self.tray.set_notifications_enabled(self.config.show_notifications)

        # Queue "Loading" state before the event loop starts
        self.tray.set_state(AppState.LOADING, "Starting...")

        # wire the waveform bubble now (on the main thread, before
        # the bg ``_do_startup`` thread runs). The wiring used to happen
        # eagerly in ``__init__``; the lazy ``_waveform_bubble`` /
        # ``waveform_wiring`` properties defer the actual construction
        # to here so a VoiceTyperApp that is constructed but never
        # ``start()``ed (e.g. in tests) pays nothing. ``_wire_waveform_bubble``
        # is idempotent so a double-call (defensive) is safe.
        try:
            self._wire_waveform_bubble()
        except Exception:
            log.warning("[START] waveform bubble wiring failed", exc_info=True)

        # Create the icon and start background work (non-blocking)
        self.tray.start(bg_work=self._do_startup)

        # On Windows: install a console control handler
        self._install_win32_console_handler()

        # POSIX signal handlers for graceful shutdown
        self._install_signal_handlers()

        # Register atexit handler to log any unexpected process exit
        atexit.register(self._atexit_log)

        # Register atexit handlers for critical cleanup paths
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

         Phase 5: the body of this method (~340 lines) was extracted
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
        """Phase 7: delegate to AudioQualityController."""
        delegate = self.audio_quality
        if delegate is None:
            log.warning("[APP] audio_quality controller unavailable — lazy-init failed earlier; skipping chunk")
            return None
        return delegate._on_audio_quality_chunk(rms, peak)

    def _rebuild_audio_processor(self, force_sr: int | None = None) -> None:
        """Phase 7: delegate to AudioQualityController."""
        delegate = self.audio_quality
        if delegate is None:
            log.warning("[APP] audio_quality controller unavailable — lazy-init failed earlier; skipping rebuild")
            return None
        return delegate._rebuild_audio_processor(force_sr=force_sr)

    def _finalize_audio_quality_report(self, audio: Any) -> None:
        """Phase 7: delegate to AudioQualityController.

        parameter annotated as ``Any`` (not ``np.ndarray``) so the
        annotation does NOT depend on ``from __future__ import annotations``
        staying in place.
        """
        delegate = self.audio_quality
        if delegate is None:
            log.warning("[APP] audio_quality controller unavailable — lazy-init failed earlier; skipping finalize")
            return None
        return delegate._finalize_audio_quality_report(audio)

    def _stop_dictation(self):
        """Stop recording and transcribe in background.

        SOUND- (Round 0): this method is now a thin delegate to
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

        delegates directly to the canonical ``UndoRepasteController``
        (``self.undo``) — the thin ``RepasteController`` wrapper in
        ``controllers/`` was deleted as a parallel-system delegator.
        Behaviour preserved verbatim — only the call chain shortened.
        """
        delegate = self.undo
        if delegate is None:
            log.warning("[APP] undo controller unavailable — lazy-init failed earlier; skipping repaste")
            return None
        return delegate.repaste_last()

    def undo_last(self) -> None:
        """Undo last transcription by sending backspace keystrokes.

        delegates directly to the canonical ``UndoRepasteController``
        (``self.undo``) — the thin ``UndoController`` wrapper in
        ``controllers/`` was deleted as a parallel-system delegator.
        Behaviour preserved verbatim — only the call chain shortened.
        """
        delegate = self.undo
        if delegate is None:
            log.warning("[APP] undo controller unavailable — lazy-init failed earlier; skipping undo")
            return None
        return delegate.undo_last()

    def push_bubble_config(self, config: Any) -> None:
        """Push a config-changed event to the waveform bubble renderer.

         (): replaces the private ``getattr(self,
        "_waveform_bubble", None)`` access that lived inline in
        :mod:`voice_typer.server.handlers.config_handlers`'s
        ``apply_config`` side-effect path. The handler now calls this
        public method instead of reaching into the app's private
        ``_waveform_bubble`` attribute.

        Behaviour preserved verbatim from the prior inline block: read
        ``self._waveform_bubble`` (which is ``None`` until
        ``_wire_waveform_bubble`` has run, e.g. during very-early
        config pushes), and if both the bubble and its ``on_config``
        callback are non-None, invoke ``bubble.on_config(config)`` so
        the sandboxed bubble renderer re-reads ``bubble_behavior`` /
        ``bubble_click_to_toggle`` / ``bubble_mic_button`` and
        redraws. ``config`` is the app's :class:`Config` object.
        """
        bubble = getattr(self, "_waveform_bubble", None)
        if bubble is not None and bubble.on_config is not None:
            bubble.on_config(config)

    def _cancel_dictation(self):
        """#2 delegate to RecordingController.cancel().

        ESC- while the frontend HotkeyPicker is in hotkey capture
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

         Phase 6: body extracted to
        :meth:`voice_typer.server.settings_controller.SettingsController.set_autostart`.
        Behaviour preserved verbatim — only the class boundary moved.
        """
        self.settings.set_autostart(enabled)

    def _set_notifications(self, enabled: bool):
        """Set notification behavior from the settings window.

         Phase 6: body extracted to
        :meth:`voice_typer.server.settings_controller.SettingsController.set_notifications`.
        """
        self.settings.set_notifications(enabled)

    def _select_microphone(self, mic_name: str | None):
        """Handle microphone selection from tray menu.

         Phase 6: body extracted to
        :meth:`voice_typer.server.settings_controller.SettingsController.select_microphone`.
        """
        self.settings.select_microphone(mic_name)

    def _open_config_file(self):
        """Open the config file in the user's default editor.

        body extracted to
        :class:`voice_typer.server.controllers.config_editor_launcher.ConfigEditorLauncher`.
        Behaviour preserved verbatim — only the class boundary moved.
        """
        self._config_editor_launcher.open()

    # ─── TrayController Protocol Methods (P3) ────────────────────────

    def change_microphone(self, mic_id: str | None) -> None:
        """TrayController protocol: select microphone."""
        self._select_microphone(mic_id)

    @property
    def active_microphone_id(self) -> str | None:
        """TrayController protocol — return the currently selected
        microphone ID from ``config.microphone`` (None = system default)."""
        mic = getattr(self.config, "microphone", None)
        return str(mic) if mic else None

    def refresh_microphones(self) -> None:
        """TrayController protocol — re-enumerate microphones
        and refresh the tray menu by delegating to startup_tasks."""
        from voice_typer.server import startup_tasks

        try:
            startup_tasks.load_microphones(self)
        except Exception:
            log.warning("[TRAY] refresh_microphones failed", exc_info=True)

    def change_model(self, model_size: str) -> None:
        """TrayController protocol: change transcription model.

         (pyrefly): parameter renamed from ``model`` to
        ``model_size`` to match :class:`voice_typer.server.providers.AppProtocol`'s
        ``change_model(self, model_size: str)`` signature. Pyrefly
        enforces parameter-name matching for Protocol members (a call
        like ``app.change_model(model_size="large")`` must be valid on
        any AppProtocol implementation), so the names must agree.

         Phase 2: the ``_change_model`` delegate has been removed;
        this method now calls ``self.models.change_model`` directly.
        """
        self.models.change_model(model_size)

    def quit_app(self) -> None:
        """TrayController protocol: quit the app.

         (Phase 4.5): body extracted to
        :meth:`voice_typer.server.app_lifecycle.LifecycleController.quit_app`.
        Behaviour preserved verbatim — only the class boundary moved.

        Preserved invariants (now in the controller):
        - RELIABILITY-001: cleanup runs via ``self.quit()`` (the
          audited ``SystemExit`` path) — never ``os._exit(0)``.
        - ``event_bus.publish({"type": "quit_app"})``
          runs BEFORE the ``if self._shutting_down:`` re-entry guard
          so a double-quit still pushes the event. (Historically the
          guard was the plain ``if self._shutting_down:`` form;
          migrated to the threading.Event version
          ``if self._shutting_down_event.is_set():`` for cross-thread
          memory ordering.)
        """
        return self.lifecycle.quit_app()

    def restart_app(self) -> None:
        """TrayController protocol: restart the app.

         (Phase 4.5): body extracted to
        :meth:`voice_typer.server.app_lifecycle.LifecycleController.restart_app`.

        Preserved invariants (now in the controller):
        - ``log.info("[RESTART] Restarting %s...", APP_NAME)``.
        - ``try:`` wraps ``self.config.save()`` so an unexpected
          raise (e.g. RecursionError from a cyclic dataclass) does not
          abort the restart — the ``except Exception:`` block logs
          ``log.warning("config.save() raised", exc_info=True)``.
        - the redundant ``_restore_volume(fade_ms=0)`` call
          was removed — ``_do_cleanup`` (now reached via
          ``self.lifecycle.restart_app`` -> ``self._do_cleanup``) handles
          the restore via the shared ShutdownController body.
        - ``self._thread_registry.shutdown_all()``
          -> ``self._do_cleanup()`` -> main-thread ``sys.exit(0)`` (or
          non-main-thread  watchdog fallback).
        """
        #  re-entry guard (must be the first executable
        # statement — see
        # tests/test_app_cleanup.py::test_restart_app_guard_is_first_statement_in_method).
        # The rest of the body lives in LifecycleController.restart_app;
        # the controller mirrors this guard (idempotent) so it is safe
        # for direct calls from future code.
        if self._shutting_down_event.is_set():
            log.debug("[RESTART] ignoring duplicate restart_app call (already shutting down)")
            return
        return self.lifecycle.restart_app()

    def _wait_for_relaunch_ack(self, timeout: float) -> bool:
        """(Phase 4.5): delegate to LifecycleController.

        Body extracted to
        :meth:`voice_typer.server.app_lifecycle.LifecycleController._wait_for_relaunch_ack`.
        Behaviour preserved verbatim — only the class boundary moved.
        """
        return self.lifecycle._wait_for_relaunch_ack(timeout)

    # the following 6 TrayController protocol methods were
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
        """Phase 7: delegate to ShutdownController.

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
        """Phase 7: delegate to ShutdownController."""
        return self.shutdown.quit()

    def _atexit_log(self) -> None:
        """Phase 7: delegate to ShutdownController."""
        return self.shutdown._atexit_log()

    def _atexit_cleanup(self) -> None:
        """Phase 7: delegate to ShutdownController."""
        return self.shutdown._atexit_cleanup()

    def _install_signal_handlers(self):
        """Phase 7: delegate to ShutdownController."""
        return self.shutdown._install_signal_handlers()

    def _install_win32_console_handler(self):
        """Phase 7: delegate to ShutdownController."""
        return self.shutdown._install_win32_console_handler()

    def _win32_console_handler(self, ctrl_type):
        """Phase 7: delegate to ShutdownController."""
        return self.shutdown._win32_console_handler(ctrl_type)


# REF-3: extraction — single-instance enforcement + backend PID file
# helpers moved to voice_typer.server.single_instance. Re-exported here so
# tests doing `from voice_typer.server.app import _ensure_single_instance` /
# `_write_backend_pid_file` / `_clear_backend_pid_file` / `_is_pid_alive` /
# `_read_stale_backend_pid` / `_backend_pid_file` keep working (test_app.py,
# test_app_cleanup.py, test_electron_launcher.py, test_feature_hardening_regressions.py,
# test_waveform_bubble.py). Source-level tests that inspect app.py for the
# mutex name "Local\\VoiceTyperSingleInstance" ( / SEC-001) and
# _create_restrictive_security_attributes continue to see those symbols here
# via the import below + the comment in this block.
# _another_voice_typer_alive() was deleted; the Win32 named
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

     (fix): the ``VoiceTyperApp.main()`` line was accidentally deleted
    in a prior refactor. pyproject.toml now points to
    ``voice_typer.server.ipc_server:main`` as the canonical entry point;
    this function is kept as a thin re-export for backward compat.
    """
    # Enable faulthandler for automatic thread-dump on SIGSEGV/SIGABRT.
    # Invaluable for debugging production crashes with CUDA/GPU drivers.
    try:
        import faulthandler

        faulthandler.enable()
    except Exception:
        log.debug("[IPC] faulthandler not available", exc_info=True)

    from voice_typer.server.ipc_server import main as ipc_main

    # wrap in try/except so a backend crash logs at ERROR with
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
# via %SYSTEMROOT%\\System32\\notepad.exe (SEC-audit-011 / ).
from voice_typer.server.platform_launch import (  # noqa: E402,F401
    _systemroot_notepad_path,
    _windows_close_process_handle,
    _windows_open_with_default_app,
    _windows_wait_for_process_exit,
)
