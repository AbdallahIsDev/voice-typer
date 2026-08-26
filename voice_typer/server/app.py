"""Main application orchestrator."""

from __future__ import annotations

import atexit
import logging
import logging.handlers
import os  # noqa: F401 (stdlib re-export for test monkeypatch)
import sys
import threading
import time  # noqa: F401 (stdlib re-export for test monkeypatch)
from typing import TYPE_CHECKING, Any

# CRASH-HANDLER: Windows VEH + Python excepthook for silent crash diagnostics
from voice_typer.server import crash_handler as _crash_handler, i18n

# Re-exported for monkeypatch.setattr("voice_typer.server.app.X", ...) in tests
# and for runtime lookups from voice_typer.server.startup_tasks.  # ruff: noqa: F401
# numpy was eagerly imported at module top but never used directly in
# this module (~250-335ms of cold-start C-extension init per import);
# the ``np = lazy_module("numpy")`` binding has since been removed — no
# caller ever touched ``np`` from this module and no test patches it.
# ``from __future__ import annotations`` above is REQUIRED so any future
# ``np.ndarray`` annotation in this file stays as an unevaluated string
# (PEP 563) and does NOT trigger a numpy import at module load.
from voice_typer.server._busyness import BusynessCoordinator
from voice_typer.server._microphone_registry import MicrophoneRegistry

# Win32 SECURITY_ATTRIBUTES builder extracted to a focused,
# security-reviewable module.  Re-exported here so existing callers
# (and tests that grep app.py source for the symbol name) keep working.
from voice_typer.server._security_attributes import (  # noqa: F401
    _create_restrictive_security_attributes,
)

# ``AudioProcessor`` / ``DuckCrashRecovery`` / ``VolumeDucker`` /
# ``WaveformBubble`` were eagerly imported at module top but are only
# used inside lazy @property getters (or the ``_LazyAudioProcessorProxy``)
# — the getters now live in ``app_lazy_hub`` and import the heavy
# classes lazily inside their bodies, deferring the transitive import
# cost (audio_filters -> scipy.signal.butter; volume_ducker -> pyobjc /
# ctypes on macOS; waveform -> numpy) to first attribute access — paid
# only when the user actually dictates / ducks volume / sees the bubble,
# not on every cold start. ``AudioQualityAnalyzer`` / ``CrashRecovery``
# stay at module top because they're either cheap to import or eagerly
# constructed by the ``_init_*`` builders. ``HistoryDB`` is NOT used by
# app.py code any more (the lazy ``history_db`` property moved to
# ``app_lazy_hub``) but MUST stay a module-top binding: the hub's getter
# resolves it through THIS module at call time so the documented
# monkeypatch seam (``voice_typer.server.app.HistoryDB``) keeps
# intercepting construction. ``ClipboardManager`` is NOT imported at
# module top: the clipboard package eagerly imports ``pyperclip`` + the
# platform backends (``.windows`` / ``.linux``, which pull in pywin32 /
# pynput) and its ``manager`` submodule imports ``config`` at module
# top — several ms of the cold-start import chain paid even though the
# clipboard is only touched at dictation-stop paste time. The class is
# imported lazily inside the ``clipboard`` @property getter in
# ``app_lazy_hub``.
# The ``_config_dir`` binding is kept (not just the helper): single_instance
# resolves ``_app_module._config_dir`` at call time, and the
# tmp_config_dir fixture belt-and-suspenders-patches this attribute.
from voice_typer.server.audio_quality import AudioQualityAnalyzer
from voice_typer.server.branding import APP_NAME
from voice_typer.server.config import Config, _config_dir  # noqa: F401
from voice_typer.server.crash_recovery import CrashRecovery
from voice_typer.server.history_db import HistoryDB

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
# ``create_hotkey_backend`` re-export removed: the last app-namespace
# patch site (tests/test_platform_fix_regressions.py) now targets the
# canonical ``voice_typer.server.hotkey_dispatcher.create_hotkey_backend``,
# matching where HotkeyDispatcher actually resolves the factory. This
# documents the broader pattern of test-seam re-exports being
# progressively removed (TranscriptionEngine was the first).
# The ``voice_typer.server.platform_utils`` platform-flag re-export
# (``is_windows``/``is_macos``/``is_linux``) was removed: consumers import
# the flags at call time from their canonical home and every test patch
# site now targets ``voice_typer.server.platform_utils.{is_windows,
# is_macos, is_linux}`` directly.
# ``Recorder`` is imported lazily inside the recording-init builder
# (immediately before the background ``Recorder(...)`` construction) to
# match the deferred-import pattern already used for
# ``RecordingController``, ``ModelManager``, and the other heavy classes
# below. Importing it at module top would trigger
# ``voice_typer/server/recording/__init__.py`` which eagerly loads 7+
# submodules that each do ``import numpy as np`` at module top, adding
# ~250–335 ms to every cold start. The proxy ``np =
# lazy_module("numpy")`` bound on the recording package is defeated if
# any submodule does a direct top-level ``import numpy``. Deferring the
# ``Recorder`` import until app construction keeps the recording package
# out of the module-import critical path entirely.
# autostart + microphone helpers are imported directly from their canonical
# home ``voice_typer.server.server_platform`` by their consumers
# (``settings_controller``, ``startup_tasks``) — no app-module re-export.
# The former test-seam re-export was removed after every patch site was
# migrated to the owning submodules
# (``server_platform.autostart.{is_autostart_enabled, enable_autostart,
# disable_autostart}``, ``server_platform.microphone_list.list_microphones``).
# ``StreamingTranscriptionSession`` / ``clean_transcribed_text`` /
# ``configure_corrections`` re-exports removed: their last app-namespace
# patch sites were migrated to the canonical modules (dictation ->
# ``streaming_session_coordinator``, cleanup -> ``text_cleanup``;
# corrections wiring is asserted via ``startup_sequence``).
from voice_typer.server.thread_registry import ThreadRegistry
from voice_typer.server.tray import AppState, TrayIcon

