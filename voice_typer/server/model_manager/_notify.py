"""Load-refusal notifications and suppression gates."""

from __future__ import annotations

import logging

from voice_typer.server import i18n
from voice_typer.server.asr_errors import ModelIntegrityError, ModelNotDownloadedError
from voice_typer.server.branding import APP_NAME
from voice_typer.server.model_registry import NO_MODEL_SIZE
from voice_typer.server.tray_types import AppState

log = logging.getLogger("voice_typer.server.model_manager")


class LastResortNotifyMixin:
    def _notify_model_load_refused(self, exc: Exception, backend: str | None = None) -> str:
        """Surface a model-load refusal (not downloaded / integrity failed).

        The app NEVER downloads models automatically, so a
        ``ModelNotDownloadedError`` (or ``ModelIntegrityError``) at load
        time is a UX signal: tell the user the model isn't on disk and
        point them at the Models page — instead of the generic "model
        load failed" message that implies a transient, retryable error.

        Returns a short human-readable ``failure_reason`` string for
        callers that publish an ``asr_backend_load_failed`` event.
        """
        backend_name = (backend or getattr(self._app.config, "asr_backend", "unknown")).title()
        # Distinguish the two load-refusal flavors so the tray surfaces
        # the message that matches the actual state (and the renderer's
        # Home status pill):
        #  1. ``NO_MODEL_SIZE`` (``model_size == ""``) — genuine "no
        #     model selected": nothing is configured, so tell the user
        #     to pick a model. Uses ``state.model_manager
        #     .no_model_selected``, whose text matches the renderer's
        #     ``home.noModelSelectedHint`` (pushed via ``set_tray_locale``)
        #     so the tray tooltip and the Home hint agree verbatim.
        #  2. A concrete model that is missing from disk — "model not
        #     downloaded": tell the user to download it.
        no_model_selected = isinstance(exc, ModelNotDownloadedError) and exc.model_size == NO_MODEL_SIZE
        if isinstance(exc, ModelIntegrityError):
            reason = i18n.t(
                "state.model_manager.model_integrity_failed",
                backend=backend_name,
            )
        elif no_model_selected:
            reason = i18n.t("state.model_manager.no_model_selected")
        else:
            reason = i18n.t(
                "state.model_manager.model_not_downloaded",
                backend=backend_name,
            )
        log.warning("[MODEL] %s load refused: %s", backend_name, exc)
        try:
            self._app.tray.set_state(AppState.ERROR, reason)
            self._app.tray.notify(
                APP_NAME,
                i18n.t(
                    "notify.model_manager.no_model_selected"
                    if no_model_selected
                    else "notify.model_manager.model_not_downloaded",
                    backend=backend_name,
                ),
            )
        except Exception:
            # A tray failure must never break the load-refusal path.
            log.debug(
                "[MODEL] tray notification for load refusal failed (non-fatal)",
                exc_info=True,
            )
        return reason

    def _model_downloaded_precheck(self) -> bool:
        """Fast filesystem probe: is the active backend's model on disk?

        Returns True (proceed with load) when the config is not a real
        ``Config`` (test doubles — the probe must never read the real
        user's HF cache from a unit test), the backend is cloud/unknown
        (no local model to gate), or the model size is unknown. Only
        definitively-absent LOCAL models are refused. The probe itself
        is TTL-cached (5s) and costs one stat.
        """
        from voice_typer.server.tray_models import is_active_model_downloaded

        return is_active_model_downloaded(self._app.config)

    def _mark_deliberately_unloaded(self, backend_name: str | None) -> None:
        """Record a deliberate unload (idle-unload / force-unload /
        LRU eviction / model change) for ``backend_name``.

        Lazily creates ``_deliberately_unloaded`` so ``__new__``-
        constructed test fixtures (which bypass ``__init__``) that call
        ``_evict_lru_model`` / ``_do_idle_unload`` etc. don't raise
        ``AttributeError`` — mirrors the defensive ``getattr`` pattern
        in ``cancel_idle_unload_timer``.
        """
        if not backend_name:
            return
        s = getattr(self, "_deliberately_unloaded", None)
        if s is None:
            s = self._deliberately_unloaded = set()
        s.add(backend_name)

    def _clear_deliberately_unloaded(self, backend_name: str | None) -> None:
        """Clear the deliberate-unload flag after a successful load."""
        s = getattr(self, "_deliberately_unloaded", None)
        if s is not None and backend_name:
            s.discard(backend_name)

    def _was_deliberately_unloaded(self, backend_name: str) -> bool:
        """Return True if ``backend_name`` was deliberately unloaded
        this session (idle-unload / force-unload / LRU eviction /
        model change)."""
        s = getattr(self, "_deliberately_unloaded", None)
        return bool(s) and backend_name in s

    def _model_load_in_progress(self) -> bool:
        """Return True while a background load / model-change /
        backend-change thread is alive.

        During these windows the backend is REGISTERED but not yet
        loaded (``is_loaded=False``), so a ``get_active`` last-resort
        fall-through is a false positive — the model is about to be
        ready, not broken. The last-resort tray notification must be
        suppressed until the load settles.
        """
        for thread in (
            self._model_load_thread,
            self._model_change_thread,
            self._backend_change_thread,
        ):
            if thread is not None and thread.is_alive():
                return True
        return bool(self._sync_load_in_progress)

    def _in_deliberate_unload_window(self, backend_name: str) -> bool:
        """Return True while the app is in a deliberate-unload window for
        ``backend_name``: the app is shutting down, a load / model-change /
        backend-change thread is alive (or a synchronous load is
        running), or the backend was deliberately unloaded this session.

        Shared by ``_should_suppress_last_resort_notification`` and
        ``_should_suppress_backend_disabled_notification`` so BOTH
        event surfaces (the ``asr_last_resort_unloaded`` tray/toast and
        the ``asr_backend_disabled`` event) suppress during the same
        windows — a backend that is mid-switch or deliberately released
        is not "broken", so neither surface should alert.
        """
        if getattr(self._app, "_shutting_down", False):
            return True
        if self._model_load_in_progress():
            return True
        return self._was_deliberately_unloaded(backend_name)

    def _should_suppress_backend_disabled_notification(self, backend_name: str) -> bool:
        """Return True when the ``asr_backend_disabled`` alert must be
        suppressed for ``backend_name``.

        Wired as the breaker's backend-disabled event gate in
        ``__init__`` so the ``asr_backend_disabled`` event_bus publish
        (consumed by the renderer) matches the last-resort suppression
        windows — during a deliberate unload (idle-unload / force-unload /
        LRU eviction / model change) or while a load is in progress, a
        transient load failure can trip the breaker and would otherwise
        publish a spurious "backend disabled" event telling the user the
        backend is permanently broken when the app is merely switching.

        Unlike ``_should_suppress_last_resort_notification`` this has NO
        cooldown rate limit: the disabled event fires at most once per
        breaker trip (``_record_failure`` skips already-disabled
        backends), so there is nothing to spam. The state mutation
        (disabling the backend in the breaker) is intentionally NOT
        gated — only the notification surface.
        """
        if self._in_deliberate_unload_window(backend_name):
            log.debug(
                "[MODEL] backend-disabled %s suppressed (deliberate-unload window)",
                backend_name,
            )
            return True
        return False

    def _should_suppress_last_resort_notification(self, backend_name: str) -> bool:
        """Return True when the last-resort alert must be suppressed for
        ``backend_name``.

        Shared by the tray notification path
        (``_on_last_resort_unloaded``) and the event_bus suppression
        gate (wired in ``__init__``) so the renderer toast that consumes
        the ``asr_last_resort_unloaded`` event matches the tray
        notification's suppressions exactly — the toast cannot see these
        ModelManager-side checks otherwise.

        Suppressed when:
        * the app is shutting down (tray may be torn down);
        * a load / model-change / backend-change thread is alive (or a
          synchronous load is running) — the backend is registered but
          about to load, not actually broken;
        * the backend was deliberately unloaded this session
          (idle-unload / force-unload / LRU eviction / model change) —
          the model IS on disk, a download nudge would be wrong;
        * the same backend was notified within
          ``_LAST_RESORT_NOTIFY_COOLDOWN_SECS`` (the registry latch
          can be reset by the 15s get_status probe, so this rate
          limit stops both surfaces from being spammed).

        The first three checks are delegated to
        :meth:`_in_deliberate_unload_window` (shared with the
        backend-disabled gate so both surfaces suppress during the same
        windows); the cooldown is last-resort-only.

        NOTE: the cooldown read here is read-only — the timestamp is
        recorded by the tray subscriber after a non-suppressed
        transition (``_on_last_resort_unloaded``), so the two callers
        can't double-record.
        """
        if self._in_deliberate_unload_window(backend_name):
            if self._was_deliberately_unloaded(backend_name):
                log.debug(
                    "[MODEL] last-resort unloaded %s suppressed (deliberate unload — model on disk)",
                    backend_name,
                )
            return True
        import time

        now = time.monotonic()
        # ``None`` sentinel (not ``0.0``): monotonic() on Windows is
        # seconds since boot, so ``now - 0.0`` could already exceed the
        # cooldown and let a REPEAT through (or, on a fresh boot,
        # suppress the FIRST notification). Only a real timestamp
        # (i.e. "this backend was notified before") engages the limit.
        # Defensive ``getattr`` (mirrors ``_mark_deliberately_unloaded``)
        # so a ``__new__``-constructed fixture (which bypasses
        # ``__init__``) that somehow reaches this helper doesn't raise
        # AttributeError inside the subscriber/gate path.
        last = getattr(self, "_last_resort_notified_at", {}).get(backend_name)
        return last is not None and now - last < self._LAST_RESORT_NOTIFY_COOLDOWN_SECS

    def _on_last_resort_unloaded(self, backend_name: str) -> None:
        """Show a tray notification when ``get_active()`` falls through
        to an unloaded last-resort backend.

        Wired as the production ``on_last_resort`` subscriber in
        ``__init__`` (the subscriber set existed but was never wired —
        the documented tray notification was dead code). Without this,
        an unloaded backend (e.g. the model failed to load / is not
        downloaded) silently returns empty transcriptions with no
        visible feedback — exactly the ``transcription may return empty
        silently`` WARN the user sees every 15s.

        Always points the user at the Models page with the download
        instruction (the app never auto-downloads models). When a host
        (Electron/Tauri) is connected, the notification is published as a
        ``notification`` event carrying ``click_path: "/models"`` so the
        host renders a CLICKABLE native toast that opens the Models page
        on click (pystray Win32 balloons cannot carry click handlers);
        without a live transport the pystray balloon fallback is used.

        Suppression is delegated to
        :meth:`_should_suppress_last_resort_notification` (shared with
        the event_bus gate so the renderer toast matches the tray).
        """
        if self._should_suppress_last_resort_notification(backend_name):
            return
        import time

        # Record the cooldown timestamp here (the alert is about to be
        # shown); the shared helper's cooldown read stays read-only so
        # the two callers can't double-record.
        self._last_resort_notified_at[backend_name] = time.monotonic()
        # Respect the user's notifications toggle (mirrors
        # ``tray_notifications.notify`` — the event-bus path below must
        # not bypass it).
        if not getattr(self._app.tray, "_notifications_enabled", True):
            return
        message = i18n.t(
            "notify.model_manager.last_resort_unloaded",
            backend=backend_name,
        )
        # Prefer a CLICKABLE host notification: when an Electron (or
        # Tauri) host is connected, publish a ``notification`` event with
        # a ``click_path`` so the host renders a native toast whose click
        # opens the Models page directly (the Electron main-process
        # ``notification`` push handler wires ``Notification.on("click")``
        # → show window + broadcast ``navigate /models``). pystray Win32
        # balloons (the ``tray.notify`` fallback) cannot carry a click
        # handler, so when no live transport exists (standalone backend)
        # fall back to the pystray balloon.
        from voice_typer.server import event_bus

        live = False
        try:
            live = event_bus.has_live_transport()
        except Exception:
            live = False
        if live:
            try:
                # NOTE: ``event_bus.publish`` returning True only means an
                # in-process subscriber accepted the event — it does NOT
                # prove the host received it (the TCP transport buffers
                # to ``_pending_tcp`` and marks the client dead on write
                # failure instead of raising). If the host dies between
                # the ``has_live_transport`` probe and the publish, the
                # toast is silently dropped with no balloon fallback.
                # Acceptable (single-host window is microseconds wide);
                # the debug log below keeps the drop diagnosable.
                ok = event_bus.publish(
                    {
                        "type": "notification",
                        "data": {
                            "title": APP_NAME,
                            "message": message,
                            "duration_ms": 0,
                            "critical": False,
                            "click_path": "/models",
                        },
                    }
                )
                if not ok:
                    log.debug(
                        "[MODEL] last-resort notification event publish returned False (no subscriber accepted)",
                    )
                return
            except Exception:
                log.debug(
                    "[MODEL] last-resort notification event publish failed — falling back to tray balloon",
                    exc_info=True,
                )
        try:
            self._app.tray.notify(APP_NAME, message)
        except Exception:
            # A tray failure must never break the last-resort path.
            log.debug(
                "[MODEL] last-resort tray notification failed (non-fatal)",
                exc_info=True,
            )
