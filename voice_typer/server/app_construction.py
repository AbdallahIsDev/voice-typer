"""AppConstruction — eager subsystem construction mixin extracted from
VoiceTyperApp.

Owns the eager subsystem-builder slice of ``VoiceTyperApp`` — every
``_init_*`` builder EXCEPT the two that are pinned to ``app.py`` source
by ``tests/test_lock_order_contract.py::TestLockInventory``
(``_init_hotkeys_and_locks`` owns the ``_config_mutation_lock`` RLock
declaration, ``_init_state_flags`` the ``_shutting_down_event`` Event
declaration) and the recording slice that lives in
``app_recording_init``:

    - ``_register_startup_i18n_fallbacks`` (module-level function) —
      English fallbacks for ``error.config_load_failed.*`` /
      ``state.app.starting``, registered at app-INIT time (called from
      ``VoiceTyperApp.__init__``) so importing the module stays
      side-effect-free. Re-exported from ``voice_typer.server.app`` so
      ``hasattr(app_module, "_register_startup_i18n_fallbacks")`` and
      the direct test calls keep working.
    - ``_init_config`` — ``Config.load()`` with the corrupt-file
      self-heal (rename to ``config.json.corrupt-<ts>.bak`` via the
      canonical ``_resolve_config_dir`` seam, fallback to ``Config()``
      defaults, ``_config_load_failed`` flag for the tray toast).
    - ``_init_threading_and_crash`` — ``ThreadRegistry`` + the two
      best-effort crash-handler excepthook installs.
    - ``_log_startup_banner`` — the first visible startup log line
      (model INSTALLED-state resolution, not just the config value) +
      the launch timeline + the ``[STARTUP] logging initialized``
      banner.
    - ``_init_audio`` — the lazy audio-processor backing declaration +
      the eager ``AudioQualityAnalyzer``.
    - ``_init_models`` — ``ModelManager`` construction.
    - ``_init_tray`` — ``TrayIcon`` construction + the
      config-load-failure toast.
    - ``_init_controllers`` — ``SettingsController`` /
      ``ShutdownController`` / ``LifecycleController`` /
      ``ConfigEditorLauncher`` wiring + the lazy-controller backing
      declarations.
    - ``_init_history_crash_volume`` — history-db backings +
      ``CrashRecovery`` + ``VolumeController`` wiring.
    - ``_init_misc_backings`` — the remaining lazy backings (waveform
      bubble / wiring, template / vocabulary managers, IPC server slot,
      polisher / cloud engine).

Previously all of this lived on ``VoiceTyperApp`` in ``app.py``. The
behaviour is preserved verbatim — only the class boundary moved.
``VoiceTyperApp(AppConstruction)`` inherits every method, so
instance-level monkeypatching and direct calls keep working unchanged,
and ``inspect.getsource`` keeps resolving through the MRO. ``__init__``
(the builder call sequence) and the construction ORDER stay in
``app.py`` — order is behavior.

A note on logging (mirrors the convention in ``app_admin.py`` /
``app_dictation.py`` / ``app_lazy_hub.py`` / ``app_lifecycle.py`` /
``app_recording_init.py``): this module uses
``logging.getLogger("voice_typer.server.app")`` rather than the
conventional ``__name__`` so caplog captures in tests (e.g. the
config-load-failure ``[INIT]`` lines and the startup banner) route to
the same logger as the original VoiceTyperApp methods.

A note on patch paths (C-ARCH-2): the module-top imports below
(``Config``, ``ThreadRegistry``, ``_crash_handler``, ``APP_NAME``,
``_emit_startup_banner``, ``AudioQualityAnalyzer``, ``TrayIcon``,
``CrashRecovery``, ``i18n``) have NO app-module patch seams — verified
by grepping the tests tree for ``setattr("voice_typer.server.app.X"``
and ``setattr(app_module, "X"``. The one name with a documented
app-module seam used here is ``_resolve_config_dir`` (resolved by
``app_lazy_hub`` the same way): ``_init_config`` resolves it through
the ``voice_typer.server.app`` module at CALL time via a deferred
import, so ``monkeypatch.setattr("voice_typer.server.app._resolve_config_dir", ...)``
keeps intercepting. ``TrayIcon.__init__`` class-attribute patches
propagate because both modules hold the same class object. No
package-level indirection, no custom module subclasses.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Type-only import (no runtime cycle): the construction mixin only
    # ever runs on ``VoiceTyperApp``, and the controllers below require
    # the full façade type. ``from __future__ import annotations`` keeps
    # the reference lazy at runtime.
    from voice_typer.server.app import VoiceTyperApp

from voice_typer.server import crash_handler as _crash_handler, i18n
from voice_typer.server.audio_quality import AudioQualityAnalyzer
from voice_typer.server.branding import APP_NAME
from voice_typer.server.config import Config
from voice_typer.server.crash_recovery import CrashRecovery
from voice_typer.server.logging_setup import _emit_startup_banner
from voice_typer.server.thread_registry import ThreadRegistry
from voice_typer.server.tray import TrayIcon

# Tests capture the config-load-failure / startup-banner log lines at
# this logger name — see module docstring.
log = logging.getLogger("voice_typer.server.app")


def _register_startup_i18n_fallbacks() -> None:
    """Register English fallbacks for the new i18n keys consumed by
    this module (``error.config_load_failed.title`` /
    ``error.config_load_failed.body`` and ``state.app.starting``).

    Called from ``VoiceTyperApp.__init__`` — i.e. at app-init time, not
    import time — so importing the module stays side-effect-free. Must
    run BEFORE ``_init_config``: the config-load-failure notification
    raised there resolves ``error.config_load_failed.*``.

    The canonical home for English fallbacks is
    ``voice_typer/server/i18n.py::_INITIAL_LABELS`` (which already holds
    every other ``notify.app.*`` / ``state.*`` key used elsewhere in the
    server), but that module is owned by another lane — so we extend the
    existing English registry in place rather than replacing it via
    ``i18n.register_locale`` (which REPLACES the locale's label dict,
    wiping all other English keys). ``setdefault`` makes this idempotent:
    if a future i18n.py change adds the same key to ``_INITIAL_LABELS``,
    that value wins (this extension becomes a no-op). Non-English locales
    are populated via the ``set_tray_locale`` IPC (pushed by the renderer
    on locale change) and via the JSON locale files at
    ``voice_typer/client/src/main/i18n/locales/*.json`` (consumed by the
    TS main process's ``mainT()``). Per the i18n completeness rule, the
    keys MUST exist in every locale file so the missing-key tooling
    doesn't silently fall back to English.
    """
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


class AppConstruction:
    """Eager subsystem construction mixin for ``VoiceTyperApp``.

    Declares NO ``__init__`` — the builder call ORDER stays in
    ``app.py`` (construction order is behavior); only the builder
    bodies live here.
    """

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
            # (extraction) ``_resolve_config_dir`` is resolved through
            # the ``voice_typer.server.app`` module at CALL time (the
            # same deferred-import pattern ``app_lazy_hub`` uses) so
            # the documented app-module seam keeps intercepting.
            from voice_typer.server.app import _resolve_config_dir

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

    def _init_tray(self: VoiceTyperApp) -> None:
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
        # ``error.config_load_failed.body`` are registered by
        # ``_register_startup_i18n_fallbacks()``, called at the top of
        # ``__init__`` (it extends ``i18n._REGISTRY["en"]`` in place
        # since ``i18n.py::_INITIAL_LABELS`` is owned by another lane).
        if self._config_load_failed:
            try:
                self.tray.notify(
                    i18n.t("error.config_load_failed.title"),
                    i18n.t("error.config_load_failed.body"),
                )
            except Exception:
                log.debug("[INIT] tray.notify for config load failure failed", exc_info=True)

    # ─── Construction: controllers + lazy backings ────────────────────

    def _init_controllers(self: VoiceTyperApp) -> None:
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
