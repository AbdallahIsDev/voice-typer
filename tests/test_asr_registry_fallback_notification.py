"""regression tests for the silent unloaded-backend fallback"""

from __future__ import annotations

from unittest.mock import MagicMock

from voice_typer.server.asr_registry import AsrBackendRegistry


def _make_unloaded_backend() -> MagicMock:
    """A backend whose ``is_loaded`` is False (trigger condition)."""
    backend = MagicMock()
    backend.is_loaded = False
    return backend


def _make_loaded_backend() -> MagicMock:
    """A backend whose ``is_loaded`` is True (no notification should fire)."""
    backend = MagicMock()
    backend.is_loaded = True
    return backend


class _Config:
    """Minimal config stub, only ``asr_backend`` is read by ``get_active``."""

    def __init__(self, asr_backend: str = "parakeet") -> None:
        self.asr_backend = asr_backend


def _make_registry_with_only_unloaded_primary(
    *, primary_name: str = "parakeet"
) -> tuple[AsrBackendRegistry, MagicMock]:
    """Construct a registry whose ONLY backend is the configured primary,"""
    registry = AsrBackendRegistry(_Config(primary_name))
    primary = _make_unloaded_backend()
    registry.register(primary_name, primary)
    return registry, primary


class TestLastResortNotificationFires:
    """the subscriber + event_bus event fire when get_active()"""

    def test_subscriber_fires_when_get_active_hits_last_resort_branch(self):
        """unloaded last-resort backend."""
        registry, primary = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        result = registry.get_active()

        # Fail-loud: unloaded last-resort returns None (never serve unloaded).
        assert result is None, "get_active() must return None when only an unloaded backend remains (fail-loud)."
        # The notification must fire:
        assert notifications == ["parakeet"], (
            "on_last_resort subscriber must fire with the "
            f"configured backend name when get_active() falls through to "
            f"the unloaded last-resort backend. Got {notifications!r}."
        )

    def test_event_bus_event_published_on_last_resort(self, monkeypatch):
        """global ``event_bus`` so the IPC push channel and any diagnostics"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: published.append(msg),
        )

        registry.get_active()

        assert any(evt.get("type") == "asr_last_resort_unloaded" for evt in published), (
            f"event_bus.publish must be called with type='asr_last_resort_unloaded'. Got {published!r}."
        )
        # The event must include the backend name so the IPC push channel
        last_resort_events = [e for e in published if e.get("type") == "asr_last_resort_unloaded"]
        assert last_resort_events[0]["data"]["backend"] == "parakeet", (
            f"asr_last_resort_unloaded event must include the backend name under data. Got {last_resort_events[0]!r}."
        )
        # The event must include a timestamp so diagnostics can correlate.
        assert "timestamp" in last_resort_events[0]["data"], (
            "asr_last_resort_unloaded event must include a timestamp under data (mirrors asr_backend_disabled)."
        )

    def test_subscriber_receives_configured_backend_name_not_actual_backend_name(self):
        """The subscriber must receive the *configured* backend name"""
        registry = AsrBackendRegistry(_Config("qwen"))
        whisper = _make_unloaded_backend()
        registry.register("whisper", whisper)

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        result = registry.get_active()
        assert result is None, "fail-loud: unloaded last-resort returns None"
        assert notifications == ["qwen"], (
            "subscriber must receive the configured backend "
            "name (matches the WARNING log), not the actual returned "
            f"backend name. Got {notifications!r}."
        )


class TestLastResortNotificationOncePerTransition:
    """the notification fires only ONCE per last-resort"""

    def test_notification_fires_only_once_for_repeated_calls(self):
        """``DictationPipeline._transcribe`` running every dictation cycle)"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        # Call get_active() 10 times, simulates 10 dictation cycles
        for _ in range(10):
            registry.get_active()

        assert notifications == ["parakeet"], (
            "notification must fire ONCE per last-resort "
            f"transition (latch), not on every get_active() call. "
            f"Got {len(notifications)} notifications: {notifications!r}."
        )

    def test_latch_resets_when_ready_backend_becomes_available(self):
        """When ``get_active()`` finds a ready backend (the configured"""
        registry, primary = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        # Step 1: first fall-through, notification fires.
        registry.get_active()
        assert notifications == ["parakeet"]

        # Step 2: backend becomes ready (e.g. user clicked "Retry load").
        primary.is_loaded = True
        result = registry.get_active()
        assert result is primary, "ready configured backend must be returned"
        # No new notification during recovery:
        assert notifications == ["parakeet"], "No notification should fire when a ready backend is available."

        # Step 3: backend breaks again, notification must fire AGAIN.
        primary.is_loaded = False
        registry.get_active()
        assert notifications == ["parakeet", "parakeet"], (
            "after recovery, the next fall-through must "
            f"re-notify (latch was cleared by the ready-backend branch). "
            f"Got {notifications!r}."
        )

    def test_latch_resets_on_record_success(self):
        """``_record_success(name)`` (called by ``load_with_fallback``"""
        registry, primary = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        # First fall-through, notification fires.
        registry.get_active()
        assert notifications == ["parakeet"]

        # Simulate a successful primary load: _record_success is called
        registry._record_success("parakeet")
        # The latch must now be False, verified by the next fall-through
        assert not registry._last_resort_notified, "_record_success must clear the last-resort latch."

        # Next fall-through must re-notify.
        registry.get_active()
        assert notifications == ["parakeet", "parakeet"], (
            f"after _record_success cleared the latch, the next fall-through must re-notify. Got {notifications!r}."
        )

    def test_latch_resets_on_whisper_fallback_load_success(self, monkeypatch):
        """``load_with_fallback``'s whisper-fallback success path must"""
        # Set up: primary parakeet (unloaded, load fails), no whisper
        failing_parakeet = _make_unloaded_backend()
        failing_parakeet.load.side_effect = RuntimeError("parakeet OOM")

        whisper_engine = _make_unloaded_backend()  # will load successfully

        class _Cfg:
            asr_backend = "parakeet"
            model_size = "parakeet"
            device = "cpu"
            language = "en"
            beam_size = 1
            best_of = 1
            condition_on_previous_text = False

        registry = AsrBackendRegistry(_Cfg())
        registry.register("parakeet", failing_parakeet)

        # Stub create() so the whisper fallback doesn't import the real
        def stub_create(name, **kwargs):
            if name == "whisper":
                registry.register("whisper", whisper_engine)
                return whisper_engine
            return None

        registry.create = stub_create  # type: ignore[method-assign]

        # First: trigger last-resort notification by calling get_active.
        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))
        registry.get_active()
        assert notifications == ["parakeet"], "first fall-through should notify"

        # Now load_with_fallback: parakeet fails, whisper fallback succeeds.
        result = registry.load_with_fallback(progress_callback=lambda msg: None)
        assert result is whisper_engine, "whisper fallback should succeed"

        # The latch must have been cleared by the whisper-fallback
        assert not registry._last_resort_notified, (
            "load_with_fallback's whisper-fallback success path must clear the last-resort latch."
        )

        # Now unload whisper and call get_active, must re-notify.
        whisper_engine.is_loaded = False
        # Parakeet is also still unloaded (its load failed).
        registry.get_active()
        assert notifications == ["parakeet", "parakeet"], (
            "after whisper-fallback success cleared the latch, "
            f"the next fall-through must re-notify. Got {notifications!r}."
        )


