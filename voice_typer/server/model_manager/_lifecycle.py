"""LRU eviction, idle-unload timer, watchdog escalation."""

from __future__ import annotations

import logging
import threading
import time

from voice_typer.server.tray_types import AppState

log = logging.getLogger("voice_typer.server.model_manager")


class LifecycleMixin:
    def _evict_lru_model(self) -> None:
        """PERF-015: Evict the least recently used model if too many are loaded.

        When more than ``_MAX_LOADED_MODELS`` models are loaded concurrently,
        unloads the least recently used one to prevent GPU OOM. Called after
        loading a new model.

        The LRU is based on ``_model_access_times`` which is updated each
        time a model is used for transcription.
        """
        with self._model_lru_lock:
            if len(self._model_access_times) <= self._MAX_LOADED_MODELS:
                return

            # Find the oldest (least recently used) backend.
            #  (pyrefly): use a lambda instead of passing
            # ``self._model_access_times.get`` directly. ``dict.get``
            # is typed as returning ``float | None`` (because callers
            # may pass a key that isn't present), but ``min(key=...)``
            # requires a function returning ``SupportsRichComparison``
            # — pyrefly rejects ``None`` as not orderable. The lambda
            # resolves this by giving an explicit ``0.0`` default that
            # can never actually be returned here (every key in
            # ``_model_access_times`` has a real ``float`` value), but
            # satisfies the type system without changing behaviour.
            oldest_backend = min(
                self._model_access_times,
                key=lambda k: self._model_access_times.get(k, 0.0),
            )
            oldest_time = self._model_access_times[oldest_backend]
            log.info(
                "[PERF] Evicting LRU model '%s' (last used %.1fs ago) — %d models loaded, max is %d",
                oldest_backend,
                time.monotonic() - oldest_time,
                len(self._model_access_times),
                self._MAX_LOADED_MODELS,
            )

            # Unload the engine via the registry so the busy-check
            # (``AsrBackendRegistry.unload`` refuses to unload a backend
            # currently inside ``transcribe_with_fallback``) is honoured.
            # Mirrors ``_do_idle_unload`` (lines ~1972-1979): if the
            # backend is busy, log + skip eviction rather than tearing
            # down a backend mid-transcription (which would crash the
            # C-level ctranslate2 / torch call with a use-after-free).
            try:
                self._registry.unload(oldest_backend)
            except RuntimeError as busy_exc:
                log.warning(
                    "[PERF] Skipping LRU eviction of busy backend '%s': %s",
                    oldest_backend,
                    busy_exc,
                )
                return
            except Exception as exc:
                log.warning(
                    "[PERF] registry.unload('%s') failed (non-fatal): %s",
                    oldest_backend,
                    exc,
                    exc_info=True,
                )
            # Deliberate unload — the evicted model was loaded and is
            # on disk; the last-resort tray notification must NOT tell
            # the user to download it. Marked AFTER the busy-check so a
            # skipped eviction (busy backend) does not leave a stale
            # flag for a backend that was never actually unloaded.
            self._mark_deliberately_unloaded(oldest_backend)
            # Unregister the backend so a subsequent ``_ensure_engine``
            # actually constructs a fresh one. Previously this path
            # called ``engine.unload()`` directly and left the backend
            # in the registry, so ``_ensure_engine``'s
            # "if registry.get(name) is not None: return" short-circuited
            # and a stale (unloaded) handle was returned. Mirrors
            # ``_change_model_unload_phase`` (lines ~990-996).
            try:
                self._registry.unregister(oldest_backend)
            except Exception as exc:
                log.warning(
                    "[PERF] registry.unregister('%s') failed (non-fatal): %s",
                    oldest_backend,
                    exc,
                    exc_info=True,
                )
            # Release GPU memory (CUDA caching allocator blocks).
            # Defense in depth — mirrors ``_do_idle_unload``
            # (lines ~1996-1998) and ``force_unload_active``
            # (lines ~2115-2117): without this, the freed CUDA tensors
            # stay in PyTorch's caching allocator and the VRAM is not
            # actually returned to the OS, defeating the eviction's
            # goal of preventing GPU OOM.
            try:
                from voice_typer.server.asr_utils import release_gpu_memory

                release_gpu_memory()
            except Exception:
                log.debug(
                    "[PERF] release_gpu_memory() failed (non-fatal)",
                    exc_info=True,
                )

            # Remove from tracking
            del self._model_access_times[oldest_backend]

    def touch_model(self, backend_name: str) -> None:
        """PERF-015: Update the last-access timestamp for a model backend.

        Called when a model is used for transcription so the LRU eviction
        knows which models are actively being used.

        if the touched backend is the ACTIVE backend AND
        ``model_idle_unload_minutes > 0``, (re)arm the idle-unload
        timer. Touching an inactive backend (e.g. via ``touch_model``
        on a non-active name during a load path) does NOT arm the
        timer — the timer is only for the active backend.
        """
        import time

        with self._model_lru_lock:
            self._model_access_times[backend_name] = time.monotonic()
        # arm the idle-unload timer only when the touched
        # backend is the active one (the timer exists to release the
        # ACTIVE backend's VRAM after inactivity). Touching a non-active
        # backend (e.g. during a registry-level pre-warm) is harmless
        # but should not arm the timer.
        try:
            active_name = self._registry.active_name
        except Exception:
            active_name = None
        if backend_name == active_name:
            self._schedule_idle_unload_timer()

    def touch_active_model(self) -> None:
        """PERF-015: refresh the LRU timestamp for the active backend.

        Public entry point intended to be called from
        :meth:`voice_typer.server.dictation_pipeline.DictationPipeline._transcribe`
        after every successful ``transcribe()`` so the LRU eviction
        knows the active backend is in use (and therefore should NOT be
        the one evicted on the next ``load_active`` / ``load_with_fallback``).

        Wired into ``DictationPipeline._transcribe``
        (``voice_typer/server/dictation_pipeline.py:636``) — called after
        every successful ``transcribe()`` so the LRU tracking added in
         (``touch_model`` after load) has a matching "after
        transcribe" entry point.

        Safe to call when no backend is active — ``touch_model`` is a
        no-op for unknown backend names (it just records the timestamp;
        eviction only considers names that were touched).

        also (re)arms the idle-unload timer via the
        ``touch_model`` → ``_schedule_idle_unload_timer`` path when the
        active backend is touched (i.e. after every successful
        transcribe the timer is pushed out by N minutes).
        """
        try:
            self.touch_model(self._registry.active_name)
        except Exception:
            log.warning(
                "[PERF] touch_active_model failed (non-fatal)",
                exc_info=True,
            )

    # ── : idle-unload timer ───────────────────────────────────────

    def cancel_idle_unload_timer(self) -> None:
        """cancel any pending idle-unload timer (idempotent).

        Safe to call when no timer is armed (no-op). Called from:
        - ``ensure_active_engine_loaded`` (user pressed toggle_dictation)
        - ``change_model`` / ``set_active_backend`` (model swap)
        - ``_schedule_idle_unload_timer`` (reschedule-on-touch)
        - ``app.shutdown`` paths (best-effort via the registry's
          stop_event — the daemon Timer is killed implicitly by
          process exit too).

        Defensive against test fixtures that construct
        ``ModelManager.__new__(ModelManager)`` and bypass ``__init__``
        (so ``_idle_unload_lock`` / ``_idle_unload_timer`` may not be
        set). In that case the method is a no-op (there is no timer to
        cancel — the fixture never armed one).
        """
        lock = getattr(self, "_idle_unload_lock", None)
        if lock is None:
            return
        with lock:
            timer = self._idle_unload_timer
            self._idle_unload_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                log.debug(
                    "[MODEL] timer.cancel() failed (non-fatal)",
                    exc_info=True,
                )

    def _schedule_idle_unload_timer(self) -> None:
        """arm (or re-arm) the idle-unload timer.

        Reads ``app.config.model_idle_unload_minutes``:
        - ``0`` (default) → feature disabled; cancel any existing timer
          and return (current behaviour preserved exactly).
        - ``N > 0`` → cancel any existing timer, then arm a new
          ``threading.Timer(N * 60.0, _on_idle_unload_fire)``.

        Each call cancels the previous timer so the deadline is pushed
        out to N minutes after the most recent touch (the "use it or
        lose it" pattern). The timer is a daemon thread so it never
        blocks process exit.

        Defensive against test fixtures that bypass ``__init__`` — if
        ``_idle_unload_lock`` is missing, the method is a no-op (the
        fixture never set ``model_idle_unload_minutes`` either, so the
        feature would be disabled anyway).
        """
        lock = getattr(self, "_idle_unload_lock", None)
        if lock is None:
            return
        try:
            minutes = getattr(self._app.config, "model_idle_unload_minutes", 0)
        except Exception:
            minutes = 0
        if not isinstance(minutes, int | float) or minutes <= 0:
            # Feature disabled — cancel any existing timer and return.
            self.cancel_idle_unload_timer()
            return
        # Cancel any existing timer first so the deadline is pushed out
        # to N minutes after THIS touch (not the previous one).
        self.cancel_idle_unload_timer()
        delay = float(minutes) * 60.0
        # Create the Timer with a placeholder callback, then override
        # ``timer.function`` with a closure that captures ``timer`` so
        # ``_on_idle_unload_fire`` can do the identity check (only the
        # CURRENT timer's callback actually unloads — a cancelled /
        # rescheduled timer's callback aborts).
        timer = threading.Timer(delay, lambda: None)
        timer.daemon = True
        timer.name = "TY-11-idle-unload"

        def _fire() -> None:
            self._on_idle_unload_fire(timer)

        # Override the Timer's stored function so the run() method
        # invokes our closure (which captures ``timer`` for the
        # identity check). The default ``timer.function`` is the
        # ``lambda: None`` placeholder above; replacing it post-init is
        # safe because ``Timer.run`` reads ``self.function`` at fire
        # time, not at construction.
        timer.function = _fire
        with lock:
            self._idle_unload_timer = timer
        timer.start()

    def _on_idle_unload_fire(self, timer: threading.Timer) -> None:
        """timer callback — identity-check then delegate to ``_do_idle_unload``.

        The identity check ensures only the CURRENT timer's callback
        actually unloads. If the timer was cancelled or rescheduled
        (replaced by a new Timer) before this callback ran, the check
        fails and the callback returns without unloading. This prevents
        the race where an old timer fires after a new one was scheduled
        (which would unload the model right after the user started a
        new dictation).
        """
        with self._idle_unload_lock:
            current = self._idle_unload_timer
        if current is not timer:
            log.debug("[MODEL] idle-unload timer callback aborted (timer no longer current)")
            return
        self._do_idle_unload()
        # Clear the timer reference (the callback has run; the timer is
        # dead). The next ``touch_active_model`` will arm a fresh timer.
        with self._idle_unload_lock:
            if self._idle_unload_timer is timer:
                self._idle_unload_timer = None

    def _do_idle_unload(self) -> None:
        """unload the active backend + release GPU memory.

        Called from ``_on_idle_unload_fire`` (the timer's callback) and
        directly from tests. Skips the unload if:
        - ``app._shutting_down`` is True (avoids racing with teardown).
        - the active engine is already unloaded (no double-unload).

        After unloading, calls ``release_gpu_memory()`` to release
        PyTorch's CUDA caching allocator blocks back to the OS, then
        sets the tray state to ``AppState.IDLE`` with the
        "Idle — model unloaded" message so the user sees the tray
        transition. The model is reloaded on the next
        ``toggle_dictation`` via ``ensure_active_engine_loaded``.
        """
        # Skip if shutting down — don't race with teardown.
        if getattr(self._app, "_shutting_down", False):
            log.debug("[MODEL] idle-unload skipped (app shutting down)")
            return
        try:
            active_name = self._registry.active_name
        except Exception:
            active_name = None
        engine = self._registry.get_active()
        # Skip if no active engine (nothing to unload).
        if engine is None:
            log.debug("[MODEL] idle-unload skipped (no active engine)")
            return
        # Skip if already unloaded (no double-unload).
        if hasattr(engine, "is_loaded") and not engine.is_loaded:
            log.debug("[MODEL] idle-unload skipped (engine already unloaded)")
            return
        log.info(
            "[MODEL] idle-unload: unloading active backend '%s' after idle period",
            active_name,
        )
        # Deliberate unload — the model IS on disk, so the last-resort
        # tray notification ("open the Models page and download") must
        # NOT fire for it. Record BEFORE the unload so a get_active
        # last-resort fall-through during the unload is also suppressed.
        self._mark_deliberately_unloaded(active_name)
        # Unload via the registry (which calls engine.unload() on the
        # registered backend).
        try:
            self._registry.unload(active_name)
        except Exception:
            log.warning(
                "[MODEL] registry.unload(%s) failed (non-fatal)",
                active_name,
                exc_info=True,
            )
        # Defense in depth: also call engine.unload() directly in case
        # registry.unload only clears the registration without calling
        # the engine's unload (the contract varies by registry impl).
        try:
            if hasattr(engine, "unload"):
                engine.unload()
        except Exception:
            log.debug(
                "[MODEL] engine.unload() failed (non-fatal)",
                exc_info=True,
            )
        # Release GPU memory (CUDA caching allocator blocks). Defense
        # in depth — parakeet_engine.unload() also calls this, but the
        # ModelManager calls it explicitly too so a registry impl that
        # doesn't propagate unload still releases VRAM.
        try:
            from voice_typer.server.asr_utils import release_gpu_memory

            release_gpu_memory()
            try:
                from voice_typer.server import vad

                vad.unload()
            except Exception:
                log.debug("[MODEL] vad.unload() failed (non-fatal)", exc_info=True)
        except Exception:
            log.debug(
                "[MODEL] release_gpu_memory() failed (non-fatal)",
                exc_info=True,
            )
        # Tray state transition: "Idle — model unloaded" (reuses
        # AppState.IDLE per the  constraint of not touching
        # tray.py / tray_types.py — no new enum value).
        try:
            self._app.tray.set_state(AppState.IDLE, "Idle — model unloaded")
        except Exception:
            log.debug(
                "[MODEL] tray.set_state failed (non-fatal)",
                exc_info=True,
            )

    # ── : force-unload the active backend (watchdog escalation) ──

    def force_unload_active(self) -> None:
        """Eject the active backend from the registry + clear its busy flag.

        Called by the watchdog
        (meth:`voice_typer.server.transcription_watchdog.TranscriptionWatchdog.force_recover`)
        when a forced recovery fires while the worker thread is still
        alive inside the engine's C-level call. The watchdog cannot
        interrupt that call, so the engine object itself must NOT be
        destroyed here: freeing CUDA tensors / ctranslate2 handles under
        a live call crashes with use-after-free (the same hazard
        :meth:`AsrBackendRegistry.unload` busy-guards against). Instead,
        this method DROPS the backend's registry slot so the next
        ``_ensure_engine`` constructs a FRESH engine instance; the stuck
        thread keeps its orphaned reference and its late result stays
        fenced downstream by the cancelled-cycle registry.

        Contract:

        * **Idempotent.** Safe to call multiple times — each call is a
          no-op once the slot is dropped.

        * **Best-effort.** Catches every exception, logs a warning, and
          NEVER raises. The watchdog calls this from its own
          force-recover path; a raise here would mask the recovery
          state reset (``_busy_event.set()``, tray to IDLE) the
          watchdog has already performed.

        * **Drops the registration without destroying the engine.**
          ``registry.unregister(active_name)`` deletes the
          ``_backends[name]`` slot (mirroring ``_evict_lru_model`` /
          ``_change_model_unload_phase``) so a subsequent
          ``get``/``get_active`` cannot hand out the stuck instance and
          ``_ensure_engine``'s "already registered" short-circuit does
          not reuse it. Unlike ``registry.unload``, this bypasses the
          busy guard deliberately — the busy flag here means "a thread
          is trapped inside the object", which is exactly why the object
          must be orphaned, not freed.

        * **Releases GPU cache.** ``release_gpu_memory()`` returns
          PyTorch's free caching-allocator blocks to the OS (allocated
          tensors of the orphaned engine are untouched until GC reclaims
          it — matches the LRU-eviction / idle-unload defence-in-depth).

        * **Clears the busy flag.** Calls
          :meth:`AsrBackendRegistry.force_clear_busy` so the next
          :meth:`ensure_active_engine_loaded` call isn't rejected by
          the  busy-check. Without this, the busy flag would
          remain set forever (the stuck transcription never returned
          to clear it) and every subsequent dictation would be
          rejected + queued.

        * **Does NOT touch ``config.asr_backend``.** The next
          :meth:`ensure_active_engine_loaded` call (from
          ``recording_controller.start``) will re-create + re-load
          the same backend via ``_ensure_engine`` + ``load_active``.
          The watchdog's contract is "eject the stuck model so the
          next dictation can load a fresh one", not "switch to a
          different backend".

        * **Does NOT call ``tray.set_state``.** The watchdog has
          already set the tray to ``AppState.IDLE`` with the
          "recovered" message — we don't want to overwrite that with
          the  "Idle — model unloaded" message (which would
          confuse the user, since the recovery message is more
          specific).
        """
        try:
            active_name = self._registry.active_name
        except Exception:
            active_name = None
        log.warning(
            "[MODEL] force_unload_active: ejecting active backend %r (watchdog escalation after stuck transcription)",
            active_name,
        )
        # Deliberate unload — the model IS on disk (it was loaded and
        # got stuck); the last-resort tray notification must NOT tell
        # the user to download it.
        self._mark_deliberately_unloaded(active_name)
        # Drop the registry slot WITHOUT calling unload() on the engine
        # object — the stuck worker may still be inside its C-level
        # call, and destroying the object under it would crash with
        # use-after-free. The next _ensure_engine constructs a fresh
        # instance because the slot is gone.
        try:
            self._registry.unregister(active_name)
        except Exception:
            log.warning(
                "[MODEL] registry.unregister(%r) failed (non-fatal)",
                active_name,
                exc_info=True,
            )
        # Release GPU memory (CUDA caching allocator blocks). Free
        # cached blocks only — allocated tensors of the orphaned engine
        # stay valid for the stuck thread.
        try:
            from voice_typer.server.asr_utils import release_gpu_memory

            release_gpu_memory()
        except Exception:
            log.debug(
                "[MODEL] release_gpu_memory() failed (non-fatal)",
                exc_info=True,
            )
        # Clear the busy flag so the next ensure_active_engine_loaded
        # isn't rejected by the  busy-check. Without this, the
        # busy flag would remain set forever (the stuck transcription
        # never returned to clear it) and every subsequent dictation
        # would be rejected + queued indefinitely.
        try:
            self._registry.force_clear_busy(active_name)
        except Exception:
            log.debug(
                "[MODEL] force_clear_busy(%s) failed (non-fatal)",
                active_name,
                exc_info=True,
            )
