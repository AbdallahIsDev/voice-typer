"""LRU eviction, idle-unload scheduler (persistent deadline thread), watchdog escalation."""

from __future__ import annotations

import logging
import threading
import time

from voice_typer.server.tray_types import AppState

log = logging.getLogger("voice_typer.server.model_manager")


class LifecycleMixin:
    # Persistent idle-unload scheduler state (RACE-013 pattern, mirroring
    _idle_unload_deadline: float | None
    _idle_unload_wakeup: threading.Event
    _idle_unload_thread: threading.Thread | None
    # Created eagerly in ``ModelManagerCore.__init__`` (``_base.py``);
    _idle_unload_lock: threading.Lock

    def _evict_lru_model(self) -> None:
        """PERF-015: Evict the least recently used model if too many are loaded."""
        with self._model_lru_lock:
            if len(self._model_access_times) <= self._MAX_LOADED_MODELS:
                return

            # Find the oldest (least recently used) backend.
            oldest_backend = min(
                self._model_access_times,
                key=lambda k: self._model_access_times.get(k, 0.0),
            )
            oldest_time = self._model_access_times[oldest_backend]
            log.info(
                "[PERF] Evicting LRU model '%s' (last used %.1fs ago), %d models loaded, max is %d",
                oldest_backend,
                time.monotonic() - oldest_time,
                len(self._model_access_times),
                self._MAX_LOADED_MODELS,
            )

            # Unload the engine via the registry so the busy-check
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
            # Deliberate unload, the evicted model was loaded and is
            self._mark_deliberately_unloaded(oldest_backend)
            # Unregister the backend so a subsequent ``_ensure_engine``
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
        """PERF-015: Update the last-access timestamp for a model backend."""
        import time

        with self._model_lru_lock:
            self._model_access_times[backend_name] = time.monotonic()
        # arm the idle-unload deadline only when the touched
        try:
            active_name = self._registry.active_name
        except Exception:
            active_name = None
        if backend_name == active_name:
            self._schedule_idle_unload_timer()

    def touch_active_model(self) -> None:
        """PERF-015: refresh the LRU timestamp for the active backend."""
        try:
            self.touch_model(self._registry.active_name)
        except Exception:
            log.warning(
                "[PERF] touch_active_model failed (non-fatal)",
                exc_info=True,
            )

    def cancel_idle_unload_timer(self) -> None:
        """disarm the idle-unload deadline (idempotent)."""
        lock = getattr(self, "_idle_unload_lock", None)
        if lock is None:
            return
        with lock:
            self._idle_unload_deadline = None
            wake = getattr(self, "_idle_unload_wakeup", None)
        if wake is not None:
            # Wake the parked/waiting loop so it observes the disarmed
            wake.set()

    def _schedule_idle_unload_timer(self) -> None:
        """RACE-013 pattern (mirrors the transcription watchdog): ONE"""
        lock = getattr(self, "_idle_unload_lock", None)
        if lock is None:
            return
        try:
            minutes = getattr(self._app.config, "model_idle_unload_minutes", 0)
        except Exception:
            minutes = 0
        if not isinstance(minutes, int | float) or minutes <= 0:
            # Feature disabled, disarm any pending deadline.
            self.cancel_idle_unload_timer()
            return
        delay = float(minutes) * 60.0
        with lock:
            # Push the deadline out to N minutes after THIS touch (not
            self._idle_unload_deadline = time.monotonic() + delay
            wake = getattr(self, "_idle_unload_wakeup", None)
            if wake is None:
                wake = threading.Event()
                self._idle_unload_wakeup = wake
            # Read-check-create-start under the lock (mirrors the
            thread = getattr(self, "_idle_unload_thread", None)
            if thread is None or not thread.is_alive():
                thread = threading.Thread(
                    target=self._idle_unload_loop,
                    name="model-idle-unload",
                    daemon=True,
                )
                self._idle_unload_thread = thread
                thread.start()
            wake.set()

    def _idle_unload_loop(self) -> None:
        """Mirrors the transcription watchdog's RACE-013 persistent-thread"""
        wake = self._idle_unload_wakeup
        lock = self._idle_unload_lock
        while True:
            wake.clear()
            with lock:
                deadline = self._idle_unload_deadline
            if deadline is None:
                # Disarmed, park until the next arm/cancel wakes us.
                wake.wait()
                continue
            remaining = deadline - time.monotonic()
            if remaining > 0 and wake.wait(timeout=remaining):
                # Woken early, a touch or cancel moved the deadline;
                continue
            # Deadline reached (or already past). Re-confirm under the
            with lock:
                deadline_now = self._idle_unload_deadline
                if deadline_now is None or deadline_now > time.monotonic():
                    continue
                # Disarm BEFORE unloading so the parked state stays
                self._idle_unload_deadline = None
            try:
                self._do_idle_unload()
            except Exception:
                # The loop must survive an unload failure, a dead
                log.error(
                    "[MODEL] idle-unload raised, scheduler continues",
                    exc_info=True,
                )
            # Loop back: deadline is None → park until the next touch.

    def _do_idle_unload(self) -> None:
        """unload the active backend + release GPU memory."""
        # Skip if shutting down, don't race with teardown.
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
        # Deliberate unload, the model IS on disk, so the last-resort
        self._mark_deliberately_unloaded(active_name)
        # Unload via the registry (which calls engine.unload() on the
        try:
            self._registry.unload(active_name)
        except Exception:
            log.warning(
                "[MODEL] registry.unload(%s) failed (non-fatal)",
                active_name,
                exc_info=True,
            )
        # Defense in depth: also call engine.unload() directly in case
        try:
            if hasattr(engine, "unload"):
                engine.unload()
        except Exception:
            log.debug(
                "[MODEL] engine.unload() failed (non-fatal)",
                exc_info=True,
            )
        # Release GPU memory (CUDA caching allocator blocks). Defense
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
        # Tray state transition: "Idle, model unloaded" (reuses
        try:
            self._app.tray.set_state(AppState.IDLE, "Idle, model unloaded")
        except Exception:
            log.debug(
                "[MODEL] tray.set_state failed (non-fatal)",
                exc_info=True,
            )

    def force_unload_active(self) -> None:
        """state reset (``_busy_event.set()``, tray to IDLE) the"""
        try:
            active_name = self._registry.active_name
        except Exception:
            active_name = None
        log.warning(
            "[MODEL] force_unload_active: ejecting active backend %r (watchdog escalation after stuck transcription)",
            active_name,
        )
        # Deliberate unload, the model IS on disk (it was loaded and
        self._mark_deliberately_unloaded(active_name)
        # Drop the registry slot WITHOUT calling unload() on the engine
        try:
            self._registry.unregister(active_name)
        except Exception:
            log.warning(
                "[MODEL] registry.unregister(%r) failed (non-fatal)",
                active_name,
                exc_info=True,
            )
        # Release GPU memory (CUDA caching allocator blocks). Free
        try:
            from voice_typer.server.asr_utils import release_gpu_memory

            release_gpu_memory()
        except Exception:
            log.debug(
                "[MODEL] release_gpu_memory() failed (non-fatal)",
                exc_info=True,
            )
        # Clear the busy flag so the next ensure_active_engine_loaded
        try:
            self._registry.force_clear_busy(active_name)
        except Exception:
            log.debug(
                "[MODEL] force_clear_busy(%s) failed (non-fatal)",
                active_name,
                exc_info=True,
            )