class TestLastResortNotificationDoesNotFire:
    """the notification must NOT fire when there's no need —"""

    def test_no_notification_when_configured_backend_is_ready(self):
        """When the configured backend is loaded, ``get_active`` returns"""
        registry = AsrBackendRegistry(_Config("parakeet"))
        parakeet = _make_loaded_backend()
        registry.register("parakeet", parakeet)

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        result = registry.get_active()
        assert result is parakeet
        assert notifications == [], "No notification should fire when the configured backend is ready."

    def test_no_notification_when_whisper_fallback_is_ready(self):
        """When the configured backend isn't ready but whisper is,"""
        registry = AsrBackendRegistry(_Config("parakeet"))
        parakeet = _make_unloaded_backend()
        whisper = _make_loaded_backend()
        registry.register("parakeet", parakeet)
        registry.register("whisper", whisper)

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        result = registry.get_active()
        assert result is whisper
        assert notifications == [], "No notification should fire when whisper fallback is ready."

    def test_no_notification_when_last_resort_backend_is_loaded(self):
        """first non-None backend IS loaded, it returns it silently (no"""
        registry = AsrBackendRegistry(_Config("parakeet"))
        # Only one backend, and it's loaded, but it's NOT the configured
        qwen = _make_loaded_backend()
        registry.register("qwen", qwen)

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        result = registry.get_active()
        assert result is qwen, "last-resort loop must still return the first non-None backend even if it's loaded."
        assert notifications == [], (
            "No notification should fire when the last-resort backend is "
            "loaded, the notification is only for the UNLOADED case."
        )