if TYPE_CHECKING:
    # imported only for type annotations on ``_template_manager``
    # and ``_vocabulary_manager`` (declared Optional so the eager-init
    # ``= None`` fallback in the misc-backings builder type-checks).  The
    # runtime imports remain inside the property getters in
    # ``app_lazy_hub`` so a missing optional dependency does not break
    # VoiceTyperApp construction.
    from voice_typer.server.templates import TemplateManager
    from voice_typer.server.vocabulary import VocabularyManager

# extraction — the lazy-@property hub (recorder/recording/undo/
# audio_quality/_duck_crash_recovery/_volume_ducker/history_db/
# clipboard/waveform/template/vocabulary accessors, the legacy
# _busy_event/_lock/_microphones delegates, the lazy-failure sentinels
# and the ``_LazyAudioProcessorProxy``) moved to
# voice_typer.server.app_lazy_hub. Re-exported here so every existing
# import — ``from voice_typer.server.app import _LAZY_FAILED`` /
# ``RETRY_TTL_SECONDS`` / ``_RECORDER_MISSING`` /
# ``_LazyAudioProcessorProxy`` — and every identity check
# (``backing is _LAZY_FAILED``) keeps working unchanged (the re-export
# binds the SAME sentinel objects, so ``is`` comparisons hold).
# extraction — mic/model/restart/settings management methods (Tray
# protocol surface, quit/restart entry points, autostart/notification
# side-effect delegates, config-editor launch) moved to
# voice_typer.server.app_admin.
from voice_typer.server.app_admin import AppAdmin

# extraction — dictation-control methods (toggle/start/stop/cancel,
# undo/repaste delegates, audio-quality chunk delegation + warning
# latch, volume duck/restore delegates) moved to
# voice_typer.server.app_dictation.
from voice_typer.server.app_dictation import AppDictation
from voice_typer.server.app_lazy_hub import (  # noqa: F401
    _LAZY_FAILED,
    _RECORDER_MISSING,
    RETRY_TTL_SECONDS,
    AppLazyHub,
    _LazyAudioProcessorProxy,
)

log = logging.getLogger(__name__)


def _resolve_config_dir():
    """Call-time indirection so patches on config._config_dir propagate."""
    from voice_typer.server import config as _config_module

    return _config_module._config_dir()


# extraction — _setup_logging moved to voice_typer.server.logging_setup.
# Re-exported here so callers (voice_typer.server.ipc_server.main,
# voice_typer.server.prewarm.run) and tests that monkeypatch
# voice_typer.server.app._setup_logging keep working unchanged.
# _setup_logging calls warn_if_in_container() (from
# voice_typer.server.container_detect) at startup to detect container
# environments and warn about unavailable features. The call lives in
# logging_setup.py now but the source-string assertion in
# tests/regressions/test_platform_misc.py::test_container_detect_called_in_startup
# greps app.py source for the symbol name — kept here as a comment.  # ruff: noqa: F401
# extraction — _validate_env_vars moved to voice_typer.server.env_validation;
# the app re-export was removed once the last test importers migrated
# (it validates SystemRoot to reject attacker-controlled values that
# could enable DLL injection — canonical home is env_validation).
from voice_typer.server.logging_setup import _emit_startup_banner, _setup_logging  # noqa: F401, E402

