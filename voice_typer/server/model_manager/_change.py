"""Model/backend change pipeline and pending-change application."""

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

# Mirrors ``Config.asr_backend`` (config/_schema.py) — the three valid
# backend names. ``set_active_backend`` validates against this allowlist
# before any config write.
AsrBackendName = Literal["whisper", "qwen", "parakeet"]

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle (app.py constructs the
    # ModelManager; this mixin is part of it). At runtime, ``_app`` is
    # whatever object ``ModelManagerCore.__init__`` received (a
    # ``VoiceTyperApp`` in production, mocks in tests).
    from voice_typer.server.app import VoiceTyperApp

log = logging.getLogger("voice_typer.server.model_manager")


def _backend_for_model_size(model_size: str) -> AsrBackendName:
    """Map a user-selected ``model_size`` to its owning ASR backend.

    Single authoritative copy of the mapping the two change-model entry
    points (``change_model`` and ``_change_model_setattr_phase``)
    previously duplicated verbatim: ``parakeet`` → the parakeet
    backend, ``qwen`` → the qwen backend, everything else (whisper
    sizes, ``tiny``, ``large-v3``, …) → the whisper backend.
    """
    if model_size == "parakeet":
        return "parakeet"
    if model_size == "qwen":
        return "qwen"
    return "whisper"


class ChangeMixin:
    # Members provided by the composed ``ModelManager`` (manager.py):
    # core state lives on ``ModelManagerCore`` (_base.py). Annotations
    # only — no values — so no runtime attribute is created and the
    # runtime MRO is unaffected (same pattern as dictation_pipeline's
    # ``_StorageStepMixin._app``).
    _app: VoiceTyperApp
    _registry: AsrBackendRegistry
    _model_change_lock: threading.RLock
    _pending_model_change: str | None
    _pending_backend_change: str | None

    if TYPE_CHECKING:
        # Methods provided by the sibling mixins at runtime
        # (``_notify.py`` / ``_construction.py`` / ``_lifecycle.py``).
        # Declared as TYPE_CHECKING-only stubs so this mixin type-checks
        # standalone; a real (un-guarded) ``def`` here would shadow the
        # sibling implementations in the composed class's MRO.
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

        Previously this method blocked the calling IPC worker for
        5-30s while ``_change_model_load_phase`` ran ``load_active`` (disk
        + torch import + weight load). Now it spawns a background daemon
        thread to do the full setattr+unload+load cycle and returns
        immediately with a "loading" ack. The background thread holds
        ``_model_change_lock`` for the duration so concurrent
        ``change_model`` / ``set_active_backend`` calls serialize. On
        completion the thread publishes an ``asr_backend_ready`` event on
        the event_bus (mirroring the ``asr_backend_disabled`` pattern).

        Handles Whisper, Parakeet, and Qwen backends. Unloads the old
        engine and loads the new one immediately (unless currently
        recording — in which case the change is deferred via
        ``_pending_model_change``).

        Uses the registry to unload/load instead of having
        three separate branches for parakeet/qwen/whisper.

        logs every model change with old/new backend and model size.

         the background thread's body is guarded by
        ``self._model_change_lock`` so two concurrent calls cannot both
        unload + re-register + reload the same backend.

        the background thread acquires ``_config_mutation_lock``
        for the setattr+save+unload phase. See ``_change_model_blocking``
        for the lock-order contract.

        Returns
        -------
        dict
            Ack dict shaped ``{"status": "loading", "previous": {...},
            "pending": {...}}``. Callers that need to wait for the load
            to complete (e.g. ``apply_pending_model_change``, tests)
            should call ``_change_model_blocking`` instead.
        """
        # cancel any pending idle-unload timer before starting
        # the unload/reload cycle — otherwise the timer could fire
        # mid-switch and unload the NEW model.
        self.cancel_idle_unload_timer()
        old_backend = self._app.config.asr_backend
        old_model_size = self._app.config.model_size
        # Determine new backend (mirrors _change_model_setattr_phase
        # logic) so the ack dict carries the pending backend.
        new_backend = _backend_for_model_size(model_size)
        # spawn background daemon thread for the full cycle.
        self._change_model_background(model_size)
        return {
            "status": "loading",
            "previous": {"backend": old_backend, "model_size": old_model_size},
            "pending": {"backend": new_backend, "model_size": model_size},
        }

    def _change_model_background(self, model_size: str) -> None:
        """Spawn a daemon thread to run ``_change_model_blocking``.

        The thread holds ``_model_change_lock`` for the duration of the
        setattr+unload+load cycle so concurrent ``change_model`` /
        ``set_active_backend`` calls serialize. On completion the thread
        publishes an ``asr_backend_ready`` event on the event_bus.

        Mirrors ``start_background_load``'s thread-registration pattern
        () so ``shutdown_all()`` can join the thread during
        ``quit()``. Best-effort registration — if the registry is missing
        (e.g. in a stripped-down test fixture) we log and continue; the
        thread is a daemon and will die on process exit anyway.
        """
        thread = threading.Thread(
            target=self._change_model_blocking,
            args=(model_size,),
            name="ModelChange",
            daemon=True,
        )
        # Track the thread BEFORE start so callers joining
        # ``_model_change_thread`` never miss it (a fast load could
        # otherwise finish between ``start()`` and the assignment).
        self._model_change_thread = thread
        thread.start()
        # track the thread centrally so shutdown_all() can
        # signal-and-join it. Best-effort — re-registering "ModelChange"
        # overwrites the previous entry (with a warning log); the
        # previous thread is still a daemon and will be killed on
        # process exit.
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
        """Synchronous model change (for test compat and direct callers).

        This is the original ``change_model`` body, preserved as a
        separate method so callers that need to wait for the load to
        complete (e.g. ``apply_pending_model_change``, tests) can do so.
        The IPC handler uses the non-blocking ``change_model`` instead.

        On completion (success or failure), publishes an
        ``asr_backend_ready`` event on the event_bus so subscribers
        (renderer, diagnostics aggregator) know the load finished.
        """
        # cancel any pending idle-unload timer before starting
        # the unload/reload cycle.
        self.cancel_idle_unload_timer()
        # outer = _config_mutation_lock (app-level, governs config
        # setattr + save); inner = _model_change_lock (ModelManager-level,
        # guards the unload/reload cycle).  See change_model docstring.
        with self._model_change_lock:
            # Phase 1: setattr + save + unload-old under config lock.
            with self._app._config_mutation_lock:
                new_backend, old_backend, deferred = self._change_model_setattr_phase(model_size)
                if deferred:
                    # Recording in progress — config saved, deferred
                    # flag set, notification shown. Skip the load.
                    return
                # Unload + unregister + clear legacy fields.
                self._change_model_unload_phase(new_backend, old_backend)
            # _config_mutation_lock released here. _model_change_lock
            # still held — concurrent IPC set_config calls can proceed.
            # Phase 2: construct + load the new engine OUTSIDE the
            # config lock (see above). ``NO_MODEL_SIZE`` ("") means the
            # user has no active model — the unload above is the whole
            # change; do NOT construct/load an engine for the empty
            # size (there is no repo for it). The renderer sees
            # ``asr_backend_ready`` with an empty model and shows
            # "No model selected".
            if model_size == NO_MODEL_SIZE:
                failure_reason = None
            else:
                failure_reason = self._change_model_load_phase(new_backend, model_size)
        # Publish asr_backend_ready ONLY on success. On failure, publish
        # asr_backend_load_failed so the renderer can show an error
        # instead of silently dismissing the loading spinner. Published
        # AFTER the lock is released so subscribers don't block on the
        # lock. ``failure_reason is None`` covers the deferred
        # early-return path (no event published — the load didn't
        # happen).
        if failure_reason is None:
            self._publish_backend_ready_event(new_backend, model_size)
        else:
            self._publish_backend_load_failed_event(
                new_backend,
                model_size,
                failure_reason=failure_reason,
            )

    def _change_model_setattr_phase(self, model_size: str) -> tuple[str, str, bool]:
        """Phase 1a: determine backend, setattr + save config.

        Returns ``(new_backend, old_backend, deferred)``. ``deferred`` is True when
        the model change was queued via ``_pending_model_change``
        because a recording is in progress — the caller should skip
        the load phase. ``old_backend`` is captured BEFORE the setattr
        overwrites ``config.asr_backend`` so the unload phase can use
        the correct value.

        Caller MUST hold both ``_config_mutation_lock`` and
        ``_model_change_lock``.
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
            log.warning("[MODEL] config.save() returned False — model change may not persist")

        if self._app.recorder.recording or not self._app._busy_event.is_set():
            log.info(
                "[CONFIG] Model changed to %s (%s); applying after active work",
                model_size,
                new_backend,
            )
            # capture the request so the next _start_dictation
            # re-runs the unload/load cycle. Without this, the config
            # is saved on disk but the in-memory engine stays as the
            # old backend — the "will change after current recording"
            # notification was a lie.
            self._pending_model_change = model_size
            self._app.tray.notify(
                APP_NAME,
                i18n.t("notify.model_manager.change_deferred", model=model_size),
            )
            return new_backend, old_backend, True
        return new_backend, old_backend, False

    def _change_model_unload_phase(self, new_backend: str, old_backend: str) -> None:
        """Phase 1b: unload + unregister + clear legacy fields for the OLD backend.

        Caller MUST hold both ``_config_mutation_lock`` and
        ``_model_change_lock``. ``new_backend`` is the target backend.
        ``old_backend`` is the backend that was active BEFORE the setattr
        phase overwrote ``config.asr_backend`` — captured by the caller
        in ``_change_model_setattr_phase`` and passed in here so we don't
        accidentally read the post-setattr value (which would always
        equal ``new_backend``).

        Note: we ALWAYS unload + unregister, even when
        ``old_backend == new_backend``. This is required for whisper,
        where ``model_size`` may have changed and a fresh
        ``TranscriptionEngine`` must be constructed with the new
        ``model_size`` kwarg. Skipping the unload (the
        optimization) broke ``test_model_change_uses_config_device``.
        """
        # Deliberate unload — the old backend is being swapped out for a
        # new one; the last-resort tray notification must NOT tell the
        # user to download a backend they explicitly switched away from.
        self._mark_deliberately_unloaded(old_backend)
        # Unload old backend via registry
        self._registry.unload(old_backend)
        # #2 UNREGISTER the old backend so _ensure_engine
        # actually constructs a fresh one. Previously unload() only
        # called backend.unload() but left the backend in the registry,
        # so _ensure_engine's "if registry.get(name) is not None: return"
        # short-circuited and no new engine was constructed.
        self._registry.unregister(old_backend)
        self._model_load_attempted = False

        # Clear old engine fields. The old ``whisper`` case
        # (``elif self.transcriber is not None: self.transcriber.unload()``)
        # was removed: by the time we reach here,
        # ``self._registry.unregister(old_backend)`` above already
        # cleared ``registry.get("whisper")`` (which ``self.transcriber``
        # delegates to), so the elif condition was always False; and
        # ``self._registry.unload(old_backend)`` above already performed
        # the whisper ``unload()`` call.
        if old_backend == "parakeet":
            self._parakeet_engine = None
        elif old_backend == "qwen":
            self._qwen_engine = None

    def _change_model_load_phase(self, new_backend: str, model_size: str) -> str | None:
        """Phase 2: construct + load the new engine.

        Caller MUST hold ``_model_change_lock``. Must NOT hold
        ``_config_mutation_lock`` (see above).

        ``model_size`` is the user-requested model size (operation
        input) — included in the failure log so a model-load failure
        report shows the input that produced the failure, not just the
        backend name. The underlying exception is already logged one
        level down by ``AsrBackendRegistry.load_active`` via
        ``log.exception`` (see ``asr_registry.py``).

        Returns
        -------
        str | None
            ``None`` on success; a short human-readable
            ``failure_reason`` string on failure (falsy ``load_active``
            return or raised exception). The caller
            (``_change_model_blocking``) uses the return value to decide
            whether to publish ``asr_backend_ready`` vs
            ``asr_backend_load_failed``.
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

    # set_active_backend — switch ASR backend WITHOUT changing
    # model_size. Mirrors change_model's unload/reload cycle but only
    # swaps the backend. The model_size field is left untouched so
    # Whisper's model selection (which depends on model_size) is
    # preserved across backend switches. For parakeet/qwen, model_size
    # is informational (their engines ignore it).
    def set_active_backend(self, backend: str) -> dict:
        """Switch the active ASR backend WITHOUT changing ``model_size`` (non-blocking).

        Previously this method blocked the calling IPC worker for
        5-30s while ``load_active`` ran. Now it spawns a background
        daemon thread (mirroring ``change_model``'s pattern) and returns
        immediately with a "loading" ack. On completion the thread
        publishes an ``asr_backend_ready`` event on the event_bus.

        Previously ``Service.set_active_backend`` delegated to
        ``self._app.models.set_active_backend(backend)`` but
        :class:`ModelManager` never defined that method — the IPC
        ``set_config`` handler caught the ``AttributeError`` and logged
        a warning, returning ``ack`` to the renderer while the actual
        backend swap never happened. Old backends stayed loaded (GPU
        + RAM) until LRU eviction.

        if the user is recording or the transcribe thread holds
        the busy event (mid-transcription), DEFER — persist config +
        capture the requested backend in ``_pending_backend_change`` +
        notify "will change after current recording" + return a
        "deferred" ack. The actual unload/load cycle runs on the next
        :meth:`apply_pending_model_change` (called from
        ``recording_controller.start`` before the new recording begins).
        Without this guard, the background thread would run the unload
        phase mid-transcription, unloading the ctranslate2 model from
        underneath the in-flight transcribe thread (crash / heap
        corruption / stuck thread — see  in review.md).

        Parameters
        ----------
        backend :
            One of ``"whisper"``, ``"qwen"``, ``"parakeet"``. Any other
            value raises :class:`ValueError`.

        Returns
        -------
        dict
            Ack dict shaped ``{"status": "loading"|"ready"|"deferred",
            "previous": {...}, "pending": {...}}``. ``status == "ready"``
            when the backend is already active (fast-path no-op).
            ``status == "deferred"`` when the change was queued via
            ``_pending_backend_change`` ().

        Raises
        ------
        ValueError
            If ``backend`` is not one of ``"whisper"``, ``"qwen"``,
            ``"parakeet"``. The validation happens synchronously BEFORE
            any thread spawn so the IPC handler's ``failed_keys``
            mechanism still catches it.
        """
        if backend not in ("whisper", "qwen", "parakeet"):
            raise ValueError(
                f"set_active_backend: unknown backend {backend!r}. Expected one of: 'whisper', 'qwen', 'parakeet'."
            )
        # ``backend`` passed the allowlist above; rebind to the Literal
        # type ``Config.asr_backend`` declares so the config assignments
        # below type-check without per-site casts.
        backend = cast(AsrBackendName, backend)
        # cancel any pending idle-unload timer before starting
        # the unload/reload cycle.
        self.cancel_idle_unload_timer()
        old_backend = self._app.config.asr_backend
        old_model_size = self._app.config.model_size
        # Fast-path no-op: if the backend is already active, don't
        # spawn a background thread. (The background thread re-checks
        # this under the lock for race-safety, but the fast path avoids
        # needless thread churn in the common case.)
        if old_backend == backend:
            return {
                "status": "ready",
                "previous": {"backend": old_backend, "model_size": old_model_size},
                "pending": {"backend": backend, "model_size": old_model_size},
            }
        # busy/recording guard, mirroring ``change_model``'s
        # ``_change_model_setattr_phase`` deferral pattern. If the user
        # is recording OR the transcribe thread holds the busy event
        # (mid-transcription), we MUST NOT run the unload phase —
        # unloading the ctranslate2 model mid-inference crashes /
        # corrupts / hangs the transcribe thread. Persist config +
        # capture the request + notify, then return a "deferred" ack.
        # The next ``apply_pending_model_change`` (called from
        # ``recording_controller.start`` before the new recording
        # begins) re-invokes ``set_active_backend`` when the app is no
        # longer busy. The check is best-effort outside the lock —
        # ``_set_active_backend_blocking`` re-checks ``recorder.recording``
        # and ``_busy_event`` INSIDE ``_model_change_lock`` +
        # ``_config_mutation_lock`` for race-safety (and re-defers if a
        # recording started between this check and the lock acquisition).
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
            # lose the user's intent (matches ``change_model``'s
            # setattr-before-deferral pattern).
            self._app.config.asr_backend = backend
            if not self._app.config.save():
                log.warning("[MODEL] config.save() returned False during set_active_backend (deferred)")
            # Capture the request — ``apply_pending_model_change`` will
            # re-invoke ``set_active_backend`` when the app is no longer
            # busy. Because we are not currently recording at that point
            # and ``_busy_event`` is set (not busy), this deferral
            # branch is skipped and the full unload/load cycle runs.
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
        """Spawn a daemon thread to run ``_set_active_backend_blocking``.

        Mirrors ``_change_model_background``'s thread-registration pattern.
        The thread holds ``_model_change_lock`` for the duration so
        concurrent ``change_model`` / ``set_active_backend`` calls
        serialize.
        """
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
        """Synchronous backend switch (for test compat and direct callers).

        This is the original ``set_active_backend`` body,
        preserved as a separate method so callers that need to wait for
        the load to complete (tests) can do so. The IPC handler uses
        the non-blocking ``set_active_backend`` instead.

        On completion, publishes ``asr_backend_ready`` ONLY on success.
        On failure (load_active returned falsy OR raised), publishes
        ``asr_backend_load_failed`` with ``{"backend": ..., "failure_reason": ...}``
        so the renderer can show an error instead of silently
        dismissing the loading spinner. The deferred case (recording
        in progress) and the no-op case (backend already active)
        publish no event.
        """
        if backend not in ("whisper", "qwen", "parakeet"):
            raise ValueError(
                f"set_active_backend: unknown backend {backend!r}. Expected one of: 'whisper', 'qwen', 'parakeet'."
            )
        # ``backend`` passed the allowlist above; rebind to the Literal
        # type ``Config.asr_backend`` declares so the config assignments
        # below type-check without per-site casts.
        backend = cast(AsrBackendName, backend)
        # cancel any pending idle-unload timer before starting
        # the unload/reload cycle — otherwise the timer could fire
        # mid-switch and unload the NEW model. The post-load touch_model
        # call below re-arms a fresh timer on the new backend.
        self.cancel_idle_unload_timer()
        # Outer = _model_change_lock (held throughout the
        # unload+construct+load cycle). Inner = _config_mutation_lock
        # (acquired only for setattr + save + the quick unload phase).
        # ``load_outcome`` captures whether the load succeeded so we
        # can publish the correct event AFTER the lock is released.
        # Initialised to ``None`` so the no-op / deferred early returns
        # skip event publication (their existing behaviour).
        load_outcome: bool | None = None
        with self._model_change_lock:
            with self._app._config_mutation_lock:
                old_backend = self._app.config.asr_backend
                if old_backend == backend:
                    # No-op — backend already active.
                    return
                # Re-check ``recorder.recording`` and ``_busy_event``
                # INSIDE both locks for race-safety. The non-blocking
                # ``set_active_backend`` wrapper checks these OUTSIDE the
                # lock (best-effort) before spawning this background
                # thread; a recording could have started between that
                # check and the lock acquisition. Without this re-check,
                # we would unload the ctranslate2 model mid-inference
                # (crashing / corrupting / hanging the transcribe
                # thread). Mirrors the deferral pattern in
                # ``_change_model_setattr_phase`` (line ~891).
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
                    # doesn't lose the user's intent (matches
                    # ``change_model``'s setattr-before-deferral pattern).
                    self._app.config.asr_backend = backend
                    if not self._app.config.save():
                        log.warning(
                            "[MODEL] config.save() returned False during _set_active_backend_blocking (deferred)"
                        )
                    # Capture the request — ``apply_pending_model_change``
                    # will re-invoke ``_set_active_backend_blocking`` when
                    # the app is no longer busy.
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
                # ``backend`` as ``new_backend`` and the captured
                # ``old_backend`` so the helper sees "different
                # backends" and performs the unload.
                self._change_model_unload_phase(backend, old_backend)
                # Set config + persist
                self._app.config.asr_backend = backend
                if not self._app.config.save():
                    log.warning("[MODEL] config.save() returned False during set_active_backend")
                # Pre-construct new backend (no load yet).
                self._ensure_engine(backend)
            # _config_mutation_lock released. _model_change_lock still held.
            # Load the new backend.

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
        # asr_backend_load_failed so the renderer can show an error
        # instead of silently dismissing the loading spinner. Published
        # AFTER the lock is released so subscribers don't block on the
        # lock. ``load_outcome is None`` covers the no-op and deferred
        # early-return paths (no event published).
        if load_outcome is True:
            self._publish_backend_ready_event(backend, self._app.config.model_size)
        elif load_outcome is False:
            self._publish_backend_load_failed_event(
                backend,
                self._app.config.model_size,
                failure_reason="load_active returned falsy or raised",
            )

    def _publish_backend_ready_event(self, backend: str, model_size: str) -> None:
        """Publish an ``asr_backend_ready`` event on the event_bus.

        Mirrors the ``asr_backend_disabled`` event pattern in
        ``asr_registry.py``. The event signals to the renderer (and any
        in-process subscribers) that a backend load has finished — either
        the user changed models via Settings, or the active backend was
        switched. The renderer uses this to dismiss the "loading"
        spinner shown after the IPC ``set_config`` ack.

        The event is published AFTER ``_model_change_lock`` is released
        so subscribers don't block on the lock. Best-effort — a publish
        failure is logged at DEBUG and swallowed (the load itself
        already succeeded; the event is purely informational).
        """
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
        """Publish an ``asr_backend_load_failed`` event on the event_bus.

        Previously ``_change_model_blocking`` and
        ``_set_active_backend_blocking`` published ``asr_backend_ready``
        UNCONDITIONALLY on completion — even when ``load_active`` had
        returned falsy or raised. The renderer's loading spinner was
        dismissed on ``asr_backend_ready``, so a failed load left the
        user with no visual indication that the spinner had cleared
        because the load FAILED (not because it succeeded). The
        renderer's tray icon transitioned to ``AppState.ERROR`` (set by
        the load-phase error path), but the renderer-side spinner
        dismissal was incorrect.

        This companion event is published ONLY on failure. The renderer
        should subscribe to it (alongside ``asr_backend_ready``) and
        show an error message in addition to dismissing the spinner.

        ``failure_reason`` is a short, human-readable string explaining
        why the load failed (e.g. ``"load_active returned falsy"`` or
        ``"load_active raised: <ExcType>"``). It's surfaced in the
        event payload so the renderer can show it verbatim or map it to
        a user-facing message.

        NOTE: the renderer change (subscribing to
        ``asr_backend_load_failed`` and showing an error) is OUT OF
        SCOPE for this fix — it lives in the Electron/renderer codebase.
        This method only emits the event; the renderer's matching
        listener must be added separately.

        Best-effort — a publish failure is logged at DEBUG and swallowed
        (the load already failed; the event is purely informational).
        """
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
    # recording. Called from _start_dictation before the new recording
    # begins so the in-memory engine matches the saved config.
    def apply_pending_model_change(self) -> bool:
        """If a model change was deferred during a previous recording,
        re-run change_model now (without the early return) so the new
        backend is actually loaded. Returns True if a change was applied.

        also applies a deferred backend-only change
        (``_pending_backend_change``) captured by
        :meth:`set_active_backend` while the user was recording/busy.
        The two pending fields are independent — a single recording
        could have triggered BOTH a ``change_model`` request (which
        sets ``_pending_model_change``) AND a ``set_active_backend``
        request (which sets ``_pending_backend_change``). We apply the
        model change FIRST (because ``change_model`` re-evaluates the
        backend from the new ``model_size`` — e.g. model_size="parakeet"
        implies backend="parakeet") and then the backend change
        SECOND (so an explicit ``set_active_backend("whisper")``
        overrides the model-change-implied backend).

        Both pending fields are cleared BEFORE either apply runs so a
        crash mid-apply doesn't leave a stale request that re-fires on
        the next recording. The apply methods (``_change_model_blocking``
        / ``_set_active_backend_blocking``) are idempotent under the
        not-currently-busy precondition (which holds because
        ``recording_controller.start`` calls us before recording
        starts), so a re-invocation from a subsequent recording would
        be a no-op (the config already reflects the requested state).
        """
        # ``getattr`` defensive: some test fixtures (and the legacy
        # ``test_recording_and_audio.py::TestPendingModelChange``)
        # construct ModelManager via ``__new__`` and only set
        # ``_pending_model_change`` — they don't know about the new
        # ``_pending_backend_change`` field added in  Reading
        # via ``getattr(..., None)`` preserves their behaviour (no
        # AttributeError) instead of forcing every test fixture to
        # set the new field.
        pending = self._pending_model_change
        pending_backend = getattr(self, "_pending_backend_change", None)
        if pending is None and pending_backend is None:
            return False
        # Clear both BEFORE applying to avoid re-entry on a crash.
        # See method docstring for the safety argument.
        self._pending_model_change = None
        self._pending_backend_change = None
        applied = False
        if pending is not None:
            log.info("[MODEL] Applying deferred model change to %s", pending)
            # use the BLOCKING variant (not the non-blocking
            # ``change_model``) because the caller —
            # ``recording_controller._start_dictation`` — needs the model
            # fully loaded BEFORE the recorder starts capturing audio. The
            # non-blocking variant would return immediately and the
            # recorder would start with the OLD (unloaded) engine.
            self._change_model_blocking(pending)
            applied = True
        if pending_backend is not None:
            log.info("[MODEL] Applying deferred backend change to %s", pending_backend)
            #  + : use the BLOCKING variant for the same
            # reason as the model-change branch above. The
            # preconditions (not recording + not busy) hold because
            # ``recording_controller.start`` calls us before recording
            # starts, so the  deferral branch in
            # ``set_active_backend`` is skipped.
            self._set_active_backend_blocking(pending_backend)
            applied = True
        return applied

    # ── Lazy init for _start_dictation ─────────────────────────────────