class TestLastResortSubscriberDefenceInDepth:
    """a subscriber that raises must be logged and skipped —"""

    def test_subscriber_exception_does_not_block_others(self, caplog):
        """If the first subscriber raises, the second must still fire."""
        registry, _ = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []

        def buggy_subscriber(name: str) -> None:
            raise RuntimeError("subscriber bug")

        def good_subscriber(name: str) -> None:
            notifications.append(name)

        registry.add_last_resort_subscriber(buggy_subscriber)
        registry.add_last_resort_subscriber(good_subscriber)

        with caplog.at_level("WARNING"):
            result = registry.get_active()

        assert result is None, "fail-loud: unloaded last-resort returns None even when a subscriber raises"
        assert notifications == ["parakeet"], (
            f"a buggy subscriber must NOT block the others, the good subscriber must still fire. Got {notifications!r}."
        )
        # The buggy subscriber's exception must be logged (defensive
        assert any("on_last_resort subscriber raised" in rec.message for rec in caplog.records), (
            "a subscriber exception must be logged with the "
            "message 'on_last_resort subscriber raised' so the failure is "
            "visible in the log file."
        )

    def test_event_bus_publish_exception_does_not_break_get_active(self, monkeypatch):
        """If ``event_bus.publish`` raises, ``get_active`` must still"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        def boom_publish(_msg: dict) -> bool:
            raise RuntimeError("event_bus broken")

        monkeypatch.setattr("voice_typer.server.event_bus.publish", boom_publish)

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        # Must not raise.
        result = registry.get_active()

        assert result is None, "get_active() must return None fail-loud even if event_bus.publish raises."
        assert notifications == ["parakeet"], (
            "per-registry subscriber must fire INDEPENDENTLY of "
            "the event_bus publish (the two paths are wrapped in separate "
            "try/except)."
        )


class TestLastResortEventGate:
    """the event_bus publish can be suppressed by an installed gate"""

    def test_gate_true_suppresses_subscribers_and_event(self, monkeypatch):
        """A gate returning True must suppress BOTH the subscriber"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: published.append(msg),
        )

        registry.set_last_resort_event_gate(lambda name: True)
        result = registry.get_active()

        # Fail-loud preserved (the gate only suppresses the alert fan-out).
        assert result is None, "get_active() must return None fail-loud when only unloaded remains"
        assert notifications == [], f"a suppressing gate must skip the subscriber fan-out, got {notifications!r}"
        assert not any(e.get("type") == "asr_last_resort_unloaded" for e in published), (
            f"a suppressing gate must skip the event_bus publish. Got {published!r}."
        )

    def test_gate_false_keeps_existing_behavior(self, monkeypatch):
        """A gate returning False must preserve the existing behavior —"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: published.append(msg),
        )

        registry.set_last_resort_event_gate(lambda name: False)
        registry.get_active()

        assert notifications == ["parakeet"], "a non-suppressing gate must NOT block the subscriber fan-out"
        assert any(e.get("type") == "asr_last_resort_unloaded" for e in published), (
            "a non-suppressing gate must NOT block the event_bus publish"
        )

    def test_gate_receives_configured_backend_name(self):
        """The gate must receive the configured backend name (same value"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        received: list[str] = []
        registry.set_last_resort_event_gate(lambda name: received.append(name) or False)
        registry.get_active()

        assert received == ["parakeet"], (
            "the event gate must receive the configured backend name "
            f"(matches the subscriber + WARNING log). Got {received!r}."
        )

    def test_clear_gate_restores_publish(self, monkeypatch):
        """``set_last_resort_event_gate(None)`` must restore the default"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: published.append(msg),
        )

        # First transition with a suppressing gate: nothing fires.
        registry.set_last_resort_event_gate(lambda name: True)
        registry.get_active()
        assert notifications == [] and not published

        # Clear the gate; the latch must be reset for a fresh transition.
        registry.set_last_resort_event_gate(None)
        registry._breaker.clear_last_resort_notified()
        registry.get_active()

        assert notifications == ["parakeet"], "after clearing the gate, the subscriber fan-out must fire again"
        assert any(e.get("type") == "asr_last_resort_unloaded" for e in published), (
            "after clearing the gate, the event_bus publish must fire again"
        )

    def test_gate_does_not_gate_asr_backend_disabled(self, monkeypatch):
        """The gate is scoped to the LAST-RESORT fan-out only, it must"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: published.append(msg),
        )

        registry.set_last_resort_event_gate(lambda name: True)

        for _ in range(3):
            registry._record_failure("parakeet")

        assert any(e.get("type") == "asr_backend_disabled" for e in published), (
            "the last-resort event gate must NOT suppress the "
            "asr_backend_disabled publish from _record_failure "
            f"(scope boundary). Got {published!r}."
        )

    def test_gate_exception_fails_open(self, monkeypatch, caplog):
        """A gate that raises must FAIL OPEN, the genuine alert is"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: published.append(msg),
        )

        def boom_gate(_name: str) -> bool:
            raise RuntimeError("gate broken")

        registry.set_last_resort_event_gate(boom_gate)
        with caplog.at_level("WARNING"):
            result = registry.get_active()

        assert result is None, "fail-loud preserved"
        assert notifications == ["parakeet"], "a raising gate must fail open, subscribers must still fire"
        assert any(e.get("type") == "asr_last_resort_unloaded" for e in published), (
            "a raising gate must fail open, the event_bus publish must still fire"
        )
        assert any("event gate raised" in rec.message for rec in caplog.records), (
            "the gate exception must be logged (message contains 'event gate raised')"
        )


class TestBackendDisabledEventGate:
    """the ``asr_backend_disabled`` event_bus publish can be suppressed"""

    @staticmethod
    def _trip(registry, name: str = "parakeet", times: int = 3) -> None:
        """Drive ``_record_failure`` past the trip threshold"""
        for _ in range(times):
            registry._record_failure(name)

    def test_gate_true_suppresses_subscribers_and_event(self, monkeypatch):
        """``asr_backend_disabled`` event_bus publish, the whole alert is"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        disable_calls: list[tuple] = []
        registry.add_backend_disabled_subscriber(lambda name, count: disable_calls.append((name, count)))
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: published.append(msg),
        )

        registry.set_backend_disabled_event_gate(lambda name: True)
        self._trip(registry)

        assert disable_calls == [], (
            f"a suppressing gate must skip the backend-disabled subscriber fan-out, got {disable_calls!r}"
        )
        assert not any(e.get("type") == "asr_backend_disabled" for e in published), (
            f"a suppressing gate must skip the event_bus publish. Got {published!r}."
        )
        # State mutation is NOT gated, the backend must still be
        assert "parakeet" in registry._disabled_backends, (
            "the gate must NOT prevent the circuit breaker from disabling "
            "the backend, only the notification fan-out is suppressed."
        )

    def test_gate_false_keeps_existing_behavior(self, monkeypatch):
        """A gate returning False must preserve the existing behavior —"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        disable_calls: list[tuple] = []
        registry.add_backend_disabled_subscriber(lambda name, count: disable_calls.append((name, count)))
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: published.append(msg),
        )

        registry.set_backend_disabled_event_gate(lambda name: False)
        self._trip(registry)

        assert disable_calls == [("parakeet", 3)], (
            f"a non-suppressing gate must NOT block the backend-disabled subscriber fan-out, got {disable_calls!r}"
        )
        assert any(e.get("type") == "asr_backend_disabled" for e in published), (
            "a non-suppressing gate must NOT block the event_bus publish"
        )

    def test_gate_receives_backend_name(self):
        """The gate must receive the backend name so ModelManager can"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        received: list[str] = []
        registry.set_backend_disabled_event_gate(lambda name: received.append(name) or False)
        self._trip(registry)

        assert received == ["parakeet"], (
            "the backend-disabled gate must receive the backend name "
            f"(matches the subscriber + WARNING log). Got {received!r}."
        )

    def test_clear_gate_restores_publish(self, monkeypatch):
        """``set_backend_disabled_event_gate(None)`` must restore the"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        disable_calls: list[tuple] = []
        registry.add_backend_disabled_subscriber(lambda name, count: disable_calls.append((name, count)))
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: published.append(msg),
        )

        # First trip with a suppressing gate: nothing fires, backend
        registry.set_backend_disabled_event_gate(lambda name: True)
        self._trip(registry)
        assert disable_calls == [] and not published

        registry.set_backend_disabled_event_gate(None)
        registry.reset_failures("parakeet")
        self._trip(registry)

        assert disable_calls == [("parakeet", 3)], (
            f"after clearing the gate, the backend-disabled subscriber fan-out must fire again, got {disable_calls!r}"
        )
        assert any(e.get("type") == "asr_backend_disabled" for e in published), (
            "after clearing the gate, the event_bus publish must fire again"
        )

    def test_gate_exception_fails_open(self, monkeypatch, caplog):
        """A gate that raises must FAIL OPEN, the genuine alert is"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        disable_calls: list[tuple] = []
        registry.add_backend_disabled_subscriber(lambda name, count: disable_calls.append((name, count)))
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: published.append(msg),
        )

        def boom_gate(_name: str) -> bool:
            raise RuntimeError("gate broken")

        registry.set_backend_disabled_event_gate(boom_gate)
        with caplog.at_level("WARNING"):
            self._trip(registry)

        assert disable_calls == [("parakeet", 3)], "a raising gate must fail open, subscribers must still fire"
        assert any(e.get("type") == "asr_backend_disabled" for e in published), (
            "a raising gate must fail open, the event_bus publish must still fire"
        )
        assert any("backend-disabled event gate raised" in rec.message for rec in caplog.records), (
            "the gate exception must be logged (message contains 'backend-disabled event gate raised')"
        )

    def test_backend_disabled_gate_does_not_gate_last_resort(self, monkeypatch):
        """two gates are independent surfaces."""
        registry, _ = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: published.append(msg),
        )

        # Install a backend-disabled gate that suppresses EVERYTHING —
        registry.set_backend_disabled_event_gate(lambda name: True)

        result = registry.get_active()

        assert result is None, "fail-loud: get_active() returns None when only unloaded remains"
        assert notifications == ["parakeet"], (
            "the backend-disabled gate must NOT suppress the last-resort "
            f"subscriber fan-out (scope boundary). Got {notifications!r}."
        )
        assert any(e.get("type") == "asr_last_resort_unloaded" for e in published), (
            "the backend-disabled gate must NOT suppress the last-resort event_bus publish"
        )