# Register English fallbacks for the new i18n keys consumed by
# this module (``error.config_load_failed.title`` /
# ``error.config_load_failed.body`` and ``state.app.starting``). The canonical home for English fallbacks is
# ``voice_typer/server/i18n.py::_INITIAL_LABELS`` (which already holds
# every other ``notify.app.*`` / ``state.*`` key used elsewhere in the
# server), but that module is owned by another lane — so we extend the
# existing English registry in place rather than replacing it via
# ``i18n.register_locale`` (which REPLACES the locale's label dict,
# wiping all other English keys). ``setdefault`` makes this idempotent:
# if a future i18n.py change adds the same key to ``_INITIAL_LABELS``,
# that value wins (this extension becomes a no-op). Non-English locales
# are populated via the ``set_tray_locale`` IPC (pushed by the renderer
# on locale change) and via the JSON locale files at
# ``voice_typer/client/src/main/i18n/locales/*.json`` (consumed by the
# TS main process's ``mainT()``). Per the i18n completeness rule, the
# keys MUST exist in every locale file so the missing-key tooling
# doesn't silently fall back to English.
with i18n._LOCK:
    _en_labels = i18n._REGISTRY.setdefault("en", {})
    # the tray notification for a config-load failure routes
    # through these two keys (resolved by the regression guard in
    # ``tests/app/test_lifecycle.py``). The title is a
    # non-brand literal so the failure is surfaced
    # in the notification even when ``APP_NAME`` is customized.
    _en_labels.setdefault("error.config_load_failed.title", "Config load failed")
    _en_labels.setdefault(
        "error.config_load_failed.body",
        "Settings were reset to defaults. Check the logs for details.",
    )
    _en_labels.setdefault("state.app.starting", "Starting...")


