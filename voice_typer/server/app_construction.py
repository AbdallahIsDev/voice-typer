"""AppConstruction, eager subsystem construction mixin extracted from
VoiceTyperApp.

Owns the eager subsystem-builder slice of ``VoiceTyperApp``, every
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
    - ``_init_config``: ``Config.load()`` with the corrupt-file
      self-heal (rename to ``config.json.corrupt-<ts>.bak`` via the
      canonical ``_resolve_config_dir`` seam, fallback to ``Config()``
      defaults, ``_config_load_failed`` flag for the tray toast).
    - ``_init_threading_and_crash``: ``ThreadRegistry`` + the two
      best-effort crash-handler excepthook installs.
    - ``_log_startup_banner``: the first visible startup log line
      (model INSTALLED-state resolution, not just the config value) +
      the launch timeline + the ``[STARTUP] logging initialized``
      banner.
    - ``_init_audio``: the lazy audio-processor backing declaration +
      the eager ``AudioQualityAnalyzer``.
    - ``_init_models``: ``ModelManager`` construction.
    - ``_init_tray``: ``TrayIcon`` construction + the
      config-load-failure toast.
    - ``_init_controllers``: ``SettingsController`` /
      ``ShutdownController`` / ``LifecycleController`` /
      ``ConfigEditorLauncher`` wiring + the lazy-controller backing
      declarations.
    - ``_init_history_crash_volume``: history-db backings +
      ``CrashRecovery`` + ``VolumeController`` wiring.
    - ``_init_misc_backings``: the remaining lazy backings (waveform
      bubble / wiring, template / vocabulary managers, IPC server slot,
      polisher / cloud engine).

Previously all of this lived on ``VoiceTyperApp`` in ``app.py``. The
behaviour is preserved verbatim, only the class boundary moved.
``VoiceTyperApp(AppConstruction)`` inherits every method, so
instance-level monkeypatching and direct calls keep working unchanged,
and ``inspect.getsource`` keeps resolving through the MRO. ``__init__``
(the builder call sequence) and the construction ORDER stay in
``app.py``: order is behavior.

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
``CrashRecovery``, ``i18n``) have NO app-module patch seams, verified
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
log = logging.getLogger("voice_typer.server.app")


def _register_startup_i18n_fallbacks() -> None:
    """Register English fallbacks for the new i18n keys consumed by
    this module (``error.config_load_failed.title`` /
    ``error.config_load_failed.body`` and ``state.app.starting``).

    Called from ``VoiceTyperApp.__init__``: i.e. at app-init time, not
    import time, so importing the module stays side-effect-free. Must
    run BEFORE ``_init_config``: the config-load-failure notification
    raised there resolves ``error.config_load_failed.*``.

    The canonical home for English fallbacks is
    ``voice_typer/server/i18n.py::_INITIAL_LABELS`` (which already holds
    every other ``notify.app.*`` / ``state.*`` key used elsewhere in the
    server), but that module is owned by another lane, so we extend the
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
        _en_labels.setdefault("error.config_load_failed.title", "Config load failed")
        _en_labels.setdefault(
            "error.config_load_failed.body",
            "Settings were reset to defaults. Check the logs for details.",
        )
        _en_labels.setdefault("state.app.starting", "Starting...")