class TestLastResortSubscriberApi:
    """the add/remove subscriber API and the"""

    def test_add_and_remove_last_resort_subscriber(self):
        """``add_last_resort_subscriber`` + ``remove_last_resort_subscriber``"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        sub = lambda name: notifications.append(name)  # noqa: E731
        registry.add_last_resort_subscriber(sub)
        assert sub in registry.on_last_resort

        registry.remove_last_resort_subscriber(sub)
        assert sub not in registry.on_last_resort

        registry.get_active()
        assert notifications == [], "Removed subscriber must NOT fire."

    def test_on_last_resort_property_setter_adds_to_set(self):
        """Assigning a callable to ``registry.on_last_resort = fn`` must"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.on_last_resort = lambda name: notifications.append(name)

        registry.get_active()
        assert notifications == ["parakeet"], (
            "assigning a callable to on_last_resort must register it as a subscriber (mirrors on_backend_disabled)."
        )

    def test_on_last_resort_property_setter_none_clears_set(self):
        """Assigning None to ``registry.on_last_resort`` must clear the"""
        registry, _ = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.on_last_resort = lambda name: notifications.append(name)
        assert len(registry.on_last_resort) == 1

        registry.on_last_resort = None
        assert len(registry.on_last_resort) == 0, "Assigning None to on_last_resort must clear the subscriber set."

        registry.get_active()
        assert notifications == [], "No subscribers should fire after None-clear."

    def test_remove_nonexistent_subscriber_is_noop(self):
        """``remove_last_resort_subscriber`` on a non-registered callable"""
        registry, _ = _make_registry_with_only_unloaded_primary()
        # Must not raise:
        registry.remove_last_resort_subscriber(lambda name: None)


