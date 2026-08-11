"""regression tests for the production last-resort tray notification.

Pre-fix, ``AsrBackendRegistry.get_active()``'s last-resort branch (the
``for b in list(self._backends.values())`` loop) returned an *unloaded*
backend when no ready backend was available and fired the
``on_last_resort`` subscriber set + an ``asr_last_resort_unloaded``
event — but NO production subscriber was ever wired. The documented
tray notification was dead code: the user got zero visible feedback
that voice recognition wasn't working (transcription silently returns
empty), only a WARN log line repeating every 15s:

    [ASR_REGISTRY] returning unloaded backend <name> (is_loaded=False)
    as last-resort active — transcription may return empty silently

Post-fix, ``ModelManager.__init__`` wires ``_on_last_resort_unloaded``
as the production subscriber. When ``get_active()`` falls through to an
unloaded backend, a tray notification is shown that ALWAYS points the
user at the Models page with the download instruction (the app never
auto-downloads models).

Suppression logic (so the notification is accurate, not noise):

* shutting down — tray may be torn down;
* a load / model-change / backend-change thread is alive (the backend
  is registered but about to load — not actually broken);
* the backend was deliberately unloaded this session (idle-unload /
  force-unload / LRU eviction / model change) — the model IS on disk,
  a download nudge would be wrong;
* the same backend was notified within
  ``_LAST_RESORT_NOTIFY_COOLDOWN_SECS`` (the 15s get_status probe can
  reset the registry's one-shot latch, so the ModelManager-side rate
  limit stops the tray from being spammed).

These tests verify:
  - ``ModelManager.__init__`` wires the subscriber onto the real
    registry.
  - A last-resort fall-through shows a tray notification whose message
    contains the backend name, "Models page", and the download
    instruction.
  - The notification is suppressed while a load thread is alive.
  - The notification is suppressed while the app is shutting down.
  - A deliberately-unloaded backend (idle-unload / force-unload / LRU
    eviction / model change) does NOT re-trigger a download nudge.
  - A successful load clears the deliberate-unload flag so a FUTURE
    genuine failure re-notifies.
  - The per-backend rate limit suppresses repeat notifications when the
    registry latch resets (15s probe), and re-notifies after the
    cooldown expires.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server.branding import APP_NAME
from voice_typer.server.model_manager import ModelManager


@pytest.fixture(autouse=True)
def _balloon_path_by_default(monkeypatch):
    """Force the pystray-balloon fallback for the legacy tests.

    ``_on_last_resort_unloaded`` prefers a clickable ``notification``
    event when ``event_bus.has_live_transport()`` reports a live host.
    In the pytest session the real event_bus may have transport probes
    registered by earlier-collected IPC test files, so the legacy tests
    (which assert ``tray.notify`` was called) must pin the fallback to
    False deterministically. The clickable-path tests below override
    this back to True explicitly.
    """
    import voice_typer.server.event_bus as event_bus

    monkeypatch.setattr(event_bus, "has_live_transport", lambda: False)


# ── Helpers ────────────────────────────────────────────────────────────


def _make_unloaded_backend() -> MagicMock:
    """A backend whose ``is_loaded`` is False (trigger condition)."""
    backend = MagicMock()
    backend.is_loaded = False
    return backend


def _make_mm() -> tuple[ModelManager, MagicMock]:
    """Construct a ModelManager with a REAL registry (so the subscriber
    wiring + get_active last-resort path are exercised) and a mock app.

    Returns ``(mm, app)``. The mock app's ``tray.notify`` is a MagicMock
    so tests can assert on notification calls.
    """
    app = MagicMock(name="app")
    app.config.asr_backend = "whisper"
    app.config.model_size = "tiny.en"
    app.config.device = "cpu"
    app.config.language = "en"
    app.config.beam_size = 1
    app.config.best_of = 1
    app.config.condition_on_previous_text = False
    app._shutting_down = False
    app._pending_dictation = False
    app._thread_registry = MagicMock()

    mm = ModelManager(app)
    # Keep the REAL registry (do NOT replace with a mock) so the
    # subscriber wiring and the get_active last-resort path run for real.
    return mm, app


def _trigger_last_resort(mm: ModelManager) -> MagicMock:
    """Register an unloaded whisper backend and call ``get_active()`` so
    the last-resort branch fires the on_last_resort subscribers.

    Returns the registered backend.
    """
    backend = _make_unloaded_backend()
    mm._registry.register("whisper", backend)
    result = mm._registry.get_active()
    assert result is backend, "get_active() must return the last-resort backend"
    return backend


# ── Test classes ───────────────────────────────────────────────────────


class TestLastResortSubscriberWired:
    """ModelManager.__init__ must wire the production subscriber."""

    def test_init_wires_on_last_resort_subscriber(self):
        """After ``ModelManager(app)``, the bound ``_on_last_resort_unloaded``
        must be in the registry's ``on_last_resort`` subscriber set.

        Pre-fix the subscriber set existed but nothing subscribed — the
        documented tray notification was dead code.
        """
        mm, _ = _make_mm()
        assert mm._on_last_resort_unloaded in mm._registry.on_last_resort, (
            "ModelManager.__init__ must wire _on_last_resort_unloaded onto "
            "the registry's on_last_resort subscriber set."
        )


class TestLastResortNotificationShown:
    """A last-resort fall-through shows a tray notification pointing at
    the Models page."""

    def test_get_active_fires_tray_notification_with_models_page_instruction(self):
        """When ``get_active()`` falls through to an unloaded backend, a
        tray notification must be shown whose message points the user at
        the Models page with the download instruction.
        """
        mm, app = _make_mm()
        _trigger_last_resort(mm)

        app.tray.notify.assert_called_once()
        title, message = app.tray.notify.call_args.args
        assert message, "notification must have a non-empty message"
        # The download instruction must be present and point at the
        # Models page (the app never auto-downloads models).
        assert "Models page" in message, (
            "last-resort notification must point the user at the Models "
            f"page. Got message: {message!r}"
        )
        assert "Download" in message, (
            "last-resort notification must include the download "
            f"instruction. Got message: {message!r}"
        )
        # The backend name must be interpolated so the user knows which
        # model is affected.
        assert "whisper" in message.lower(), (
            "last-resort notification must name the affected backend. "
            f"Got message: {message!r}"
        )

    def test_notification_not_shown_when_backend_is_ready(self):
        """A READY backend must not trigger the notification (the
        last-resort branch is not reached)."""
        mm, app = _make_mm()
        backend = MagicMock()
        backend.is_loaded = True
        mm._registry.register("whisper", backend)

        result = mm._registry.get_active()
        assert result is backend
        app.tray.notify.assert_not_called()


class TestLastResortNotificationSuppressed:
    """The notification is suppressed for deliberate unloads, load-in-
    progress windows, and shutdown."""

    def test_suppressed_while_load_in_progress(self):
        """While a background load thread is alive (backend registered but
        not yet loaded), the last-resort fall-through is a false positive
        — the notification must NOT fire."""
        mm, app = _make_mm()
        _trigger_last_resort(mm)  # first transition notifies (no load thread)
        app.tray.notify.reset_mock()

        # Simulate a load in progress on a tracked thread.
        stop = threading.Event()

        def _wait() -> None:
            stop.wait(timeout=5.0)

        thread = threading.Thread(target=_wait, daemon=True)
        thread.start()
        mm._model_load_thread = thread
        try:
            # Reset the registry latch so the subscriber WOULD fire again
            # if the suppression gate were absent.
            mm._registry._breaker.clear_last_resort_notified()
            mm._registry.get_active()
            app.tray.notify.assert_not_called()
        finally:
            stop.set()
            thread.join(timeout=5.0)

    def test_suppressed_while_sync_load_in_progress(self):
        """While a synchronous ``load_active`` is running on the calling
        thread (``ensure_active_engine_loaded``'s reload-after-idle-
        unload / retry branch), a concurrent get_status probe must NOT
        fire the download nudge — the model is literally loading."""
        mm, app = _make_mm()
        _trigger_last_resort(mm)
        app.tray.notify.reset_mock()

        mm._sync_load_in_progress = True
        try:
            mm._registry._breaker.clear_last_resort_notified()
            mm._registry.get_active()
            app.tray.notify.assert_not_called()
        finally:
            mm._sync_load_in_progress = False

    def test_suppressed_while_shutting_down(self):
        """During shutdown the tray may be torn down — no notification."""
        mm, app = _make_mm()
        app._shutting_down = True
        _trigger_last_resort(mm)
        app.tray.notify.assert_not_called()

    def test_suppressed_for_deliberately_unloaded_backend(self):
        """A backend that was deliberately unloaded (idle-unload /
        force-unload / LRU eviction / model change) must NOT trigger a
        download nudge — the model IS on disk."""
        mm, app = _make_mm()
        mm._mark_deliberately_unloaded("whisper")
        _trigger_last_resort(mm)
        app.tray.notify.assert_not_called()

    def test_idle_unload_marks_backend_deliberately_unloaded(self):
        """``_do_idle_unload`` must record the active backend so the
        last-resort notification is suppressed for it."""
        mm, app = _make_mm()
        backend = MagicMock()
        backend.is_loaded = True
        mm._registry.register("whisper", backend)

        # Simulate the idle-unload path: mark + unload via the registry.
        mm._mark_deliberately_unloaded(mm._registry.active_name)
        mm._registry.unload("whisper")
        backend.is_loaded = False

        assert mm._was_deliberately_unloaded("whisper"), (
            "idle-unload must record the active backend as deliberately unloaded"
        )
        # The next fall-through must be suppressed.
        mm._registry.get_active()
        app.tray.notify.assert_not_called()

    def test_successful_load_clears_deliberate_unload_flag(self):
        """After a successful load the deliberate-unload flag must be
        cleared so a FUTURE genuine failure re-notifies the user."""
        mm, app = _make_mm()
        mm._mark_deliberately_unloaded("whisper")
        # The load-success paths call _clear_deliberately_unloaded.
        mm._clear_deliberately_unloaded("whisper")
        assert not mm._was_deliberately_unloaded("whisper")
        _trigger_last_resort(mm)
        app.tray.notify.assert_called_once()


class TestLastResortNotificationClickable:
    """When a host (Electron/Tauri) is connected, the last-resort
    notification is published as a ``notification`` event carrying a
    ``click_path`` so the host renders a CLICKABLE native toast that
    opens the Models page directly — pystray Win32 balloons cannot carry
    a click handler. Without a live transport, the pystray balloon
    fallback keeps working."""

    def test_live_transport_publishes_clickable_notification_event(self, monkeypatch):
        """With a live host transport, ``_on_last_resort_unloaded`` must
        publish a ``notification`` event whose data carries
        ``click_path: "/models"`` (the Electron main-process handler
        wires ``Notification.on("click")`` → navigate) and must NOT
        fall back to the pystray balloon."""
        mm, app = _make_mm()
        published: list[dict] = []

        import voice_typer.server.event_bus as event_bus

        monkeypatch.setattr(event_bus, "has_live_transport", lambda: True)
        monkeypatch.setattr(event_bus, "publish", published.append)

        _trigger_last_resort(mm)

        # The pystray balloon must NOT be used (it has no click handler).
        app.tray.notify.assert_not_called()

        notifications = [e for e in published if e.get("type") == "notification"]
        assert len(notifications) == 1, f"expected one notification event, got {published!r}"
        data = notifications[0]["data"]
        assert data.get("click_path") == "/models", (
            f"notification must carry click_path='/models' so the host toast "
            f"opens the Models page on click. Got data: {data!r}"
        )
        message = data.get("message", "")
        assert "Models page" in message, (
            "notification message must still point the user at the Models "
            f"page. Got: {message!r}"
        )
        assert data.get("title") == APP_NAME

    def test_no_live_transport_falls_back_to_pystray_balloon(self, monkeypatch):
        """Without a live host transport (standalone backend), the
        pystray balloon fallback must still fire (existing behavior)."""
        mm, app = _make_mm()

        import voice_typer.server.event_bus as event_bus

        monkeypatch.setattr(event_bus, "has_live_transport", lambda: False)

        _trigger_last_resort(mm)

        app.tray.notify.assert_called_once()
        title, message = app.tray.notify.call_args.args
        assert "Models page" in message and "Download" in message

    def test_publish_failure_falls_back_to_pystray_balloon(self, monkeypatch):
        """If the ``notification`` event publish raises, the fallback
        balloon must still fire (a broken event bus must not swallow the
        user alert)."""
        mm, app = _make_mm()

        import voice_typer.server.event_bus as event_bus

        monkeypatch.setattr(event_bus, "has_live_transport", lambda: True)

        def _boom(_event: dict) -> bool:
            raise RuntimeError("event bus down")

        monkeypatch.setattr(event_bus, "publish", _boom)

        _trigger_last_resort(mm)

        app.tray.notify.assert_called_once()

    def test_live_transport_but_notifications_disabled_no_notification(self, monkeypatch):
        """The notifications toggle must gate the clickable path too — a
        user who disabled notifications gets neither a toast nor a
        balloon (mirrors ``tray_notifications.notify``)."""
        mm, app = _make_mm()
        app.tray._notifications_enabled = False
        published: list[dict] = []

        import voice_typer.server.event_bus as event_bus

        monkeypatch.setattr(event_bus, "has_live_transport", lambda: True)
        monkeypatch.setattr(event_bus, "publish", published.append)

        _trigger_last_resort(mm)

        app.tray.notify.assert_not_called()
        assert not any(e.get("type") == "notification" for e in published), published


class TestLastResortEventGateWired:
    """ModelManager wires the event_bus suppression gate so the renderer
    toast (which consumes the ``asr_last_resort_unloaded`` event) matches
    the tray notification's suppressions exactly — the toast cannot see
    the ModelManager-side checks otherwise.

    The gate is checked by the breaker BEFORE the subscribers fire, so a
    suppressed window skips BOTH the event_bus publish AND the tray
    notification (the tray path would self-suppress anyway)."""

    def test_init_wires_event_gate(self):
        """After ``ModelManager(app)``, the breaker's event gate must be
        the bound ``_should_suppress_last_resort_notification`` — the
        renderer toast and the tray share ONE suppression decision."""
        mm, _ = _make_mm()
        gate = mm._registry._breaker._last_resort_event_gate
        assert gate is not None, "ModelManager.__init__ must install the event gate"
        assert gate == mm._should_suppress_last_resort_notification, (
            "ModelManager.__init__ must wire _should_suppress_last_resort_notification "
            "onto the registry's breaker so the renderer toast matches the "
            "tray notification's suppressions."
        )

    def test_deliberate_unload_suppresses_event_publish(self, monkeypatch):
        """A deliberately-unloaded backend (idle-unload / force-unload /
        LRU eviction / model change) must NOT publish the
        ``asr_last_resort_unloaded`` event — the renderer toast must not
        tell the user to download a model that is on disk."""
        mm, app = _make_mm()
        mm._mark_deliberately_unloaded("whisper")
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish", published.append
        )

        _trigger_last_resort(mm)

        assert not any(
            e.get("type") == "asr_last_resort_unloaded" for e in published
        ), (
            "deliberate unload must suppress the asr_last_resort_unloaded "
            f"event_bus publish (renderer toast). Got {published!r}."
        )
        app.tray.notify.assert_not_called()

    def test_load_in_progress_suppresses_event_publish(self, monkeypatch):
        """While a synchronous load is running (the model is literally
        loading), the event must NOT be published — the renderer toast
        must not fire during the reload-after-idle-unload window."""
        mm, app = _make_mm()
        mm._sync_load_in_progress = True
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish", published.append
        )
        try:
            _trigger_last_resort(mm)
        finally:
            mm._sync_load_in_progress = False

        assert not any(
            e.get("type") == "asr_last_resort_unloaded" for e in published
        ), (
            "load-in-progress must suppress the asr_last_resort_unloaded "
            f"event_bus publish. Got {published!r}."
        )
        app.tray.notify.assert_not_called()

    def test_shutting_down_suppresses_event_publish(self, monkeypatch):
        """During shutdown the event must not be published (mirrors the
        tray suppression)."""
        mm, app = _make_mm()
        app._shutting_down = True
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish", published.append
        )

        _trigger_last_resort(mm)

        assert not any(
            e.get("type") == "asr_last_resort_unloaded" for e in published
        ), "shutting down must suppress the asr_last_resort_unloaded publish"
        app.tray.notify.assert_not_called()

    def test_genuine_broken_backend_publishes_event(self, monkeypatch):
        """A genuinely broken backend (NOT deliberately unloaded, no load
        in progress) must still publish the event — the renderer toast
        and the tray both alert the user."""
        mm, app = _make_mm()
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish", published.append
        )

        _trigger_last_resort(mm)

        events = [e for e in published if e.get("type") == "asr_last_resort_unloaded"]
        assert len(events) == 1, (
            "a genuine last-resort fall-through must publish the event "
            f"once. Got {published!r}."
        )
        assert events[0]["data"]["backend"] == "whisper"
        app.tray.notify.assert_called_once()

    def test_cooldown_suppresses_repeat_event_publish(self, monkeypatch):
        """When the registry latch resets (15s probe) within the cooldown,
        the repeat transition must NOT re-publish the event — the
        renderer toast, like the tray, is rate-limited per backend."""
        mm, app = _make_mm()
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish", published.append
        )

        _trigger_last_resort(mm)
        assert len([e for e in published if e.get("type") == "asr_last_resort_unloaded"]) == 1

        # Simulate the latch reset (the probe / load_with_fallback retry).
        mm._registry._breaker.clear_last_resort_notified()
        mm._registry.get_active()
        assert len([e for e in published if e.get("type") == "asr_last_resort_unloaded"]) == 1, (
            "repeat transition within the cooldown must not re-publish "
            "the event (renderer toast rate-limited like the tray)"
        )
        assert app.tray.notify.call_count == 1

    def test_renotifies_event_after_cooldown_expires(self, monkeypatch):
        """Once the cooldown elapses, a new genuine transition re-publishes
        the event — the user is still pointed at the Models page."""
        mm, app = _make_mm()
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish", published.append
        )

        _trigger_last_resort(mm)
        assert len([e for e in published if e.get("type") == "asr_last_resort_unloaded"]) == 1

        # Expire the cooldown (simulate 15+ minutes passing).
        mm._last_resort_notified_at["whisper"] = time.monotonic() - (
            mm._LAST_RESORT_NOTIFY_COOLDOWN_SECS + 1.0
        )
        mm._registry._breaker.clear_last_resort_notified()
        mm._registry.get_active()
        assert len([e for e in published if e.get("type") == "asr_last_resort_unloaded"]) == 2, (
            "after the cooldown, a new last-resort transition must "
            "re-publish the event (renderer toast re-alerts)"
        )
        assert app.tray.notify.call_count == 2


class TestLastResortNotificationRateLimit:
    """The per-backend rate limit stops the 15s probe from spamming the
    tray while the registry latch keeps resetting."""

    def test_repeat_transition_within_cooldown_is_suppressed(self):
        """If the registry latch resets (as the 15s get_status probe
        does) and the backend is still broken, a second notification must
        NOT fire within ``_LAST_RESORT_NOTIFY_COOLDOWN_SECS``."""
        mm, app = _make_mm()
        _trigger_last_resort(mm)
        assert app.tray.notify.call_count == 1

        # Simulate the latch reset (the probe / load_with_fallback retry).
        mm._registry._breaker.clear_last_resort_notified()
        mm._registry.get_active()
        assert app.tray.notify.call_count == 1, (
            "repeat last-resort transition within the cooldown must be suppressed"
        )

    def test_renotifies_after_cooldown_expires(self):
        """Once the cooldown elapses, a new transition re-notifies (the
        user is still pointed at the Models page)."""
        mm, app = _make_mm()
        _trigger_last_resort(mm)
        assert app.tray.notify.call_count == 1

        # Expire the cooldown (simulate 15+ minutes passing).
        mm._last_resort_notified_at["whisper"] = time.monotonic() - (
            mm._LAST_RESORT_NOTIFY_COOLDOWN_SECS + 1.0
        )
        mm._registry._breaker.clear_last_resort_notified()
        mm._registry.get_active()
        assert app.tray.notify.call_count == 2, (
            "after the cooldown, a new last-resort transition must re-notify"
        )

    def test_rate_limit_is_per_backend(self):
        """A broken parakeet must not be rate-limited by an earlier
        whisper notification."""
        mm, app = _make_mm()
        _trigger_last_resort(mm)  # whisper notified
        assert app.tray.notify.call_count == 1

        # Now break parakeet (configured backend switch) — different key,
        # must notify independently. The registry's one-shot latch is
        # reset first (as the probe / load retry would) so the subscriber
        # actually re-fires for the new transition.
        app.config.asr_backend = "parakeet"
        backend = _make_unloaded_backend()
        mm._registry.register("parakeet", backend)
        mm._registry._breaker.clear_last_resort_notified()
        mm._registry.get_active()
        assert app.tray.notify.call_count == 2, (
            "rate limit must be per-backend; parakeet must not inherit "
            "whisper's cooldown"
        )


class TestBackendDisabledEventGateWired:
    """ModelManager wires the breaker's backend-disabled suppression gate
    so the ``asr_backend_disabled`` event_bus publish (consumed by the
    renderer) is suppressed during the same deliberate-unload windows as
    the last-resort alert — a backend that was deliberately unloaded / is
    mid-load must not publish a spurious 'disabled' event when the
    switch's own transient failure trips the breaker."""

    @staticmethod
    def _trip_backend_disabled(mm: ModelManager, name: str = "whisper") -> None:
        """Drive ``_record_failure`` past the trip threshold
        (``_MAX_CONSECUTIVE_FAILURES = 3``) on the ModelManager's real
        registry, publishing ``asr_backend_disabled`` unless gated."""
        for _ in range(3):
            mm._registry._record_failure(name)

    def test_init_wires_backend_disabled_event_gate(self):
        """After ``ModelManager(app)``, the breaker's backend-disabled
        event gate must be the bound
        ``_should_suppress_backend_disabled_notification`` — the
        ``asr_backend_disabled`` event and the deliberate-unload windows
        share ONE suppression decision."""
        mm, _ = _make_mm()
        gate = mm._registry._breaker._backend_disabled_event_gate
        assert gate is not None, (
            "ModelManager.__init__ must install the backend-disabled event gate"
        )
        assert gate == mm._should_suppress_backend_disabled_notification, (
            "ModelManager.__init__ must wire "
            "_should_suppress_backend_disabled_notification onto the "
            "registry's breaker so the asr_backend_disabled event matches "
            "the deliberate-unload windows."
        )

    def test_deliberate_unload_suppresses_backend_disabled_publish(self, monkeypatch):
        """A deliberately-unloaded backend (idle-unload / force-unload /
        LRU eviction / model change) must NOT publish the
        ``asr_backend_disabled`` event — the breaker can trip on the
        switch's own transient failure, but the app is merely releasing /
        switching, not reporting a permanently-broken backend.

        The circuit-breaker state mutation (disabling the backend) is
        NOT gated — only the notification surface."""
        mm, _ = _make_mm()
        mm._mark_deliberately_unloaded("whisper")
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish", published.append
        )

        self._trip_backend_disabled(mm)

        assert not any(
            e.get("type") == "asr_backend_disabled" for e in published
        ), (
            "deliberate unload must suppress the asr_backend_disabled "
            f"event_bus publish (renderer event). Got {published!r}."
        )
        assert "whisper" in mm._registry._disabled_backends, (
            "the gate must NOT prevent the breaker from disabling the "
            "backend — only the notification surface is suppressed."
        )

    def test_load_in_progress_suppresses_backend_disabled_publish(self, monkeypatch):
        """While a synchronous load is running (the model is literally
        loading), the ``asr_backend_disabled`` event must NOT be
        published — a transient failure during the reload window is not
        a permanent disable."""
        mm, _ = _make_mm()
        mm._sync_load_in_progress = True
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish", published.append
        )
        try:
            self._trip_backend_disabled(mm)
        finally:
            mm._sync_load_in_progress = False

        assert not any(
            e.get("type") == "asr_backend_disabled" for e in published
        ), (
            "load-in-progress must suppress the asr_backend_disabled "
            f"event_bus publish. Got {published!r}."
        )

    def test_genuine_broken_backend_publishes_backend_disabled(self, monkeypatch):
        """A genuinely broken backend (NOT deliberately unloaded, no load
        in progress) must still publish the ``asr_backend_disabled``
        event — the gate only suppresses during deliberate-unload
        windows, never a real alert."""
        mm, _ = _make_mm()
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish", published.append
        )

        self._trip_backend_disabled(mm)

        assert any(
            e.get("type") == "asr_backend_disabled" for e in published
        ), (
            "a genuinely broken backend must still publish "
            f"asr_backend_disabled. Got {published!r}."
        )

    def test_last_resort_cooldown_does_not_suppress_backend_disabled(self, monkeypatch):
        """Cross-surface non-interaction: a RECENT last-resort
        notification for the same backend (which engages the
        ``_LAST_RESORT_NOTIFY_COOLDOWN_SECS`` rate limit in
        ``_should_suppress_last_resort_notification``) must NOT suppress
        a genuine ``asr_backend_disabled`` event — the cooldown lives
        only in the last-resort helper, never in the backend-disabled
        gate. Locks the boundary so a future refactor can't merge the
        two gates' suppression sets."""
        mm, _ = _make_mm()
        # Fire a last-resort transition first — records
        # ``_last_resort_notified_at["whisper"]`` (the tray subscriber
        # timestamps it).
        _trigger_last_resort(mm)
        assert "whisper" in mm._last_resort_notified_at, (
            "sanity: the last-resort transition must record the cooldown timestamp"
        )
        # The last-resort suppression must now be engaged (cooldown
        # active)…
        assert mm._should_suppress_last_resort_notification("whisper") is True, (
            "sanity: a repeat last-resort alert within the cooldown must be suppressed"
        )

        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish", published.append
        )
        # …but a genuine backend-disabled trip must STILL publish.
        self._trip_backend_disabled(mm)

        assert any(
            e.get("type") == "asr_backend_disabled" for e in published
        ), (
            "a recent last-resort notification must NOT suppress a "
            "genuine asr_backend_disabled event (cooldown is "
            f"last-resort-only). Got {published!r}."
        )
