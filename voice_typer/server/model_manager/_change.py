"""Model switch/change helpers."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Literal, cast

from voice_typer.server import i18n
from voice_typer.server.asr_errors import ModelIntegrityError, ModelNotDownloadedError
from voice_typer.server.asr_registry import AsrBackendRegistry
from voice_typer.server.branding import APP_NAME
from voice_typer.server.model_registry import NO_MODEL_SIZE
from voice_typer.server.tray_types import AppState

# Mirrors ``Config.asr_backend`` (config/_schema.py), the three valid
AsrBackendName = Literal["whisper", "qwen", "parakeet"]

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle (app.py constructs the
    from voice_typer.server.app import VoiceTyperApp

log = logging.getLogger("voice_typer.server.model_manager")


def _backend_for_model_size(model_size: str) -> AsrBackendName:
    """Map a user-selected ``model_size`` to its owning ASR backend."""
    if model_size == "parakeet":
        return "parakeet"
    if model_size == "qwen":
        return "qwen"
    return "whisper"


class ChangeMixin:
    # Members provided by the composed ``ModelManager`` (manager.py):
    _app: VoiceTyperApp
    _registry: AsrBackendRegistry
    _model_change_lock: threading.RLock
    _pending_model_change: str | None
    _pending_backend_change: str | None

    if TYPE_CHECKING:
        # Methods provided by the sibling mixins at runtime
        def _notify_model_load_refused(self, exc: Exception, backend: str | None = None) -> str: ...

        def _mark_deliberately_unloaded(self, backend_name: str | None) -> None: ...

        def _clear_deliberately_unloaded(self, backend_name: str | None) -> None: ...

        def _on_load_success(self, backend_name: str) -> None: ...

        def _ensure_engine(self, backend_name: str) -> None: ...

        def touch_model(self, backend_name: str) -> None: ...

        def _evict_lru_model(self) -> None: ...

        def cancel_idle_unload_timer(self) -> None: ...

    def change_model(self, model_size: str) -> dict:
        """Apply a model change for future dictation sessions (non-blocking).

        Returns
        """
        # cancel any pending idle-unload timer before starting
        self.cancel_idle_unload_timer()
        old_backend = self._app.config.asr_backend
        old_model_size = self._app.config.model_size
        # Determine new backend (mirrors _change_model_setattr_phase
        new_backend = _backend_for_model_size(model_size)
        # No-op guard: re-selecting the already-LOADED model (tray menu
        if model_size and model_size == old_model_size and self._is_model_loaded(model_size):
            log.info(
                "[MODEL] change_model(%s): already the loaded model, skipping reload",
                model_size,
            )
            return {
                "status": "ready",
                "previous": {"backend": old_backend, "model_size": old_model_size},
                "pending": {"backend": new_backend, "model_size": model_size},
            }
        # spawn background daemon thread for the full cycle.
        self._change_model_background(model_size)
        return {
            "status": "loading",
            "previous": {"backend": old_backend, "model_size": old_model_size},
            "pending": {"backend": new_backend, "model_size": model_size},
        }

    def _is_model_loaded(self, model_size: str) -> bool:
        """True when the engine for ``model_size`` is registered AND loaded."""
        try:
            engine = self._registry.get(_backend_for_model_size(model_size))
        except Exception:
            return False
        return engine is not None and bool(getattr(engine, "is_loaded", False))

    def _change_model_background(self, model_size: str) -> None:
        """Spawn a daemon thread to run ``_change_model_blocking``."""
        thread = threading.Thread(
            target=self._change_model_blocking,
            args=(model_size,),
            name="ModelChange",
            daemon=True,
        )
        # Track the thread BEFORE start so callers joining
        self._model_change_thread = thread
        thread.start()
        # track the thread centrally so shutdown_all() can
        try:
            self._app._thread_registry.register(
                name="ModelChange",
                thread=thread,
                stop_event=None,
                join_timeout=3.0,
            )
        except Exception:
            log.debug(
                "[MODEL] Failed to register ModelChange thread with thread_registry",
                exc_info=True,
            )

    def _change_model_blocking(self, model_size: str) -> None:
        """Synchronous model change (for test compat and direct callers)."""
        # cancel any pending idle-unload timer before starting
        self.cancel_idle_unload_timer()
        # outer = _config_mutation_lock (app-level, governs config
        with self._model_change_lock:
            # Step 1: setattr + save + unload-old under config lock.
            with self._app._config_mutation_lock:
                new_backend, old_backend, deferred = self._change_model_setattr_phase(model_size)
                if deferred:
                    # Recording in progress, config saved, deferred
                    return
                # Unload + unregister + clear legacy fields.
                self._change_model_unload_phase(new_backend, old_backend)
            # _config_mutation_lock released here. _model_change_lock
            if model_size == NO_MODEL_SIZE:
                failure_reason = None
            else:
                failure_reason = self._change_model_load_phase(new_backend, model_size)
        # Publish asr_backend_ready ONLY on success. On failure, publish
        if failure_reason is None:
            self._publish_backend_ready_event(new_backend, model_size)
        else:
            self._publish_backend_load_failed_event(
                new_backend,
                model_size,
                failure_reason=failure_reason,
            )

    def _change_model_setattr_phase(self, model_size: str) -> tuple[str, str, bool]:
        """Step 1a: determine backend, setattr + save config.

        Returns ``(new_backend, old_backend, deferred)``. ``deferred`` is True when
        """
        new_backend: AsrBackendName = _backend_for_model_size(model_size)

        old_backend = self._app.config.asr_backend
        log.info(
            "[MODEL] Changing model: %s (%s) -> %s (%s)",
            self._app.config.model_size,
            old_backend,
            model_size,
            new_backend,
        )

        self._app.config.asr_backend = new_backend
        self._app.config.model_size = model_size
        if not self._app.config.save():
            log.warning("[MODEL] config.save() returned False, model change may not persist")

        if self._app.recorder.recording or not self._app._busy_event.is_set():
            log.info(
                "[CONFIG] Model changed to %s (%s); applying after active work",
                model_size,
                new_backend,
            )
            # capture the request so the next _start_dictation
            self._pending_model_change = model_size
            self._app.tray.notify(
                APP_NAME,
                i18n.t("notify.model_manager.change_deferred", model=model_size),
            )
            return new_backend, old_backend, True
        return new_backend, old_backend, False

    def _change_model_unload_phase(self, new_backend: str, old_backend: str) -> None:
        """Step 1b: unload + unregister + clear legacy fields for the OLD backend."""
        # Deliberate unload, the old backend is being swapped out for a
        self._mark_deliberately_unloaded(old_backend)
        # Unload old backend via registry
        self._registry.unload(old_backend)
        # #2 UNREGISTER the old backend so _ensure_engine
        self._registry.unregister(old_backend)
        self._model_load_attempted = False

        # Clear old engine fields. The old ``whisper`` case
        if old_backend == "parakeet":
            self._parakeet_engine = None
        elif old_backend == "qwen":
            self._qwen_engine = None

    def unload_backend_for_delete(self, backend: str) -> None:
        """Unload + unregister ``backend`` ahead of deleting its files."""
        with self._model_change_lock:
            self._mark_deliberately_unloaded(backend)
            self._registry.unload(backend)
            self._registry.unregister(backend)
            self._model_load_attempted = False
            if backend == "parakeet":
                self._parakeet_engine = None
            elif backend == "qwen":
                self._qwen_engine = None

    def _change_model_load_phase(self, new_backend: str, model_size: str) -> str | None:
        """Step 2: construct + load the new engine.

        Returns
        """
        # Create new engine object via registry.create()
        self._ensure_engine(new_backend)

        def on_progress(msg: str):
            self._app.tray.set_state(AppState.LOADING, msg)

        try:
            success = self._registry.load_active(progress_callback=on_progress)
            if success:
                self._on_load_success(new_backend)
                self._app.tray.invalidate_menu_cache()
                return None
            log.warning(
                "[MODEL] %s model failed to load (model_size=%s)",
                new_backend.title(),
                model_size,
            )
            self._app.tray.set_state(
                AppState.ERROR,
                i18n.t(
                    "state.model_manager.backend_failed",
                    backend=new_backend.title(),
                ),
            )
            return f"{new_backend.title()} model failed to load"
        except (ModelNotDownloadedError, ModelIntegrityError) as exc:
            return self._notify_model_load_refused(exc, backend=new_backend)
        except Exception as exc:
            log.exception("[MODEL] Model load failed: %s", exc)
            self._app.tray.set_state(
                AppState.ERROR,
                i18n.t("state.model_manager.model_failed", error=str(exc)),
            )
            return f"load_active raised: {exc}"

    # set_active_backend, switch ASR backend WITHOUT changing
    def set_active_backend(self, backend: str) -> dict:
        """Switch the active ASR backend WITHOUT changing ``model_size`` (non-blocking).

        Returns
        """
        if backend not in ("whisper", "qwen", "parakeet"):
            raise ValueError(
                f"set_active_backend: unknown backend {backend!r}. Expected one of: 'whisper', 'qwen', 'parakeet'."
            )
        # ``backend`` passed the allowlist above; rebind to the Literal
        backend = cast(AsrBackendName, backend)
        # cancel any pending idle-unload timer before starting
        self.cancel_idle_unload_timer()
        old_backend = self._app.config.asr_backend
        old_model_size = self._app.config.model_size
        # Fast-path no-op: if the backend is already active, don't
        if old_backend == backend:
            return {
                "status": "ready",
                "previous": {"backend": old_backend, "model_size": old_model_size},
                "pending": {"backend": backend, "model_size": old_model_size},
            }
        # busy/recording guard, mirroring ``change_model``'s
        # Background thread re-checks recorder.recording under the lock in _blocking.
        try:
            is_recording = bool(self._app.recorder.recording)
            is_busy = not self._app._busy_event.is_set()
        except Exception:
            log.debug(
                "[MODEL] busy/recording check in set_active_backend failed (non-fatal)",
                exc_info=True,
            )
            is_recording = False
            is_busy = False
        if is_recording or is_busy:
            log.info(
                "[CONFIG] Backend change to %s; applying after active work",
                backend,
            )
            # Persist the new backend so a crash mid-recording doesn't
            self._app.config.asr_backend = backend
            if not self._app.config.save():
                log.warning("[MODEL] config.save() returned False during set_active_backend (deferred)")
            # Capture the request, ``apply_pending_model_change`` will
            self._pending_backend_change = backend
            self._app.tray.notify(
                APP_NAME,
                i18n.t("notify.model_manager.backend_change_deferred", backend=backend),
            )
            return {
                "status": "deferred",
                "previous": {"backend": old_backend, "model_size": old_model_size},
                "pending": {"backend": backend, "model_size": old_model_size},
            }
        # spawn background daemon thread for the full cycle.
        self._set_active_backend_background(backend)
        return {
            "status": "loading",
            "previous": {"backend": old_backend, "model_size": old_model_size},
            "pending": {"backend": backend, "model_size": old_model_size},
        }

    def _set_active_backend_background(self, backend: str) -> None:
        """Spawn a daemon thread to run ``_set_active_backend_blocking``."""
        thread = threading.Thread(
            target=self._set_active_backend_blocking,
            args=(backend,),
            name="BackendChange",
            daemon=True,
        )
        # Track the thread BEFORE start (see ``_change_model_background``).
        self._backend_change_thread = thread
        thread.start()
        try:
            self._app._thread_registry.register(
                name="BackendChange",
                thread=thread,
                stop_event=None,
                join_timeout=3.0,
            )
        except Exception:
            log.debug(
                "[MODEL] Failed to register BackendChange thread with thread_registry",
                exc_info=True,
            )

    def _set_active_backend_blocking(self, backend: str) -> None:
        """Synchronous backend switch (for test compat and direct callers)."""
        if backend not in ("whisper", "qwen", "parakeet"):
            raise ValueError(
                f"set_active_backend: unknown backend {backend!r}. Expected one of: 'whisper', 'qwen', 'parakeet'."
            )
        # ``backend`` passed the allowlist above; rebind to the Literal
        backend = cast(AsrBackendName, backend)
        # cancel any pending idle-unload timer before starting
        self.cancel_idle_unload_timer()
        # Outer = _model_change_lock (held throughout the
        load_outcome: bool | None = None
        with self._model_change_lock:
            with self._app._config_mutation_lock:
                old_backend = self._app.config.asr_backend
                if old_backend == backend:
                    # No-op, backend already active.
                    return
                # Re-check ``recorder.recording`` and ``_busy_event``
                try:
                    rec_now = bool(self._app.recorder.recording)
                    busy_now = not self._app._busy_event.is_set()
                except Exception:
                    log.debug(
                        "[MODEL] busy/recording re-check in _set_active_backend_blocking failed (non-fatal)",
                        exc_info=True,
                    )
                    rec_now = False
                    busy_now = False
                if rec_now or busy_now:
                    log.info(
                        "[CONFIG] Backend change to %s deferred (recording=%s, "
                        "busy=%s at lock-acquire time); applying after active work",
                        backend,
                        rec_now,
                        busy_now,
                    )
                    # Persist the new backend so a crash mid-recording
                    self._app.config.asr_backend = backend
                    if not self._app.config.save():
                        log.warning(
                            "[MODEL] config.save() returned False during _set_active_backend_blocking (deferred)"
                        )
                    # Capture the request, ``apply_pending_model_change``
                    self._pending_backend_change = backend
                    self._app.tray.notify(
                        APP_NAME,
                        i18n.t("notify.model_manager.backend_change_deferred", backend=backend),
                    )
                    return
                log.info(
                    "[MODEL] Switching active backend: %s -> %s (model_size=%s unchanged)",
                    old_backend,
                    backend,
                    self._app.config.model_size,
                )
                # Unload old backend via the shared helper. Pass
                self._change_model_unload_phase(backend, old_backend)
                # Set config + persist
                self._app.config.asr_backend = backend
                if not self._app.config.save():
                    log.warning("[MODEL] config.save() returned False during set_active_backend")
                # Pre-construct new backend (no load yet).
                self._ensure_engine(backend)
            # _config_mutation_lock released. _model_change_lock still held.

            def on_progress(msg: str):
                self._app.tray.set_state(AppState.LOADING, msg)

            try:
                success = self._registry.load_active(progress_callback=on_progress)
                if success:
                    self._on_load_success(backend)
                    self._app.tray.invalidate_menu_cache()
                    load_outcome = True
                else:
                    log.warning(
                        "[MODEL] %s backend failed to load during set_active_backend",
                        backend.title(),
                    )
                    self._app.tray.set_state(
                        AppState.ERROR,
                        f"{backend.title()} backend failed to load",
                    )
                    load_outcome = False
            except (ModelNotDownloadedError, ModelIntegrityError) as exc:
                self._notify_model_load_refused(exc, backend=backend)
                load_outcome = False
            except Exception as exc:
                log.exception("[MODEL] set_active_backend load failed: %s", exc)
                self._app.tray.set_state(AppState.ERROR, f"Backend failed: {exc}")
                load_outcome = False
        # Publish asr_backend_ready ONLY on success. On failure, publish
        if load_outcome is True:
            self._publish_backend_ready_event(backend, self._app.config.model_size)
        elif load_outcome is False:
            self._publish_backend_load_failed_event(
                backend,
                self._app.config.model_size,
                failure_reason="load_active returned falsy or raised",
            )

    def _publish_backend_ready_event(self, backend: str, model_size: str) -> None:
        """Publish an ``asr_backend_ready`` event on the event_bus."""
        try:
            from voice_typer.server import event_bus

            event_bus.publish(
                {
                    "type": "asr_backend_ready",
                    "data": {
                        "backend": backend,
                        "model_size": model_size,
                    },
                }
            )
        except Exception:
            log.debug(
                "[MODEL] Failed to publish asr_backend_ready event",
                exc_info=True,
            )

    def _publish_backend_load_failed_event(
        self,
        backend: str,
        model_size: str,
        *,
        failure_reason: str,
    ) -> None:
        """Publish an ``asr_backend_load_failed`` event on the event_bus."""
        try:
            from voice_typer.server import event_bus

            event_bus.publish(
                {
                    "type": "asr_backend_load_failed",
                    "data": {
                        "backend": backend,
                        "model_size": model_size,
                        "failure_reason": failure_reason,
                    },
                }
            )
        except Exception:
            log.debug(
                "[MODEL] Failed to publish asr_backend_load_failed event",
                exc_info=True,
            )

    # apply a deferred model change captured during an active
    def apply_pending_model_change(self) -> bool:
        """If a model change was deferred during a previous recording,"""
        # ``getattr`` defensive: some test fixtures (and the legacy
        pending = self._pending_model_change
        pending_backend = getattr(self, "_pending_backend_change", None)
        if pending is None and pending_backend is None:
            return False
        # Clear both BEFORE applying to avoid re-entry on a crash.
        self._pending_model_change = None
        self._pending_backend_change = None
        applied = False
        if pending is not None:
            log.info("[MODEL] Applying deferred model change to %s", pending)
            # use the BLOCKING variant (not the non-blocking
            self._change_model_blocking(pending)
            applied = True
        if pending_backend is not None:
            log.info("[MODEL] Applying deferred backend change to %s", pending_backend)
            #  + : use the BLOCKING variant for the same
            self._set_active_backend_blocking(pending_backend)
            applied = True
        return applied