class TestLastResortReturnContractPreserved:
    """the notification is ADDITIVE and the fail-loud return (None when"""

    def test_get_active_returns_none_when_last_resort_unloaded(self):
        """The last-resort branch returns None fail-loud (never serves"""
        registry, primary = _make_registry_with_only_unloaded_primary()

        # Add a subscriber (the fix):
        registry.add_last_resort_subscriber(lambda name: None)

        result = registry.get_active()

        assert result is None, (
            "get_active() must return None when only an unloaded backend "
            "remains (fail-loud). Callers take their not-ready path "
            "(toggle re-triggers load, pipeline raises BackendNotLoadedError)."
        )
        assert not primary.is_loaded, "Sanity: the unloaded backend IS unloaded (the trigger condition)."

    def test_get_active_still_returns_none_when_no_backends_registered(self):
        """If no backends are registered at all, ``get_active`` returns"""
        registry = AsrBackendRegistry(_Config("parakeet"))
        # No backends registered.

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        result = registry.get_active()
        assert result is None, "get_active must return None when no backends are registered."
        assert notifications == [], "No notification should fire when no backends are registered."

    def test_latch_starts_false(self):
        """Sanity: the latch is initialized to False in __init__."""
        registry = AsrBackendRegistry(_Config("parakeet"))
        assert registry._last_resort_notified is False, "_last_resort_notified latch must start as False."


