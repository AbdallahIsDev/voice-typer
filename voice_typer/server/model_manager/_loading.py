"""Background/manual model loading, fallback chain, lazy init."""

from __future__ import annotations

import logging
import threading
from typing import Any

from voice_typer.server import i18n
from voice_typer.server.asr_errors import ModelIntegrityError, ModelNotDownloadedError
from voice_typer.server.branding import APP_NAME
from voice_typer.server.model_registry import NO_MODEL_SIZE
from voice_typer.server.tray_hotkey import notification_hotkey_label
from voice_typer.server.tray_types import AppState

log = logging.getLogger("voice_typer.server.model_manager")


class LoadingMixin:
    def load_background(self) -> None:
        """Background worker: create + load the transcription engine.

        Runs in a daemon thread so the heavy torch/transformers import and
        weight download/read (off-disk on cold boot) do not block the app
        reaching an interactive state.  All tray state transitions happen
        here; if a dictation is pending (user pressed F2 during load), it
        is auto-started once loading succeeds.
        """
        # bail out early if shutdown was signalled while this
        # loader was queued. Without this guard the loader would proceed
        # to construct + load an ASR backend after ``_do_cleanup`` has
        # already torn down the tray, recorder, hotkeys, etc., touching
        # freed state. The thread_registry join (3s timeout) gives the
        # in-flight loader a chance to exit, but this early check avoids
        # the race where the loader hasn't started its first instruction
        # yet.
        if self._app._shutting_down:
            log.debug("[MODEL] load_background skipped — shutdown already in progress")
            return
        # Capture the backend/model BEFORE the try so the except handler
        # can log them without re-reading ``self._app.config`` (which
        # could itself be the source of the original exception — a
        # degraded/None config would make the handler raise a SECOND
        # exception, skipping the pending-dictation clear and the tray
        # ERROR transition).
        backend_name = getattr(self._app.config, "asr_backend", "unknown")
        model_size = getattr(self._app.config, "model_size", "unknown")
        try:
            # Fast existence pre-check: if the configured model is
            # definitively NOT on disk, refuse immediately — BEFORE the
            # heavy engine import, BEFORE the misleading "Loading model"
            # LOADING state, and with a GENERIC message (no model name).
            # The load path would raise ModelNotDownloadedError anyway
            # (the registry re-raises for a missing primary — no whisper
            # fallback), so this only skips wasted work. Cloud backends /
            # unknown model sizes return True from the probe (nothing to
            # gate); the probe is TTL-cached and costs one stat.
            if not self._model_downloaded_precheck():
                if model_size == NO_MODEL_SIZE:
                    # Genuine "no model selected" state — nothing to
                    # load, and no phantom model to claim is missing.
                    # DEBUG: ``_notify_model_load_refused`` below logs
                    # the single WARNING for every refusal path.
                    log.debug(
                        "[MODEL] no model selected — refusing load before heavy import",
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
                    # single WARNING for every refusal path — a second
                    # WARNING here duplicated the same event. The model
                    # size travels in the exception message instead.
                    log.debug(
                        "[MODEL] %s model '%s' not downloaded — refusing load before heavy import",
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
                #  PERF-015 LRU eviction — touch the
                # freshly-loaded backend so it's tracked, then evict the
                # LRU model if more than _MAX_LOADED_MODELS are now loaded.
                # Guarded so a tracking failure doesn't break the load.
                try:
                    self.touch_model(self._registry.active_name)
                    self._evict_lru_model()
                except Exception:
                    log.warning(
                        "[PERF] LRU tracking failed (non-fatal)",
                        exc_info=True,
                    )
                # Successful load → the backend is healthy again; clear
                # any deliberate-unload flag so a FUTURE genuine failure
                # re-notifies the user.
                self._clear_deliberately_unloaded(self._registry.active_name)
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
            else:
                if self._app._shutting_down:
                    return
                # List which backends were attempted
                # so the user (and support) can see exactly what failed,
                # plus a remediation hint. ``available_backends`` returns
                # the registered backend names; ``active_name`` is the one
                # that was selected as primary.
                # pyrefly not-callable — ``available_backends`` is a
                # @property on ASRRegistry (asr_registry.py:534-538) returning
                # ``list[str]``, NOT a method. Calling it (``()``) raises
                # ``TypeError: 'list' object is not callable`` at runtime.
                # Use the property's value directly (no parentheses). Guard
                # with ``callable()`` so test doubles that override the
                # attribute with a callable (e.g. MagicMock auto-spec) still
                # work.
                _backends = self._registry.available_backends
                if callable(_backends):
                    _backends = _backends()
                # Narrow ``_backends`` to a list before joining.
                # ``available_backends`` is a ``@property`` returning
                # ``list[str]``, but the ``callable(_backends)`` fallback
                # (for MagicMock test doubles that override the attribute
                # with a callable) widens the inferred type to
                # ``list[str] | object`` — ``str.join`` rejects ``object``
                # (not iterable). At runtime the value is always a list.
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
                # block does NOT auto-start a dictation that would
                # immediately fail (no model is loaded). Pre-fix, the
                # flag was NOT cleared and the finally block
                # unconditionally scheduled ``_start_dictation``, which
                # fell through to ``fallback_to_whisper`` (same root
                # cause), entered a tight retry loop, and spammed the
                # tray with ERROR state.
                self._pending_dictation = False

        except (ModelNotDownloadedError, ModelIntegrityError) as exc:
            # The selected model isn't on disk (or failed integrity
            # verification) — the app never auto-downloads. Surface an
            # actionable message and do NOT auto-start any pending
            # dictation (it would fail the same way).
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
            # the finally's auto-start of a pending dictation (it would
            # crash again on the same root cause).
            self._pending_dictation = False
        finally:
            self._model_load_thread = None
            # If the user pressed F2 during load, honour it now — but
            # ONLY on success. On failure/crash the ``_pending_dictation``
            # flag was already cleared by the error paths above, so this
            # check skips the auto-start (which would otherwise loop on
            # ``fallback_to_whisper`` and fail the same way, spamming the
            # tray with ERROR state).
            if self._pending_dictation and not self._app._shutting_down:
                log.info("[STARTUP] Pending dictation -- auto-starting now")
                self._pending_dictation = False
                # Schedule off this loader thread to avoid nesting
                self._app._schedule_timer(0, self._app._start_dictation)

    def start_background_load(self) -> None:
        """Spawn the background model-load thread (idempotent).

        register the thread with ``app._thread_registry`` so
        ``shutdown_all()`` can join it during ``quit()``. Previously
        the ModelLoad thread was a daemon but untracked — it was
        indirectly signalled via ``_shutting_down`` checks inside
        ``load_background``, which meant a stuck model load (e.g. a
        slow Whisper download on a cold boot) could outlive
        ``_do_cleanup`` and access torn-down state (tray, recorder,
        hotkeys). With registration, ``shutdown_all()`` joins it with
        a 3s timeout, matching the existing transcription-thread join
        in ``_do_cleanup``. ``stop_event=None`` because the loader
        has no single cancellation point — it checks
        ``_app._shutting_down`` itself at the top of
        ``load_background``.
        """
        if self._model_load_thread is not None and self._model_load_thread.is_alive():
            return
        with self._model_load_spawn_lock:
            # Re-check under the lock — a concurrent caller may have
            # spawned the thread between our check and the lock
            # acquisition.
            if self._model_load_thread is not None and self._model_load_thread.is_alive():
                return
            self._model_load_thread = threading.Thread(
                target=self.load_background,
                name="ModelLoad",
                daemon=True,
            )
            self._model_load_thread.start()
        # track the loader centrally so shutdown_all() can
        # signal-and-join it. Best-effort — if the registry is missing
        # (e.g. in a stripped-down test fixture) we log and continue;
        # the loader is a daemon and will die on process exit anyway.
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

    def fallback_to_whisper(self, notify_on_failure: bool = False) -> None:
        """Fallback to Whisper tiny after Parakeet/Qwen backend failed.

        008: Uses the registry to reconfigure and reload.
        Switches config to whisper/tiny (the smallest Whisper model —
        deliberately a LITERAL, not ``DEFAULT_MODEL_SIZE``: the
        fallback must stay small/fast even if the config default
        changes), ensures the whisper backend is registered, and
        delegates loading to AsrBackendRegistry.load_with_fallback().
        """
        self._app.config.model_size = "tiny"
        self._app.config.asr_backend = "whisper"
        #  persist the fallback so the next boot
        # doesn't re-try the failed backend and repeat the failure
        # loop.  Previously the config mutation was in-memory only —
        # if the app crashed after fallback, the next boot read the
        # original (failed) backend from disk and re-entered the
        # failure loop on every boot.  Mirrors the persist pattern in
        # ``change_model`` (line 557).
        try:
            self._app.config.save()
        except Exception:
            # Previously missing the ``[MODEL]``
            # topic prefix used by every other log call in this module.
            # Adding it keeps the log topic-consistent so log filters /
            # greps work.
            log.warning("[MODEL] failed to persist fallback config", exc_info=True)
        existing = self._registry.get("whisper")
        if existing is None:
            self._registry.create(
                "whisper",
                whisper_kwargs=dict(
                    model_size="tiny",
                    device=self._app.config.device,
                    language=self._app.config.language,
                    beam_size=self._app.config.beam_size,
                    best_of=self._app.config.best_of,
                    condition_on_previous_text=self._app.config.condition_on_previous_text,
                    # pass live Config so consent check works.
                    config=self._app.config,
                ),
            )
        else:
            existing.model_size = "tiny"
            existing._configured_model_size = "tiny"
            # also backfill the config reference on the
            # existing engine in case it was constructed without one
            # (e.g. by an older code path or a test).  No-op if the
            # engine already has a non-None config.
            if getattr(existing, "config", None) is None:
                existing.config = self._app.config

        def on_progress(msg: str):
            self._app.tray.set_state(AppState.LOADING, msg)

        try:
            success = self._registry.load_with_fallback(progress_callback=on_progress)
        except (ModelNotDownloadedError, ModelIntegrityError) as exc:
            # Whisper tiny isn't downloaded either — surface the
            # actionable message instead of crashing the hotkey thread.
            self._notify_model_load_refused(exc, backend="whisper")
            return
        if success:
            #  PERF-015 LRU eviction — touch the
            # freshly-loaded backend so it's tracked, then evict the
            # LRU model if more than _MAX_LOADED_MODELS are now loaded.
            # Guarded so a tracking failure doesn't break the load.
            try:
                self.touch_model(self._registry.active_name)
                self._evict_lru_model()
            except Exception:
                log.warning(
                    "[PERF] LRU tracking failed (non-fatal)",
                    exc_info=True,
                )
            # Successful load → backend healthy again; clear any
            # deliberate-unload flag so a future genuine failure
            # re-notifies.
            self._clear_deliberately_unloaded(self._registry.active_name)
            active = self._registry.get_active()
            self._app.tray.set_state(
                AppState.IDLE,
                f"Ready -- {active.device_info}" if active else "Ready",
            )
        else:
            self._app.tray.set_state(AppState.ERROR, i18n.t("state.model_manager.load_failed_retry"))
            if notify_on_failure:
                # critical — bypass toggle (model load failed).
                # Use the i18n key so the tray tooltip + OS notification
                # render in the user's selected UI locale, and name the
                # user's ACTUAL configured hotkey in the retry hint.
                self._app.tray.notify_safety(
                    APP_NAME,
                    i18n.t(
                        "notify.model_manager.load_failed_critical",
                        hotkey=notification_hotkey_label(self._app.config.hotkey),
                    ),
                )

    def try_load(self, notify_on_failure: bool = False) -> None:
        """Attempt to load the transcription model.

        008: Delegates to AsrBackendRegistry.load_with_fallback()
        instead of calling self.transcriber.load() directly.

        Prewarm became a worker startup phase (master plan §6.2 P-1):
        the previous ADR-0009 Issue 4 wait-for-prewarm handshake (wait
        for a separate prewarm process to finish, spawn a fresh
        background prewarm on timeout) was removed along with the
        deleted prewarm machinery. Each worker spawn warms the OS
        file cache itself before accepting the first transcription
        request, so there is no separate process to wait for or
        re-spawn here.
        """
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
                #  PERF-015 LRU eviction — touch the
                # freshly-loaded backend so it's tracked, then evict the
                # LRU model if more than _MAX_LOADED_MODELS are now loaded.
                # Guarded so a tracking failure doesn't break the load.
                try:
                    self.touch_model(self._registry.active_name)
                    self._evict_lru_model()
                except Exception:
                    log.warning(
                        "[PERF] LRU tracking failed (non-fatal)",
                        exc_info=True,
                    )
                # Successful load → backend healthy again; clear any
                # deliberate-unload flag so a future genuine failure
                # re-notifies.
                self._deliberately_unloaded.discard(self._registry.active_name)
                active = self._registry.get_active()
                info = getattr(active, "device_info", "unknown") if active else "unknown"
                self._app.tray.set_state(
                    AppState.IDLE,
                    i18n.t("state.model_manager.ready_whisper", device_info=info),
                )
                log.info("[MODEL] Loaded successfully")
            else:
                raise RuntimeError("All backends failed to load")
        except (ModelNotDownloadedError, ModelIntegrityError) as exc:
            _failed_backend = getattr(self._app.config, "asr_backend", "unknown")
            self._notify_model_load_refused(exc, backend=_failed_backend)
        except Exception as e:
            # Include the model name and backend
            # info so the failure is actionable — the user can see which
            # backend and model size failed and retry / switch via Settings.
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
        """Ensure the active backend's engine exists; lazy-init if missing.

        Called from VoiceTyperApp._start_dictation to handle the case
        where the user changed the backend via Electron UI after startup
        (so the engine wasn't created during __init__).

        previously two threads could both pass the
        ``registry.get(backend) is None`` check and each call
        ``_ensure_engine``, creating two engine instances (memory leak
        + double GPU allocation). We guard with a dedicated lock so
        the second caller sees the engine created by the first.

        this is also the reload-after-idle-unload path. When the
        idle-unload timer has fired (``engine.is_loaded == False``),
        this method reloads the SAME backend via
        ``_registry.load_active(progress_callback=...)`` (not Whisper
        fallback). The tray transitions through LOADING "Loading
        model..." → IDLE "Ready -- ..." so the user sees the reload
        latency. After reload, ``touch_model`` re-arms the idle-unload
        timer so the cycle can repeat on the next idle period.

        if the active backend is currently busy (inside
        ``transcribe_with_fallback`` on another thread — e.g. a stuck
        ctranslate2 call that the watchdog hasn't force-recovered yet),
        REJECT this request and mark a pending dictation so the user's
        F2 press is honoured after the watchdog recovers. Returning
        None here causes ``recording_controller.start`` to fall through
        to the ``fallback_to_whisper`` path, which is the safest
        fallback — it loads a separate Whisper backend rather than
        piling up on the stuck backend's ctranslate2 internal lock.

        Returns the active transcriber on success, or None if no engine
        could be created. The caller is responsible for checking
        ``is_loaded`` and calling fallback_to_whisper() if needed.
        """
        # busy-flag rejection. The transcribe thread sets the
        # flag via ``registry.busy_context`` / ``transcribe_with_fallback``
        # (or, when the pipeline adopts the wrapper, automatically); the
        # IPC / hotkey thread reads it here. The check is best-effort —
        # the flag may have been cleared between the read and the
        # subsequent ``_lazy_init_lock`` acquisition — but it
        # short-circuits the common case where the previous
        # transcription is stuck and the user has pressed F2 again. The
        # ``_pending_dictation`` flag ensures the user's F2 press isn't
        # lost: the next ``recording_controller.start`` (after the
        # watchdog recovers) will re-enter this method and the busy
        # check will pass.
        #
        # Strict ``is True`` check (not just truthy): test fixtures that
        # replace the registry with a ``MagicMock`` get a truthy
        # MagicMock from ``is_busy()`` by default — a truthy check
        # would incorrectly reject every dictation in those tests.
        # The real ``AsrBackendRegistry.is_busy`` returns a real
        # ``bool``, so the strict check is safe in production.
        try:
            active_name = self._app.config.asr_backend
            if self._registry.is_busy(active_name) is True:
                log.warning(
                    "[MODEL] Active backend %s is busy (stuck transcription?) — "
                    "rejecting ensure_active_engine_loaded and queuing the "
                    "dictation. The watchdog will force-recover and clear "
                    "the busy flag via force_unload_active().",
                    active_name,
                )
                # Queue the dictation so the user's F2 press is honoured
                # after the watchdog recovers (mirrors the
                # ``_pending_dictation`` semantics in ``load_background``'s
                # finally block).
                self._pending_dictation = True
                return None
        except Exception:
            log.debug(
                "[MODEL] busy-check in ensure_active_engine_loaded failed (non-fatal)",
                exc_info=True,
            )
        # cancel any pending idle-unload timer — the user is
        # actively dictating so the model must NOT be unloaded mid-
        # dictation. This is the canonical "cancel on activity" path.
        self.cancel_idle_unload_timer()
        # race-safe lazy init. The ``backend = config.asr_backend`` read
        # MUST happen INSIDE ``_lazy_init_lock`` (not before it) so a
        # concurrent ``_change_model_blocking`` — which does NOT take
        # ``_lazy_init_lock`` — cannot rewrite ``config.asr_backend``
        # between our read and the lock acquisition (producing a phantom
        # VRAM engine for the stale backend name). The check inside
        # ``_ensure_engine`` is also guarded, but we need to guard the
        # whole check-then-init sequence so two threads don't both create
        # the engine. ``_lazy_init_lock`` is created in __init__
        # (LAZY-INIT-LOCK-FIX).
        with self._lazy_init_lock:
            backend = self._app.config.asr_backend
            engine = self._registry.get(backend)
            if engine is None:
                self._ensure_engine(backend)
                engine = self._registry.get(backend)
                # re-validate ``config.asr_backend`` after
                # ``_ensure_engine``: a concurrent ``_change_model_blocking``
                # may have rewritten it while we constructed the (now
                # phantom) engine for the stale backend name. Re-route to
                # the CURRENT backend so the caller never transcribes
                # against an abandoned backend.
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
            # has been unloaded by the idle-unload timer (is_loaded=False),
            # reload it via load_active so the SAME backend is restored
            # (not silently switched to Whisper fallback). The tray
            # transitions through LOADING "Loading model..." then IDLE
            # "Ready -- ..." so the user sees the reload latency.
            if engine is not None and hasattr(engine, "is_loaded") and not engine.is_loaded:
                self._app.tray.set_state(AppState.LOADING, "Loading model...")

                def on_progress(msg: str) -> None:
                    self._app.tray.set_state(AppState.LOADING, msg)

                # Set the synchronous-load flag so the last-resort
                # subscriber (fired by a concurrent 15s get_status probe)
                # does NOT tell the user to download a model that is
                # literally loading on this thread.
                self._sync_load_in_progress = True
                try:
                    self._registry.load_active(progress_callback=on_progress)
                    # Successful reload → backend healthy; clear any
                    # deliberate-unload flag (set by _do_idle_unload) so
                    # a FUTURE genuine failure re-notifies.
                    self._clear_deliberately_unloaded(backend)
                except Exception:
                    log.warning(
                        "[MODEL] reload after idle-unload failed (non-fatal)",
                        exc_info=True,
                    )
                finally:
                    self._sync_load_in_progress = False
                # Re-arm the idle-unload timer for the next idle period
                # (touch_model only arms when backend == active_name).
                try:
                    self.touch_model(self._registry.active_name)
                except Exception:
                    log.debug("[MODEL] touch_model after reload failed", exc_info=True)
                # Surface the active backend's device info on the tray
                # so the user sees "Ready -- <device>" (matches the
                # set_active_backend success-path tray message).
                try:
                    active = self._registry.get_active()
                    name = self._registry.active_name
                    if active is not None:
                        if name == "whisper":
                            self._app.tray.set_state(AppState.IDLE, f"Ready -- {active.device_info}")
                        else:
                            self._app.tray.set_state(AppState.IDLE, f"Ready -- {name.title()} ASR")
                except Exception:
                    log.debug("[MODEL] tray set_state after reload failed", exc_info=True)
        return self.active_transcriber()
