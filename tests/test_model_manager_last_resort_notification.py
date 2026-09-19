"""regression tests for the production last-resort tray notification."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server.branding import APP_NAME
from voice_typer.server.model_manager import ModelManager


@pytest.fixture(autouse=True)
def _balloon_path_by_default(monkeypatch):
    """Force the pystray-balloon fallback for the legacy tests."""
    import voice_typer.server.event_bus as event_bus

    monkeypatch.setattr(event_bus, "has_live_transport", lambda: False)


def _make_unloaded_backend() -> MagicMock:
    """A backend whose ``is_loaded`` is False (trigger condition)."""
    backend = MagicMock()
    backend.is_loaded = False
    return backend


def _make_mm() -> tuple[ModelManager, MagicMock]:
    """Construct a ModelManager with a REAL registry (so the subscriber"""
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
    return mm, app


def _trigger_last_resort(mm: ModelManager) -> MagicMock:
    """Register an unloaded whisper backend and call ``get_active()`` so"""
    backend = _make_unloaded_backend()
    mm._registry.register("whisper", backend)
    result = mm._registry.get_active()
    assert result is None, "get_active() must return None fail-loud when only unloaded remains"
    return backend


class TestLastResortSubscriberWired:
    """ModelManager.__init__ must wire the production subscriber."""

    def test_init_wires_on_last_resort_subscriber(self):
        """After ``ModelManager(app)``, the bound ``_on_last_resort_unloaded``"""
        mm, _ = _make_mm()
        assert mm._on_last_resort_unloaded in mm._registry.on_last_resort, (
            "ModelManager.__init__ must wire _on_last_resort_unloaded onto "
            "the registry's on_last_resort subscriber set."
        )


class TestLastResortNotificationShown:
    """A last-resort fall-through shows a tray notification pointing at"""

    def test_get_active_fires_tray_notification_with_models_page_instruction(self):
        """tray notification must be shown whose message points the user at"""
        mm, app = _make_mm()
        _trigger_last_resort(mm)

        app.tray.notify.assert_called_once()
        title, message = app.tray.notify.call_args.args
        assert message, "notification must have a non-empty message"
        assert "models page" in message, (
            f"last-resort notification must point the user at the models page. Got message: {message!r}"
        )
        assert "download a model" in message, (
            f"last-resort notification must include the download instruction. Got message: {message!r}"
        )
        # 2026-08-15 user request: the message must be GENERIC, it must
        assert "whisper" not in message.lower(), (
            f"last-resort notification must NOT name the backend. Got message: {message!r}"
        )
        assert "Open the models page to download a model" in message, (
            f"last-resort notification must carry the generic download instruction. Got message: {message!r}"
        )

    def test_notification_not_shown_when_backend_is_ready(self):
        """A READY backend must not trigger the notification (the"""
        mm, app = _make_mm()
        backend = MagicMock()
        backend.is_loaded = True
        mm._registry.register("whisper", backend)

        result = mm._registry.get_active()
        assert result is backend
        app.tray.notify.assert_not_called()


class TestLastResortNotificationSuppressed:
    """The notification is suppressed for deliberate unloads, load-in-"""

    def test_suppressed_while_load_in_progress(self):
        """While a background load thread is alive (backend registered but"""
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
            mm._registry._breaker.clear_last_resort_notified()
            mm._registry.get_active()
            app.tray.notify.assert_not_called()
        finally:
            stop.set()
            thread.join(timeout=5.0)

    def test_suppressed_while_sync_load_in_progress(self):
        """While a synchronous ``load_active`` is running on the calling"""
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
        """During shutdown the tray may be torn down, no notification."""
        mm, app = _make_mm()
        app._shutting_down = True
        _trigger_last_resort(mm)
        app.tray.notify.assert_not_called()

    def test_suppressed_for_deliberately_unloaded_backend(self):
        """A backend that was deliberately unloaded (idle-unload /"""
        mm, app = _make_mm()
        mm._mark_deliberately_unloaded("whisper")
        _trigger_last_resort(mm)
        app.tray.notify.assert_not_called()

    def test_idle_unload_marks_backend_deliberately_unloaded(self):
        """``_do_idle_unload`` must record the active backend so the"""
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
        """After a successful load the deliberate-unload flag must be"""
        mm, app = _make_mm()
        mm._mark_deliberately_unloaded("whisper")
        # The load-success paths call _clear_deliberately_unloaded.
        mm._clear_deliberately_unloaded("whisper")
        assert not mm._was_deliberately_unloaded("whisper")
        _trigger_last_resort(mm)
        app.tray.notify.assert_called_once()


class TestLastResortNotificationClickable:
    """When a host (predecessor/Tauri) is connected, the last-resort"""

    def test_live_transport_publishes_clickable_notification_event(self, monkeypatch):
        """With a live host transport, ``_on_last_resort_unloaded`` must"""
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
        assert "models page" in message, (
            f"notification message must still point the user at the models page. Got: {message!r}"
        )
        assert data.get("title") == APP_NAME

    def test_no_live_transport_falls_back_to_pystray_balloon(self, monkeypatch):
        """Without a live host transport (standalone backend), the"""
        mm, app = _make_mm()

        import voice_typer.server.event_bus as event_bus

        monkeypatch.setattr(event_bus, "has_live_transport", lambda: False)

        _trigger_last_resort(mm)

        app.tray.notify.assert_called_once()
        title, message = app.tray.notify.call_args.args
        assert "models page" in message and "download a model" in message

    def test_publish_failure_falls_back_to_pystray_balloon(self, monkeypatch):
        """If the ``notification`` event publish raises, the fallback"""
        mm, app = _make_mm()

        import voice_typer.server.event_bus as event_bus

        monkeypatch.setattr(event_bus, "has_live_transport", lambda: True)

        def _boom(_event: dict) -> bool:
            raise RuntimeError("event bus down")

        monkeypatch.setattr(event_bus, "publish", _boom)

        _trigger_last_resort(mm)

        app.tray.notify.assert_called_once()

    def test_live_transport_but_notifications_disabled_no_notification(self, monkeypatch):
        """balloon (mirrors ``tray_notifications.notify``)."""
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
    """ModelManager wires the event_bus suppression gate so the renderer"""

    def test_init_wires_event_gate(self):
        """After ``ModelManager(app)``, the breaker's event gate must be"""
        mm, _ = _make_mm()
        gate = mm._registry._breaker._last_resort_event_gate
        assert gate is not None, "ModelManager.__init__ must install the event gate"
        assert gate == mm._should_suppress_last_resort_notification, (
            "ModelManager.__init__ must wire _should_suppress_last_resort_notification "
            "onto the registry's breaker so the renderer toast matches the "
            "tray notification's suppressions."
        )

    def test_deliberate_unload_suppresses_event_publish(self, monkeypatch):
        """
        A deliberately-unloaded backend (idle-unload / force-unload /
        ``asr_last_resort_unloaded`` event, the renderer toast must not
        """
        mm, app = _make_mm()
        mm._mark_deliberately_unloaded("whisper")
        published: list[dict] = []
        monkeypatch.setattr("voice_typer.server.event_bus.publish", published.append)

        _trigger_last_resort(mm)

        assert not any(e.get("type") == "asr_last_resort_unloaded" for e in published), (
            "deliberate unload must suppress the asr_last_resort_unloaded "
            f"event_bus publish (renderer toast). Got {published!r}."
        )
        app.tray.notify.assert_not_called()

    def test_load_in_progress_suppresses_event_publish(self, monkeypatch):
        """While a synchronous load is running (the model is literally"""
        mm, app = _make_mm()
        mm._sync_load_in_progress = True
        published: list[dict] = []
        monkeypatch.setattr("voice_typer.server.event_bus.publish", published.append)
        try:
            _trigger_last_resort(mm)
        finally:
            mm._sync_load_in_progress = False

        assert not any(e.get("type") == "asr_last_resort_unloaded" for e in published), (
            f"load-in-progress must suppress the asr_last_resort_unloaded event_bus publish. Got {published!r}."
        )
        app.tray.notify.assert_not_called()

    def test_shutting_down_suppresses_event_publish(self, monkeypatch):
        """During shutdown the event must not be published (mirrors the"""
        mm, app = _make_mm()
        app._shutting_down = True
        published: list[dict] = []
        monkeypatch.setattr("voice_typer.server.event_bus.publish", published.append)

        _trigger_last_resort(mm)

        assert not any(e.get("type") == "asr_last_resort_unloaded" for e in published), (
            "shutting down must suppress the asr_last_resort_unloaded publish"
        )
        app.tray.notify.assert_not_called()

    def test_genuine_broken_backend_publishes_event(self, monkeypatch):
        """A genuinely broken backend (NOT deliberately unloaded, no load"""
        mm, app = _make_mm()
        published: list[dict] = []
        monkeypatch.setattr("voice_typer.server.event_bus.publish", published.append)

        _trigger_last_resort(mm)

        events = [e for e in published if e.get("type") == "asr_last_resort_unloaded"]
        assert len(events) == 1, f"a genuine last-resort fall-through must publish the event once. Got {published!r}."
        assert events[0]["data"]["backend"] == "whisper"
        app.tray.notify.assert_called_once()

    def test_cooldown_suppresses_repeat_event_publish(self, monkeypatch):
        """When the registry latch resets (15s probe) within the cooldown,"""
        mm, app = _make_mm()
        published: list[dict] = []
        monkeypatch.setattr("voice_typer.server.event_bus.publish", published.append)

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
        """Once the cooldown elapses, a new genuine transition re-publishes"""
        mm, app = _make_mm()
        published: list[dict] = []
        monkeypatch.setattr("voice_typer.server.event_bus.publish", published.append)

        _trigger_last_resort(mm)
        assert len([e for e in published if e.get("type") == "asr_last_resort_unloaded"]) == 1

        # Expire the cooldown (simulate 15+ minutes passing).
        mm._last_resort_notified_at["whisper"] = time.monotonic() - (mm._LAST_RESORT_NOTIFY_COOLDOWN_SECS + 1.0)
        mm._registry._breaker.clear_last_resort_notified()
        mm._registry.get_active()
        assert len([e for e in published if e.get("type") == "asr_last_resort_unloaded"]) == 2, (
            "after the cooldown, a new last-resort transition must re-publish the event (renderer toast re-alerts)"
        )
        assert app.tray.notify.call_count == 2


class TestLastResortNotificationRateLimit:
    """The per-backend rate limit stops the 15s probe from spamming the"""

    def test_repeat_transition_within_cooldown_is_suppressed(self):
        """If the registry latch resets (as the 15s get_status probe"""
        mm, app = _make_mm()
        _trigger_last_resort(mm)
        assert app.tray.notify.call_count == 1

        # Simulate the latch reset (the probe / load_with_fallback retry).
        mm._registry._breaker.clear_last_resort_notified()
        mm._registry.get_active()
        assert app.tray.notify.call_count == 1, "repeat last-resort transition within the cooldown must be suppressed"

    def test_renotifies_after_cooldown_expires(self):
        """Once the cooldown elapses, a new transition re-notifies (the"""
        mm, app = _make_mm()
        _trigger_last_resort(mm)
        assert app.tray.notify.call_count == 1

        # Expire the cooldown (simulate 15+ minutes passing).
        mm._last_resort_notified_at["whisper"] = time.monotonic() - (mm._LAST_RESORT_NOTIFY_COOLDOWN_SECS + 1.0)
        mm._registry._breaker.clear_last_resort_notified()
        mm._registry.get_active()
        assert app.tray.notify.call_count == 2, "after the cooldown, a new last-resort transition must re-notify"

    def test_rate_limit_is_per_backend(self):
        """A broken parakeet must not be rate-limited by an earlier"""
        mm, app = _make_mm()
        _trigger_last_resort(mm)  # whisper notified
        assert app.tray.notify.call_count == 1

        app.config.asr_backend = "parakeet"
        backend = _make_unloaded_backend()
        mm._registry.register("parakeet", backend)
        mm._registry._breaker.clear_last_resort_notified()
        mm._registry.get_active()
        assert app.tray.notify.call_count == 2, (
            "rate limit must be per-backend; parakeet must not inherit whisper's cooldown"
        )


class TestBackendDisabledEventGateWired:
    """ModelManager wires the breaker's backend-disabled suppression gate"""

    @staticmethod
    def _trip_backend_disabled(mm: ModelManager, name: str = "whisper") -> None:
        """Drive ``_record_failure`` past the trip threshold"""
        for _ in range(3):
            mm._registry._record_failure(name)

    def test_init_wires_backend_disabled_event_gate(self):
        """After ``ModelManager(app)``, the breaker's backend-disabled"""
        mm, _ = _make_mm()
        gate = mm._registry._breaker._backend_disabled_event_gate
        assert gate is not None, "ModelManager.__init__ must install the backend-disabled event gate"
        assert gate == mm._should_suppress_backend_disabled_notification, (
            "ModelManager.__init__ must wire "
            "_should_suppress_backend_disabled_notification onto the "
            "registry's breaker so the asr_backend_disabled event matches "
            "the deliberate-unload windows."
        )

    def test_deliberate_unload_suppresses_backend_disabled_publish(self, monkeypatch):
        """A deliberately-unloaded backend (idle-unload / force-unload /"""
        mm, _ = _make_mm()
        mm._mark_deliberately_unloaded("whisper")
        published: list[dict] = []
        monkeypatch.setattr("voice_typer.server.event_bus.publish", published.append)

        self._trip_backend_disabled(mm)

        assert not any(e.get("type") == "asr_backend_disabled" for e in published), (
            "deliberate unload must suppress the asr_backend_disabled "
            f"event_bus publish (renderer event). Got {published!r}."
        )
        assert "whisper" in mm._registry._disabled_backends, (
            "the gate must NOT prevent the breaker from disabling the "
            "backend, only the notification surface is suppressed."
        )

    def test_load_in_progress_suppresses_backend_disabled_publish(self, monkeypatch):
        """While a synchronous load is running (the model is literally"""
        mm, _ = _make_mm()
        mm._sync_load_in_progress = True
        published: list[dict] = []
        monkeypatch.setattr("voice_typer.server.event_bus.publish", published.append)
        try:
            self._trip_backend_disabled(mm)
        finally:
            mm._sync_load_in_progress = False

        assert not any(e.get("type") == "asr_backend_disabled" for e in published), (
            f"load-in-progress must suppress the asr_backend_disabled event_bus publish. Got {published!r}."
        )

    def test_genuine_broken_backend_publishes_backend_disabled(self, monkeypatch):
        """A genuinely broken backend (NOT deliberately unloaded, no load"""
        mm, _ = _make_mm()
        published: list[dict] = []
        monkeypatch.setattr("voice_typer.server.event_bus.publish", published.append)

        self._trip_backend_disabled(mm)

        assert any(e.get("type") == "asr_backend_disabled" for e in published), (
            f"a genuinely broken backend must still publish asr_backend_disabled. Got {published!r}."
        )

    def test_last_resort_cooldown_does_not_suppress_backend_disabled(self, monkeypatch):
        """
        Cross-surface non-interaction: a RECENT last-resort
        ``_should_suppress_last_resort_notification``) must NOT suppress
        """
        mm, _ = _make_mm()
        # Fire a last-resort transition first, records
        _trigger_last_resort(mm)
        assert "whisper" in mm._last_resort_notified_at, (
            "sanity: the last-resort transition must record the cooldown timestamp"
        )
        # The last-resort suppression must now be engaged (cooldown
        assert mm._should_suppress_last_resort_notification("whisper") is True, (
            "sanity: a repeat last-resort alert within the cooldown must be suppressed"
        )

        published: list[dict] = []
        monkeypatch.setattr("voice_typer.server.event_bus.publish", published.append)
        # …but a genuine backend-disabled trip must STILL publish.
        self._trip_backend_disabled(mm)

        assert any(e.get("type") == "asr_backend_disabled" for e in published), (
            "a recent last-resort notification must NOT suppress a "
            "genuine asr_backend_disabled event (cooldown is "
            f"last-resort-only). Got {published!r}."
        )
