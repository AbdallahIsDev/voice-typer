"""Model load path helpers."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, cast

from voice_typer.server import i18n
from voice_typer.server.asr_errors import ModelIntegrityError, ModelNotDownloadedError
from voice_typer.server.asr_registry import AsrBackendRegistry
from voice_typer.server.branding import APP_NAME
from voice_typer.server.model_manager._change import AsrBackendName
from voice_typer.server.model_registry import NO_MODEL_SIZE
from voice_typer.server.tray_hotkey import notification_hotkey_label
from voice_typer.server.tray_types import AppState

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle (app.py constructs the
    from voice_typer.server.app import LausuApp

log = logging.getLogger("voice_typer.server.model_manager")


class LoadingMixin:
    # Members provided by the composed ``ModelManager`` (manager.py):
    _app: LausuApp
    _registry: AsrBackendRegistry
    _deliberately_unloaded: set[str]
    _model_load_thread: threading.Thread | None
    _model_load_spawn_lock: threading.Lock
    _lazy_init_lock: threading.Lock
    _sync_load_in_progress: bool
    _pending_dictation: bool

    if TYPE_CHECKING:
        # Methods provided by the sibling mixins at runtime
        def _model_downloaded_precheck(self) -> bool: ...

        def _notify_model_load_refused(self, exc: Exception, backend: str | None = None) -> str: ...

        def _ensure_engine(self, backend_name: str) -> None: ...

        def touch_model(self, backend_name: str) -> None: ...

        def _evict_lru_model(self) -> None: ...

        def _clear_deliberately_unloaded(self, backend_name: str | None) -> None: ...

        def cancel_idle_unload_timer(self) -> None: ...

        def active_transcriber(self) -> Any | None: ...

    def _on_load_success(self, backend_name: str) -> None:
        """1. touch the freshly-loaded backend so PERF-015 LRU tracking
        ``set_tray_locale`` (see ``client/.../i18n/push.ts``), so the
        """
        try:
            self.touch_model(backend_name)
            self._evict_lru_model()
        except Exception:
            log.warning("[PERF] LRU tracking failed (non-fatal)", exc_info=True)
        self._clear_deliberately_unloaded(backend_name)
        active = self._registry.get_active()
        name = self._registry.active_name
        if name == "whisper" and active is not None:
            self._app.tray.set_state(
                AppState.IDLE,
                i18n.t(
                    "state.model_manager.ready_whisper",
                    device_info=active.device_info,
                ),
            )
        else:
            self._app.tray.set_state(
                AppState.IDLE,
                i18n.t("state.model_manager.ready_other", name=name.title()),
            )

    def load_background(self) -> None:
        """Background worker: create + load the transcription engine."""
        # bail out early if shutdown was signalled while this
        if self._app._shutting_down:
            log.debug("[MODEL] load_background skipped, shutdown already in progress")
            return
        # can log them without re-reading ``self._app.config`` (which
        backend_name = getattr(self._app.config, "asr_backend", "unknown")
        model_size = getattr(self._app.config, "model_size", "unknown")
        try:
            # Fast existence pre-check: if the configured model is
            if not self._model_downloaded_precheck():
                if model_size == NO_MODEL_SIZE:
                    # Genuine "no model selected" state, nothing to
                    log.debug(
                        "[MODEL] no model selected, refusing load before heavy import",
                    )
                    self._notify_model_load_refused(
                        ModelNotDownloadedError(
                            "No model selected. Open the Models page to pick a model.",
                            model_size=NO_MODEL_SIZE,
                            backend=backend_name,
                        ),
                        backend=backend_name,
                    )
                else:
                    # DEBUG: ``_notify_model_load_refused`` below logs the
                    log.debug(
                        "[MODEL] %s model '%s' not downloaded, refusing load before heavy import",
                        backend_name,
                        model_size,
                    )
                    self._notify_model_load_refused(
                        ModelNotDownloadedError(
                            f"The configured {backend_name} model '{model_size}' "
                            "is not downloaded. Open the Models page to download it.",
                            model_size=model_size,
                            backend=backend_name,
                        ),
                        backend=backend_name,
                    )
                self._pending_dictation = False
                return
            self._ensure_engine(backend_name)

            # Set tray state before heavy import so user sees progress
            self._app.tray.set_state(AppState.LOADING, i18n.t("state.model_manager.loading"))

            def on_progress(msg: str):
                self._app.tray.set_state(AppState.LOADING, msg)

            success = self._registry.load_with_fallback(progress_callback=on_progress)

            if success:
                self._on_load_success(self._registry.active_name)
            else:
                if self._app._shutting_down:
                    return
                # List which backends were attempted
                _backends = self._registry.available_backends
                if callable(_backends):
                    _backends = _backends()
                # Narrow ``_backends`` to a list before joining.
                if isinstance(_backends, list):
                    _attempted = ", ".join(str(_b) for _b in _backends) or "(none registered)"
                else:
                    _attempted = "(none registered)"
                _primary = getattr(self._app.config, "asr_backend", "unknown")
                log.warning(
                    "[STARTUP] All backends failed to load "
                    "(primary=%s, attempted=[%s]). "
                    "Recovery: press your hotkey to retry, or change the backend "
                    "in Settings -> Models.",
                    _primary,
                    _attempted,
                )
                self._app.tray.set_state(AppState.ERROR, i18n.t("state.model_manager.load_failed_retry"))
                # Clear the pending-dictation flag so the ``finally``
                self._pending_dictation = False

        except (ModelNotDownloadedError, ModelIntegrityError) as exc:
            # The selected model isn't on disk (or failed integrity
            self._notify_model_load_refused(exc, backend=backend_name)
            self._pending_dictation = False
        except Exception:
            log.exception(
                "[STARTUP] Background model load crashed (backend=%s, model=%s)",
                backend_name,
                model_size,
            )
            self._app.tray.set_state(AppState.ERROR, i18n.t("state.model_manager.load_failed_retry"))
            # Same failure-path guard as above: a crash must NOT trigger
            self._pending_dictation = False
        finally:
            self._model_load_thread = None
            # If the user pressed F2 during load, honour it now, but
            if self._pending_dictation and not self._app._shutting_down:
                log.info("[STARTUP] Pending dictation -- auto-starting now")
                self._pending_dictation = False
                # Schedule off this loader thread to avoid nesting
                self._app._schedule_timer(0, self._app._start_dictation)

    def start_background_load(self) -> None:
        """Spawn the background model-load thread (idempotent)."""
        if self._model_load_thread is not None and self._model_load_thread.is_alive():
            return
        with self._model_load_spawn_lock:
            # Re-check under the lock, a concurrent caller may have
            if self._model_load_thread is not None and self._model_load_thread.is_alive():
                return
            self._model_load_thread = threading.Thread(
                target=self.load_background,
                name="ModelLoad",
                daemon=True,
            )
            self._model_load_thread.start()
        # track the loader centrally so shutdown_all() can
        try:
            self._app._thread_registry.register(
                name="ModelLoad",
                thread=self._model_load_thread,
                stop_event=None,
                join_timeout=3.0,
            )
        except Exception:
            log.debug(
                "[MODEL] Failed to register ModelLoad thread with thread_registry",
                exc_info=True,
            )

    def _find_installed_model(self) -> tuple[AsrBackendName, str] | None:
        """Return the first installed (downloaded) model as ``(backend, model_size)``."""
        from voice_typer.server import config as _cfg
        from voice_typer.server.model_registry import MODEL_REGISTRY
        from voice_typer.server.tray_models import (
            _check_hf_model_downloaded,
            _check_parakeet_model_downloaded,
            _check_qwen_model_downloaded,
        )

        config_dir = _cfg._config_dir()
        current = getattr(self._app.config, "model_size", "")
        for name, meta in MODEL_REGISTRY.items():
            if name == current:
                continue
            if meta.backend == "qwen":
                if _check_qwen_model_downloaded(
                    config_dir,
                    getattr(self._app.config, "qwen_model_path", None),
                ):
                    return ("qwen", name)
            elif meta.backend == "parakeet":
                if _check_parakeet_model_downloaded(
                    config_dir,
                    getattr(self._app.config, "parakeet_model_path", None),
                ):
                    return ("parakeet", name)
            else:
                if _check_hf_model_downloaded(meta.repo_id, config_dir):
                    # ``ModelMetadata.backend`` is typed ``str``, but the
                    return (cast(AsrBackendName, meta.backend), name)
        return None

    def fallback_to_whisper(self, notify_on_failure: bool = False) -> None:
        """Fall back to an INSTALLED model after the configured one failed."""
        fallback = self._find_installed_model()
        if fallback is None:
            # Nothing installed, surface the actionable message rather
            missing_backend = self._app.config.asr_backend
            self._notify_model_load_refused(
                ModelNotDownloadedError(
                    "No speech model is installed. Open the Models page to download one.",
                    model_size=NO_MODEL_SIZE,
                    backend=missing_backend,
                ),
                backend=missing_backend,
            )
            return
        new_backend, new_model = fallback
        log.info("[MODEL] falling back to installed model %s/%s", new_backend, new_model)
        self._app.config.model_size = new_model
        self._app.config.asr_backend = new_backend
        # persist the fallback so the next boot
        try:
            self._app.config.save()
        except Exception:
            # Previously missing the ``[MODEL]``
            log.warning("[MODEL] failed to persist fallback config", exc_info=True)
        self._ensure_engine(new_backend)

        def on_progress(msg: str):
            self._app.tray.set_state(AppState.LOADING, msg)

        try:
            success = self._registry.load_with_fallback(progress_callback=on_progress)
        except (ModelNotDownloadedError, ModelIntegrityError) as exc:
            # The fallback model isn't downloaded either, surface the
            self._notify_model_load_refused(exc, backend=new_backend)
            return
        if success:
            self._on_load_success(self._registry.active_name)
        else:
            self._app.tray.set_state(AppState.ERROR, i18n.t("state.model_manager.load_failed_retry"))
            if notify_on_failure:
                # critical, bypass toggle (model load failed).
                self._app.tray.notify_safety(
                    APP_NAME,
                    i18n.t(
                        "notify.model_manager.load_failed_critical",
                        hotkey=notification_hotkey_label(self._app.config.hotkey),
                    ),
                )

    def try_load(self, notify_on_failure: bool = False) -> None:
        """Attempt to load the transcription model."""
        self._model_load_attempted = True
        try:
            log.info(
                "[MODEL] Loading model (backend=%s, size=%s, device=%s)...",
                self._app.config.asr_backend,
                self._app.config.model_size,
                self._app.config.device,
            )

            def on_progress(message: str):
                self._app.tray.set_state(AppState.LOADING, message)

            success = self._registry.load_with_fallback(progress_callback=on_progress)
            if success:
                self._on_load_success(self._registry.active_name)
                log.info("[MODEL] Loaded successfully")
            else:
                raise RuntimeError("All backends failed to load")
        except (ModelNotDownloadedError, ModelIntegrityError) as exc:
            _failed_backend = getattr(self._app.config, "asr_backend", "unknown")
            self._notify_model_load_refused(exc, backend=_failed_backend)
        except Exception as e:
            # Include the model name and backend
            _failed_backend = getattr(self._app.config, "asr_backend", "unknown")
            _failed_model = getattr(self._app.config, "model_size", "unknown")
            log.exception(
                "[MODEL] Load FAILED (backend=%s, model=%s)",
                _failed_backend,
                _failed_model,
            )
            self._app.tray.set_state(AppState.ERROR, i18n.t("state.model_manager.load_failed_retry"))
            if notify_on_failure:
                self._app.tray.notify(
                    APP_NAME,
                    i18n.t(
                        "notify.model_manager.load_failed",
                        error=str(e),
                        hotkey=notification_hotkey_label(self._app.config.hotkey),
                    ),
                )

    def ensure_active_engine_loaded(self) -> Any | None:
        """Called from LausuApp._start_dictation to handle the case"""
        # busy-flag rejection. The transcribe thread sets the
        try:
            active_name = self._app.config.asr_backend
            if self._registry.is_busy(active_name) is True:
                log.warning(
                    "[MODEL] Active backend %s is busy (stuck transcription?), "
                    "rejecting ensure_active_engine_loaded and queuing the "
                    "dictation. The watchdog will force-recover and clear "
                    "the busy flag via force_unload_active().",
                    active_name,
                )
                # Queue the dictation so the user's F2 press is honoured
                self._pending_dictation = True
                return None
        except Exception:
            log.debug(
                "[MODEL] busy-check in ensure_active_engine_loaded failed (non-fatal)",
                exc_info=True,
            )
        # cancel any pending idle-unload timer, the user is
        self.cancel_idle_unload_timer()
        # race-safe lazy init. The ``backend = config.asr_backend`` read
        with self._lazy_init_lock:
            backend = self._app.config.asr_backend
            engine = self._registry.get(backend)
            if engine is None:
                self._ensure_engine(backend)
                engine = self._registry.get(backend)
                # re-validate ``config.asr_backend`` after
                current_backend = self._app.config.asr_backend
                if current_backend != backend:
                    log.info(
                        "[MODEL] config.asr_backend changed during engine "
                        "init (%s -> %s); re-routing to current backend",
                        backend,
                        current_backend,
                    )
                    backend = current_backend
                    engine = self._registry.get(backend)
                    if engine is None:
                        self._ensure_engine(backend)
                        engine = self._registry.get(backend)
            # reload-after-idle-unload. If the engine exists but
            if engine is not None and hasattr(engine, "is_loaded") and not engine.is_loaded:
                self._app.tray.set_state(AppState.LOADING, "Loading model...")

                def on_progress(msg: str) -> None:
                    self._app.tray.set_state(AppState.LOADING, msg)

                # Set the synchronous-load flag so the last-resort
                self._sync_load_in_progress = True
                try:
                    self._registry.load_active(progress_callback=on_progress)
                    # Successful reload → backend healthy; clear any
                    self._clear_deliberately_unloaded(backend)
                except Exception:
                    log.warning(
                        "[MODEL] reload after idle-unload failed (non-fatal)",
                        exc_info=True,
                    )
                finally:
                    self._sync_load_in_progress = False
                # Shared success ritual: re-arm the idle-unload timer
                try:
                    self._on_load_success(backend)
                except Exception:
                    log.debug("[MODEL] success ritual after reload failed", exc_info=True)
        return self.active_transcriber()