class VoiceTyperApp(AppLazyHub, AppDictation, AppAdmin):
    """The main application.

    Construction is split into focused ``_init_*`` builders (one
    subsystem slice each); ``__init__`` is a short call sequence that
    runs them in the exact historical order — construction order is
    behavior (the thread registry must exist before any spawned
    thread, ``self.config`` before the tray, the mutation lock before
    it is shared with Config, etc.). The lazy subsystem accessors live
    on the ``AppLazyHub`` mixin, the dictation-control surface on
    ``AppDictation``, and the mic/model/restart/settings management
    surface on ``AppAdmin``.
    """

    # Declared as a class attribute (not only dynamically injected by
    # ``DictationPipeline._maybe_init_vocabulary_automation``) so
    # ``build_ipc_server``'s runtime ``isinstance(app, AppProtocol)``
    # check passes at startup — the protocol declares
    # ``_vocabulary_automation: Any`` and the attribute must exist from
    # construction time (see ``providers.py``). The pipeline overwrites
    # it with the real controller when dictation initialises.
    _vocabulary_automation: Any = None

    def __init__(self):
        """Run the subsystem builders in the historical order."""
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

    # ─── Construction: config ─────────────────────────────────────────

    def _init_config(self) -> None:
        """Load ``Config`` with corrupt-file self-heal.

        Must run FIRST — every later builder reads ``self.config``.
        """
        # catch unexpected exceptions from Config.load() (e.g.
        # KeyError from a data[...] access without a default, or
        # AttributeError from a None dereference during schema
        # migration).  Log at ERROR with exc_info=True, fall back to
        # Config() defaults, and flag the failure so a tray notification
        # can be surfaced once the tray is built later in init.
        try:
            self.config = Config.load()
        except Exception:
            log.error("[INIT] Config.load() raised", exc_info=True)
            # Self-heal: rename the existing config file to
            # ``config.json.corrupt-<timestamp>.bak`` so the next restart
            # loads fresh defaults instead of re-failing on the same
            # corrupt file. We use the canonical ``_config_dir``
            # accessor (via ``_resolve_config_dir()``) rather than
            # ``voice_typer.server._paths.config_dir`` so the
            # ``tmp_config_dir`` test fixture (which patches
            # ``voice_typer.server.config._config_dir``) takes
            # effect: using ``_paths.config_dir()`` here would resolve
            # the REAL user config dir during tests and rename the
            # user's actual config. Best-effort: if the rename itself
            # fails (permissions, readonly mount), we still fall back to
            # ``Config()`` so init can proceed; the next restart will
            # re-attempt Config.load() against the same corrupt file
            # and re-trigger this path.
            try:
                _config_path = _resolve_config_dir() / "config.json"
                if _config_path.exists():
                    import time as _time

                    _corrupt_path = _config_path.with_name(f"config.json.corrupt-{int(_time.time())}.bak")
                    _config_path.rename(_corrupt_path)
                    log.warning(
                        "[INIT] renamed corrupt config to %s",
                        _config_path.name,
                    )
            except Exception:
                log.warning(
                    "[INIT] could not rename corrupt config",
                    exc_info=True,
                )
            self.config = Config()
            self._config_load_failed = True
        else:
            self._config_load_failed = False

    # ─── Construction: threading + crash handlers ─────────────────────

    def _init_threading_and_crash(self) -> None:
        """Create the ThreadRegistry and install both excepthooks."""
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
        #  wrapped in try/except so an excepthook-install
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

    # ─── Construction: startup banner ─────────────────────────────────

    def _log_startup_banner(self) -> None:
        """Emit the first visible startup log lines + launch timeline."""
        # Startup banner -- first visible log, before any subsystem init.
        # The model field reports INSTALLED state, not just the config
        # value: a model_size left over in config after its weights were
        # deleted must not be advertised as active (the root cause of the
        # misleading ``model=<stale>`` banner). ``is_active_model_downloaded``
        # is a TTL-cached single-stat probe; on probe failure we fall back
        # to the configured value so the banner is never blocked.
        try:
            from voice_typer.server.tray_models import is_active_model_downloaded

            _model_installed = is_active_model_downloaded(self.config)
        except Exception:
            _model_installed = True
        from voice_typer.server.model_registry import NO_MODEL_SIZE

        _model_desc = str(self.config.model_size)
        if _model_desc == NO_MODEL_SIZE:
            # Genuine "no model selected" — report it honestly instead
            # of ``model= (not installed)`` (the empty selection has no
            # name to suffix).
            _model_desc = "none"
        elif not _model_installed:
            _model_desc = f"{_model_desc} (not installed)"
        log.info(
            "%s starting -- model=%s, hotkey=%s, mic=%s, sample_rate=%s",
            APP_NAME,
            _model_desc,
            self.config.hotkey,
            self.config.microphone or "default",
            self.config.sample_rate,
        )
        # One-line attribution of the spawn→first-log gap (Electron
        # boot vs backend interpreter + imports). No-op when the
        # backend wasn't spawned by Electron (standalone / Tauri-WS).
        from voice_typer.server.startup_timeline import log_launch_timeline

        log_launch_timeline(log)

        # Emit the ``[STARTUP] logging initialized`` banner + install the
        # Windows VEH crash handler AFTER the ``APP starting`` line so the
        # startup log reads: ``APP starting`` → ``[STARTUP] logging
        # initialized`` → ``[CRASH] Windows VEH installed``.
        _emit_startup_banner()

    # ─── Construction: audio ──────────────────────────────────────────

    def _init_audio(self) -> None:
        """Declare the lazy audio-processor backing + quality analyzer."""
        # Audio processor wraps a FilterChain built from config.
        # Rebuilt on every config change via _rebuild_audio_processor()
        # so Settings UI changes take effect immediately in dictation.
        #
        # ``AudioProcessor`` construction is deferred to
        # first attribute access via the ``_audio_processor`` @property
        # (AppLazyHub). The eager construction that used to live here
        # pulled in the full ``audio_filters`` package +
        # ``scipy.signal.butter`` (via ``build_chain``) on every cold
        # start, even when the user never dictates. The lazy property
        # returns a ``_LazyAudioProcessorProxy`` that transparently
        # constructs the real ``AudioProcessor`` on first ``process_chunk``
        # / ``set_sample_rate`` / ``rebuild_from_config`` call (i.e. on
        # the first recording or the first config-driven rebuild). The
        # proxy also wires ``set_quality_callback`` after construction
        # so the per-chunk quality callback is hooked up before the
        # first chunk is processed.
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
        # ``set_quality_callback`` wiring lives in the
        # ``_LazyAudioProcessorProxy._resolve`` method so it fires on
        # first attribute access (after the real AudioProcessor is
        # constructed). Calling it here would trigger the proxy to
        # resolve immediately, defeating the lazy construction.

    # ─── Construction: recorder / recording subsystem ─────────────────

    def _init_recording(self) -> None:
        """Spawn the background recorder build + VAD preload.

        ``Recorder`` + ``RecordingController`` construction is deferred
        to a background thread. The
        ``voice_typer.server.recording`` import + ``Recorder()`` build
        eagerly loads numpy/scipy/sounddevice (PortAudio) and can take
        1-8s on the main thread — measured ~5x slower under the system
        Python (the interpreter the packaged app runs on) than under
        the dev venv, and worse on cold cache. The tray and IPC server
        don't need the recorder, so blocking startup on it made the
        app look dead for seconds. The background build is registered
        with the ThreadRegistry (shutdown joins it) and ``app.recorder``
        / ``app.recording`` are lazy properties that block only briefly
        on first access if the build is still in flight — every existing
        call site keeps working unchanged.
        """
        self._recorder_backing: Any = _RECORDER_MISSING
        self._recording_backing: Any = _RECORDER_MISSING
        self._recorder_build_error: BaseException | None = None
        self._recorder_build_ready = threading.Event()

        def _build_recorder_subsystem() -> None:
            try:
                # Setter guard: a test (or a later caller) may have
                # injected ``app.recorder = MagicMock()`` via the setter
                # while this background build was in flight — never
                # clobber a caller-provided value with the real recorder.
                if self._recorder_backing is not _RECORDER_MISSING:
                    return
                from voice_typer.server.recording import Recorder

                recorder = Recorder(
                    self.config,
                    audio_processor=self._audio_processor,
                    thread_registry=self._thread_registry,
                )
                if self._recorder_backing is not _RECORDER_MISSING:
                    return  # setter raced us between import + construction
                self._recorder_backing = recorder
                # Recording lifecycle extracted to RecordingController.
                # Owns toggle/start/stop/cancel, silence/xrun callbacks,
                # and the streaming session.
                from voice_typer.server.recording_controller import RecordingController

                controller: Any = RecordingController(self)
                if self._recorder_backing is not recorder:
                    return  # setter raced us during controller construction
                self._recording_backing = controller
                # wire xrun threshold callback for tray
                # notification (was ``self.recorder.on_xrun_threshold =
                # self.recording.on_xrun_threshold`` on the main thread).
                recorder.on_xrun_threshold = controller.on_xrun_threshold
            except Exception as exc:  # noqa: BLE001 — surfaced on first access
                self._recorder_build_error = exc
                log.warning(
                    "[INIT] background recorder construction failed (%s)",
                    type(exc).__name__,
                    exc_info=True,
                )
            finally:
                self._recorder_build_ready.set()

        # Eagerly resolve numpy (and the recording package) on the MAIN
        # thread BEFORE spawning the recorder-init thread below.  The
        # recorder uses `lazy_module("numpy")` (see `_lazy_import.py`),
        # so its first numpy access triggers `importlib.import_module`
        # on the background thread.  If the main thread is concurrently
        # importing numpy (e.g. the audio-filter chain / scipy path),
        # Python 3.13+ raises `_DeadlockError: deadlock detected by
        # _ModuleLock('numpy._core._multiarray_umath')` — the classic
        # import-lock contention between two threads.  Pre-importing
        # numpy here (single-threaded, before any background thread
        # exists) makes the recorder-init thread's lazy resolve a
        # sys.modules cache hit with zero lock contention.
        try:
            import numpy  # noqa: F401

            from voice_typer.server.recording import Recorder as _RecorderType  # noqa: F401
        except Exception as exc:  # noqa: BLE001 — recorder still retries in its thread
            log.warning(
                "[INIT] eager numpy/recorder pre-import failed (%s) — recorder-init thread will retry on demand",
                type(exc).__name__,
            )

        self._thread_registry.spawn_and_register(
            "recorder-init",
            _build_recorder_subsystem,
            daemon=True,
            join_timeout=10.0,
        )
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

    # ─── Construction: model manager ──────────────────────────────────

    def _init_models(self) -> None:
        """Construct the ModelManager (ASR backend lifecycle owner)."""
        # ASR backend lifecycle extracted to ModelManager.
        # Previously VoiceTyperApp owned the AsrBackendRegistry + three
        # engine fields + ~500 LOC of load/fallback/change logic. Now
        # ModelManager owns all of that; app.py accesses it via
        # `self.models`. (the @property delegates that
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

    # ─── Construction: tray ───────────────────────────────────────────

    def _init_tray(self) -> None:
        """Construct TrayIcon + surface the config-load-failure toast."""
        # ``ClipboardManager`` construction deferred to first
        # access via the ``clipboard`` @property (AppLazyHub). The eager
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
        # The title and body are BOTH localized via ``i18n.t``.
        # The English fallbacks for
        # ``error.config_load_failed.title`` /
        # ``error.config_load_failed.body`` are registered at the
        # top of this module (extends ``i18n._REGISTRY["en"]`` since
        # ``i18n.py::_INITIAL_LABELS`` is owned by another lane).
        if self._config_load_failed:
            try:
                self.tray.notify(
                    i18n.t("error.config_load_failed.title"),
                    i18n.t("error.config_load_failed.body"),
                )
            except Exception:
                log.debug("[INIT] tray.notify for config load failure failed", exc_info=True)

    # ─── Construction: controllers + lazy backings ────────────────────

    def _init_controllers(self) -> None:
        """Construct settings/shutdown/lifecycle/config-editor controllers."""
        # Settings side-effects (autostart, notifications,
        # microphone selection) extracted to SettingsController. The app
        # keeps thin delegate methods (``_toggle_autostart``,
        # ``_set_autostart``, ``_set_notifications``, ``_select_microphone``)
        # so tray menu callbacks and tests calling ``app._select_microphone``
        # keep working unchanged. ``_open_config_file`` is a thin
        # delegate too (the launcher holds the real body).
        from voice_typer.server.settings_controller import SettingsController

        self.settings: SettingsController = SettingsController(self)

        # Shutdown / cleanup lifecycle (quit, _do_cleanup,
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

        # Restart / quit relaunch-ack lifecycle extracted to
        # LifecycleController. The app keeps thin delegate methods
        # (``restart_app``, ``_wait_for_relaunch_ack``, ``quit_app``) so
        # tray menu callbacks (quit_app -> self.quit(), restart_app ->
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
        # access via the ``undo`` @property (AppLazyHub). The eager
        # construction that used to live here paid the ``app_undo``
        # import + class init on every cold start, even when the user
        # never invokes undo / repaste (the only entry points are the
        # tray menu items and the repaste hotkey). The lazy property
        # transparently constructs on first access.
        self._undo_backing: Any = None
        # Monotonic-clock timestamp of the most recent lazy-init failure
        # for the ``undo`` property (see the ``_LAZY_FAILED`` sentinel in
        # ``app_lazy_hub``). When ``_undo_backing`` is ``_LAZY_FAILED``,
        # the getter reads this to decide whether to retry construction
        # (after ``RETRY_TTL_SECONDS``) or return ``None`` silently
        # (within TTL — avoids 94 Hz log spam on the hot path). ``None``
        # means "no failure recorded" (either never failed, or the last
        # attempt succeeded and cleared the timestamp).
        self._undo_failed_at: Any = None

        # Audio-quality side-effects extracted to
        # AudioQualityController. The app keeps thin delegate methods so
        # ``self._audio_processor.set_quality_callback(self._on_audio_quality_chunk)``,
        # ``service.apply_config_side_effects`` (-> _rebuild_audio_processor),
        # and ``RecordingController.stop`` (-> _finalize_audio_quality_report)
        # all keep working unchanged.
        #
        # Construction of ``AudioQualityController`` is deferred to first
        # access via the ``audio_quality`` @property (AppLazyHub). The
        # eager construction that used to live here paid the
        # ``audio_quality_controller`` import (which eagerly imports
        # numpy) on every cold start. The lazy property transparently
        # constructs on first access; the per-chunk quality callback
        # wired through the proxy delegates through the property
        # so the first chunk triggers construction.
        self._audio_quality_backing: Any = None
        # Monotonic-clock timestamp of the most recent lazy-init failure
        # for the ``audio_quality`` property — see ``_undo_failed_at``
        # above for the full rationale. The ``audio_quality`` property is
        # on the per-chunk audio callback hot path (~94 Hz at 48 kHz/512),
        # so this sentinel is the critical fix for the 94 Hz log spam.
        self._audio_quality_failed_at: Any = None

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

    # ─── Construction: hotkeys + busyness + mutation lock ─────────────

    def _init_hotkeys_and_locks(self) -> None:
        """Construct HotkeyDispatcher, busyness/mic coordinators, and
        the config-mutation lock (wired into Config)."""
        # Hotkey registration extracted to HotkeyDispatcher.
        # Owns the 3 hotkey backends (dictation / ESC / repaste) and the
        # register/restart logic. (the @property
        # delegates that used to mirror the 3 legacy fields
        # (_hotkey_backend, _esc_backend, _repaste_backend) on
        # VoiceTyperApp have been removed — callers now use
        # `self.hotkeys.<field>` directly.)
        from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher

        self.hotkeys: HotkeyDispatcher = HotkeyDispatcher(self)
        # The dead inline ``from voice_typer.server.hotkeys
        # import create_hotkey_backend`` that lived here has been
        # removed. It was a no-op — the symbol lived in
        # ``hotkeys``/``hotkey_dispatcher``, and the inline import only
        # bound a *local* variable that was never read.
        # _streaming_session and _transcription_thread now
        # live in RecordingController. (the @property
        # delegates that used to mirror them on VoiceTyperApp have been
        # removed — callers now use `self.recording.<field>` directly,
        # or `self.recording.get_streaming_session()` /
        # `self.recording.set_streaming_session(...)`.)
        # Pipeline "busy" flag + companion lock live in
        # BusynessCoordinator; the cached microphone list lives in
        # MicrophoneRegistry. The legacy private attributes
        # (``_busy_event`` / ``_lock`` / ``_microphones``) remain
        # reachable via the back-compat properties on ``AppLazyHub`` —
        # they delegate to these coordinators, so every existing
        # consumer (``recording_lifecycle``,
        # ``transcription_watchdog``, ``dictation_pipeline/*``,
        # ``model_manager``, ``startup_tasks.load_microphones``,
        # ``service/microphone_test``, ``tray_menu``) keeps working
        # unchanged. NOTE: the legacy ``_busy_event`` semantics were
        # INVERTED (SET == not busy — the event doubles as a ready
        # signal); that inversion is now internal to the coordinator,
        # which also exposes intent-revealing methods (``is_busy`` /
        # ``set_busy`` / ``set_idle`` / ``wait_idle``).
        self._busyness = BusynessCoordinator()
        self._microphone_registry = MicrophoneRegistry()
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
        # ``set_mutation_lock`` was defined on Config but never called
        # in production, so only the IPC ``set_config`` path that
        # manually acquired the lock was serialized — every other
        # ``save()`` ran unlocked, allowing a background mic-fallback
        # save to interleave with an in-flight ``apply_config`` and
        # persist a torn snapshot. The lock is an ``RLock`` so nested
        # acquisition from the same thread (e.g. ``apply_config`` calls
        # ``save()`` which itself re-enters) is safe. MUST run AFTER
        # both ``self.config`` (set by the config builder) and
        # ``self._config_mutation_lock`` (set just above) exist — order
        # matters, the lock has to be created before it is shared.
        self.config.set_mutation_lock(self._config_mutation_lock)

    # ─── Construction: state flags + timers ───────────────────────────

    def _init_state_flags(self) -> None:
        """Declare shutdown/electron/restart/esc flags + timer wiring."""
        # _model_load_attempted / _model_load_thread /
        # _pending_dictation now live in ModelManager. (
        # the @property delegates that used to mirror them on
        # VoiceTyperApp have been removed — callers now use
        # `self.models.<field>` directly.)
        self._shutting_down = False  # True once quit() starts
        # assert the shutdown gate is a real bool. The
        # ``getattr(self.app, "_shutting_down", False) is True`` idiom
        # used by the IPC dispatch path (see voice_typer/server/
        # ipc_server.py and voice_typer/server/sidecar_ws.py)
        # accommodates test MagicMock auto-vivification - a test
        # that does ``mock_app._shutting_down = 1`` would otherwise
        # bypass the shutdown gate (a truthy int IS truthy but is NOT
        # ``True``). Catching the wrong-type assignment at __init__
        # time gives a clear, immediate failure ("TypeError:
        # _shutting_down must be bool, got int") instead of a silent
        # shutdown-bypass bug that surfaces only when a test exercises
        # the gate.
        if not isinstance(self._shutting_down, bool):
            raise TypeError(f"VoiceTyperApp._shutting_down must be bool, got {type(self._shutting_down).__name__}")
        # threading.Event version of _shutting_down so executor
        # tasks can check it without reading the boolean (which provides
        # no memory-order guarantee across threads).
        self._shutting_down_event = threading.Event()
        # counter incremented by startup_sequence.py when the
        # onboarding check persistently fails (see
        # startup_sequence.py). Declared here so pyrefly
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
        # PID of the Electron subprocess we launched in standalone
        # mode (None when Electron spawned us, or when standalone launch
        # failed).  Tracked here so quit() can terminate the subprocess
        # explicitly during shutdown.
        self._electron_pid: int | None = None
        # True when ``restart_app()`` runs in standalone
        # mode and the process must stay alive to re-initialize the app in
        # the same terminal/console (instead of ``sys.exit(0)`` + Electron
        # respawning a hidden backend).  The entrypoint loop checks this
        # flag after ``app.start()`` returns to decide whether to re-run
        # the startup sequence.
        self._in_place_restart: bool = False
        # flag gating the global ESC cancel hotkey.  Set to
        # True by the ""set_esc_cancel_paused"" IPC handler when the
        # frontend HotkeyPicker enters capture mode, so the backend's
        # ESC polling callback doesn't fire while the user is assigning
        # a custom hotkey in the Settings UI.
        self._esc_cancel_paused: bool = False
        # Timer lifecycle extracted to TimerCoordinator.
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

    # ─── Construction: history / crash recovery / volume ──────────────

    def _init_history_crash_volume(self) -> None:
        """Declare history-db backings + crash recovery + volume wiring."""
        # ``HistoryDB()`` construction is deferred to first
        # access via the ``history_db`` @property (AppLazyHub). The eager
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
        # Monotonic-clock timestamp of the most recent lazy-init failure
        # for the ``history_db`` property — see ``_undo_failed_at`` in
        # the controllers builder for the full rationale.
        self._history_db_failed_at: Any = None
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
        # ``_volume_ducker`` @properties (AppLazyHub). The eager
        # construction that used to live here paid the
        # ``duck_crash_recovery`` + ``volume_ducker`` imports + class
        # init on every cold start, even when the user has
        # ``volume_duck_enabled=False`` and never triggers a duck. The
        # lazy properties transparently construct on first access (e.g.
        # when ``VolumeController._duck_volume`` runs at the start of
        # the first dictation).
        self._duck_crash_recovery_backing: Any = None
        # Monotonic-clock timestamp of the most recent lazy-init failure
        # for the ``_duck_crash_recovery`` property — see
        # ``_undo_failed_at`` in the controllers builder for the full
        # rationale.
        self._duck_crash_recovery_failed_at: Any = None
        # VolumeController owns duck/restore side effects.
        # Kept eager because it's just a back-reference holder
        # (``self._app = app``) and the ``_on_volume_crash_restore``
        # callback wired into ``VolumeDucker`` delegates to it —
        # constructing it lazily would save nothing (the class is
        # trivial) and would complicate the callback wiring.
        from voice_typer.server.volume_controller import VolumeController

        self.volume: VolumeController = VolumeController(self)
        self._volume_ducker_backing: Any = None
        # Monotonic-clock timestamp of the most recent lazy-init failure
        # for the ``_volume_ducker`` property — see ``_undo_failed_at``
        # in the controllers builder for the full rationale.
        self._volume_ducker_failed_at: Any = None

    # ─── Construction: misc lazy backings ─────────────────────────────

    def _init_misc_backings(self) -> None:
        """Declare the remaining lazy backings + IPC/polisher fields."""
        # NOTE: AudioQualityAnalyzer is instantiated earlier in
        # construction (next to AudioProcessor) and wired to the
        # processor's per-chunk quality callback. See self._audio_quality
        # / self._on_audio_quality_chunk / _finalize_audio_quality_report.
        # ``WaveformBubble`` and ``WaveformBubbleWiring``
        # construction deferred to first access via the
        # ``_waveform_bubble`` / ``waveform_wiring`` @properties
        # (AppLazyHub). The eager construction + immediate
        # ``_wire_waveform_bubble()`` call that used to live here paid
        # the full bubble-wiring cost (importing
        # ``waveform_bubble_wiring`` + starting the bubble-level-pusher
        # daemon thread + registering it with the thread registry) on
        # every cold start, even when the user has
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
        # ``_vocabulary_manager`` @properties (AppLazyHub — passive
        # backings; the callers in ``service/template.py`` /
        # ``service/vocabulary.py`` own construction). The eager
        # construction that used to live here read ``templates.json`` /
        # ``vocabulary.json`` off disk on every cold start (hundreds of
        # ms on a slow disk), even when the user never uses templates /
        # vocabulary.
        #
        # The properties do NOT auto-construct on first access
        # (failure is logged at WARNING with ``exc_info=True`` and the
        # backing is left ``None`` to retry on next access) — the
        # ``is None`` fallback paths in ``service/template.py`` and
        # ``dictation_pipeline.py`` therefore see a cached instance on
        # success, or ``None`` on failure — their fallback construction
        # still works unchanged.
        self._template_manager_backing: Any = None
        self._vocabulary_manager_backing: Any = None
        self._llm_polisher = None  # Created on first polish (needs consent check)
        self._cloud_engine = None  # Lazy-init if cloud backend selected

    # ─── Timer Tracking ───────────────────────────────────────────────
    #  Phase 7: logic moved to TimerCoordinator. VoiceTyperApp keeps
    # thin delegates so existing callers (and tests that monkeypatch
    # app._schedule_timer / app._cancel_pending_timers) keep working.

    def _schedule_timer(self, delay: float, func) -> threading.Thread:
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
        # Localized via ``i18n.t("state.app.starting")`` instead of the
        # hardcoded English literal ``"Starting..."``. The English
        # fallback is registered at the top of this module.
        self.tray.set_state(AppState.LOADING, i18n.t("state.app.starting"))

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

        # Enter pystray event loop -- MUST be on the main thread. The
        # run() entry logs ``[TRAY] Tray icon created; event loop running
        # (main thread)`` itself, so this line would duplicate it (two
        # near-identical lines ~1ms apart in every session log).
        self.tray.run()

    def _do_startup(self) -> None:
        """Background work: sync autostart, load mics, load model, register hotkey.

         Phase 5: the body of this method (~340 lines) was extracted
        into :class:`voice_typer.server.startup_sequence.StartupSequence`
        to reduce the god-class size of ``VoiceTyperApp``.  The phase
        ordering, shutdown gates, parallel executor semantics,
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

    # ─── Model / hotkey delegate notes ────────────────────────────────
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


# extraction — single-instance enforcement + backend PID file
# helpers moved to voice_typer.server.single_instance. Re-exported here so
# tests doing `from voice_typer.server.app import _ensure_single_instance` /
# `_write_backend_pid_file` / `_clear_backend_pid_file` / `_is_pid_alive` /
# `_read_stale_backend_pid` / `_backend_pid_file` keep working (test_app.py,
# test_app_cleanup.py, test_electron_launcher.py, test_feature_hardening_regressions.py,
# test_waveform_bubble.py). Source-level tests that inspect app.py for the
# mutex name "Local\\VoiceTyperSingleInstance" and
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
    this function is NOT a bare re-export — it (1) enables faulthandler
    for crash thread-dumps (SIGSEGV/SIGABRT) and (2) wraps the canonical
    ``ipc_server`` entry point in a top-level try/except that logs
    ``[FATAL] backend crashed`` at ERROR and exits with code 1, so a
    backend crash surfaces in the structured log instead of dying
    silently.
    """
    # Enable faulthandler for automatic thread-dump on SIGSEGV/SIGABRT.
    # Invaluable for debugging production crashes with CUDA/GPU drivers.
    # ``faulthandler.enable()`` failure is logged at WARNING (not DEBUG)
    # because crash thread-dumps are a critical production-debugging
    # capability — if faulthandler is unavailable (e.g. stripped from
    # a minimal Python build, or ``faulthandler.enable()`` raises on a
    # platform without SIGSEGV support), the operator MUST know that
    # crash dumps will not be generated, otherwise a subsequent
    # production crash yields no thread-state snapshot and the support
    # ticket goes round-trip with "no traceback available". DEBUG is
    # invisible in default log configs; WARNING lands in the rotating
    # log file.
    try:
        import faulthandler

        faulthandler.enable()
    except Exception:
        log.warning(
            "[IPC] faulthandler not available — crash thread-dumps will not be generated",
            exc_info=True,
        )

    from voice_typer.server.ipc_server import main as ipc_main

    # wrap in try/except so a backend crash logs at ERROR with
    # the full traceback and the process exits with code 1 (rather than
    # propagating to the console-script wrapper with no structured log).
    try:
        ipc_main()
    except Exception:
        log.exception("[FATAL] backend crashed")
        sys.exit(1)


# extraction — Windows editor-launch helpers moved to
# voice_typer.server.platform_launch. Re-exported here so callers
# (the app-admin config-editor delegate) and tests that monkeypatch
# voice_typer.server.app._windows_open_with_default_app /
# _windows_wait_for_process_exit / _windows_close_process_handle /
# _systemroot_notepad_path keep working unchanged (test_api_doc_accuracy.py,
# test_config_editor_lock.py). The bare PATH-resolved "notepad" pattern
# is intentionally NOT used — _systemroot_notepad_path validates the path
# via %SYSTEMROOT%\\System32\\notepad.exe.
from voice_typer.server.platform_launch import (  # noqa: E402,F401
    _systemroot_notepad_path,
    _windows_close_process_handle,
    _windows_open_with_default_app,
    _windows_wait_for_process_exit,
)