class AppConstruction:
    """Eager subsystem construction mixin for ``VoiceTyperApp``.

    Declares NO ``__init__``: the builder call ORDER stays in
    ``app.py`` (construction order is behavior); only the builder
    bodies live here.
    """

    def _init_config(self) -> None:
        """Load ``Config`` with corrupt-file self-heal.

        Must run FIRST, every later builder reads ``self.config``.
        """
        # catch unexpected exceptions from Config.load() (e.g.
        try:
            self.config = Config.load()
        except Exception:
            log.error("[INIT] Config.load() raised", exc_info=True)
            # Self-heal: rename the existing config file to
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

    def _init_threading_and_crash(self) -> None:
        """Create the ThreadRegistry and install both excepthooks."""
        # THREAD-REGISTRY: create the central registry FIRST so all
        self._thread_registry = ThreadRegistry()

        # Wire subsystems that own daemon workers created lazily (not
        try:
            from voice_typer.server import level_monitor

            level_monitor.set_thread_registry(self._thread_registry)
        except Exception:
            log.debug("[INIT] level_monitor thread-registry wiring failed", exc_info=True)

        # Install Python-level excepthook for unhandled Python exceptions.
        try:
            _crash_handler.install_python_excepthook()
            # install the threading excepthook so unhandled
            _crash_handler.install_threading_excepthook()
        except Exception:
            log.debug("[INIT] excepthook install failed", exc_info=True)

    def _log_startup_banner(self) -> None:
        """Emit the first visible startup log lines + launch timeline."""
        # Startup banner -- first visible log, before any subsystem init.
        try:
            from voice_typer.server.tray_models import is_active_model_downloaded

            _model_installed = is_active_model_downloaded(self.config)
        except Exception:
            _model_installed = True
        from voice_typer.server.model_registry import NO_MODEL_SIZE

        _model_desc = str(self.config.model_size)
        if _model_desc == NO_MODEL_SIZE:
            # Genuine "no model selected": report it honestly instead
            _model_desc = "none"
        elif not _model_installed:
            _model_desc = f"{_model_desc} (not installed)"
        log.info(
            "%s starting -- model=%s | hotkey=%s | mic=%s | sample_rate=%s",
            APP_NAME,
            _model_desc,
            self.config.hotkey,
            self.config.microphone or "default",
            self.config.sample_rate,
        )
        # One-line attribution of the spawn→first-log gap (predecessor
        from voice_typer.server.startup_timeline import log_launch_timeline

        log_launch_timeline(log)

        # Emit the ``[STARTUP] logging initialized`` banner + install the
        _emit_startup_banner()

    def _init_audio(self) -> None:
        """Declare the lazy audio-processor backing + quality analyzer."""
        # Audio processor wraps a FilterChain built from config.
        self._audio_processor_backing: Any = None

        # AudioQualityAnalyzer: wired to the AudioProcessor's
        self._audio_quality = AudioQualityAnalyzer()
        self._audio_quality.reset()
        # ``set_quality_callback`` wiring lives in the

    def _init_models(self) -> None:
        """Construct the ModelManager (ASR backend lifecycle owner)."""
        # ASR backend lifecycle extracted to ModelManager.
        from voice_typer.server.model_manager import ModelManager

        self.models: ModelManager = ModelManager(self)
        # the eager ``self.models._ensure_engine("qwen")`` call

    def _init_tray(self: VoiceTyperApp) -> None:
        """Construct TrayIcon + surface the config-load-failure toast."""
        # ``ClipboardManager`` construction deferred to first
        self._clipboard_backing: Any = None

        self.tray = TrayIcon(
            controller=self,
            config=self.config,
        )

        # if Config.load() failed earlier, surface a tray
        if self._config_load_failed:
            try:
                self.tray.notify(
                    i18n.t("error.config_load_failed.title"),
                    i18n.t("error.config_load_failed.body"),
                )
            except Exception:
                log.debug("[INIT] tray.notify for config load failure failed", exc_info=True)

    def _init_controllers(self: VoiceTyperApp) -> None:
        """Construct settings/shutdown/lifecycle/config-editor controllers."""
        # Settings side-effects (autostart, notifications,
        from voice_typer.server.settings_controller import SettingsController

        self.settings: SettingsController = SettingsController(self)

        # Shutdown / cleanup lifecycle (quit, _do_cleanup,
        from voice_typer.server.shutdown_controller import (
            SHUTDOWN_WATCHDOG_TIMEOUT_S,
            ShutdownController,
        )

        self.shutdown: ShutdownController = ShutdownController(self)
        # stash the watchdog timeout on the instance so
        self._shutdown_watchdog_timeout_s: float = SHUTDOWN_WATCHDOG_TIMEOUT_S

        # Restart / quit relaunch-ack lifecycle extracted to
        from voice_typer.server.app_lifecycle import LifecycleController

        self.lifecycle: LifecycleController = LifecycleController(self)

        # undo / repaste side effects extracted to
        self._undo_backing: Any = None
        # Monotonic-clock timestamp of the most recent lazy-init failure
        self._undo_failed_at: Any = None

        # Audio-quality side-effects extracted to
        self._audio_quality_backing: Any = None
        # Monotonic-clock timestamp of the most recent lazy-init failure
        self._audio_quality_failed_at: Any = None

        # config-editor controller extracted to a focused
        from voice_typer.server.controllers.config_editor_launcher import ConfigEditorLauncher as _ConfigLauncher

        self._config_editor_launcher: _ConfigLauncher = _ConfigLauncher(self)

    def _init_history_crash_volume(self) -> None:
        """Declare history-db backings + crash recovery + volume wiring."""
        # ``HistoryDB()`` construction is deferred to first
        self._history_db_backing: Any = None
        # Monotonic-clock timestamp of the most recent lazy-init failure
        self._history_db_failed_at: Any = None
        self._crash_recovery = CrashRecovery(
            thread_registry=self._thread_registry,
        )
        # Volume ducking: reduces system volume during dictation to
        self._duck_crash_recovery_backing: Any = None
        # Monotonic-clock timestamp of the most recent lazy-init failure
        self._duck_crash_recovery_failed_at: Any = None
        # VolumeController owns duck/restore side effects.
        from voice_typer.server.volume_controller import VolumeController

        self.volume: VolumeController = VolumeController(self)
        self._volume_ducker_backing: Any = None
        # Monotonic-clock timestamp of the most recent lazy-init failure
        self._volume_ducker_failed_at: Any = None

    def _init_misc_backings(self) -> None:
        """Declare the remaining lazy backings + IPC/polisher fields."""
        # NOTE: AudioQualityAnalyzer is instantiated earlier in
        self._waveform_bubble_backing: Any = None
        self._waveform_wiring_backing: Any = None
        self._last_transcription: str = ""  # For repaste
        # declare ``_ipc_server`` upfront so VoiceTyperApp
        self._ipc_server: Any | None = None
        # ``TemplateManager`` and ``VocabularyManager`` construction is
        self._template_manager_backing: Any = None
        self._vocabulary_manager_backing: Any = None
        self._llm_polisher = None  # Created on first polish (needs consent check)
        self._cloud_engine = None  # Lazy-init if cloud backend selected