class TestLastResortWarningLogOncePerTransition:
    """the notification, it fires at WARNING once per last-resort"""

    @staticmethod
    def _records(caplog) -> list:
        return [r for r in caplog.records if "last-resort" in r.getMessage()]

    def test_warning_fires_once_for_repeated_calls(self, caplog):
        """10 consecutive ``get_active()`` calls while the backend is"""
        import logging

        registry, _ = _make_registry_with_only_unloaded_primary()

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.asr.registry"):
            for _ in range(10):
                registry.get_active()

        records = self._records(caplog)
        assert len(records) == 10, "all 10 calls must produce a log record (first WARNING, rest DEBUG)"
        warnings = [r for r in records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, (
            "the WARNING must fire exactly ONCE per last-resort "
            f"transition, not on every call. Got {len(warnings)} WARNING records."
        )
        debugs = [r for r in records if r.levelno == logging.DEBUG]
        assert len(debugs) == 9, "the 9 repeat calls must log at DEBUG, not WARNING"

    def test_warning_refires_after_recovery(self, caplog):
        """After the backend becomes ready (latch cleared), a new"""
        import logging

        registry, primary = _make_registry_with_only_unloaded_primary()

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.asr.registry"):
            # First transition: one WARNING.
            registry.get_active()
            warnings = [r for r in self._records(caplog) if r.levelno == logging.WARNING]
            assert len(warnings) == 1, "first fall-through must log the WARNING once"

            # Repeats while still broken: no new WARNINGs.
            registry.get_active()
            registry.get_active()
            warnings = [r for r in self._records(caplog) if r.levelno == logging.WARNING]
            assert len(warnings) == 1, "repeats must NOT log additional WARNINGs"

            primary.is_loaded = True
            registry.get_active()
            primary.is_loaded = False
            registry.get_active()
            warnings = [r for r in self._records(caplog) if r.levelno == logging.WARNING]
            assert len(warnings) == 2, (
                "after recovery, the next fall-through must log the "
                f"WARNING again. Got {len(warnings)} WARNING records."
            )
