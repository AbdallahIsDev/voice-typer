"""regression tests for the silent unloaded-backend fallback
notification.

Pre-fix, ``AsrBackendRegistry.get_active()``'s last-resort branch (the
``for b in list(self._backends.values())`` loop at the bottom) returned
an *unloaded* backend when no ready backend was available and only
logged a WARNING:

    [ASR_REGISTRY] returning unloaded backend <name> (is_loaded=False)
    as last-resort active — transcription may return empty silently

The caller (``ModelManager.active_transcriber`` →
``DictationPipeline._transcribe``) then called
``backend.transcribe_with_fallback(...)`` on the unloaded backend,
which silently returned an empty string. The user got NO tray
notification, NO IPC event, NO visible feedback that voice
transcription wasn't working — only a log line buried in the log file.

Post-fix, the same last-resort branch fires:

  1. Every registered ``on_last_resort`` subscriber with the configured
     backend name (so the app can show a tray notification via the
     same path used for ``load_with_fallback`` failures).
  2. An ``{"type": "asr_last_resort_unloaded", ...}`` event on the
     global ``event_bus`` (mirrors the ``asr_backend_disabled`` event
     published from ``_record_failure``).

The notification fires only ONCE per last-resort transition (latch
``_last_resort_notified``). The latch resets when a ready backend
becomes available again (``get_active`` success branch) or when a
backend successfully loads (``_record_success`` /
``load_with_fallback`` whisper-fallback success) — so a recovery →
re-fallback sequence re-notifies the user.

The WARNING log line is gated by the SAME latch: it fires at
WARNING once per transition and drops to DEBUG on repeats. This stops
the renderer's 15s ``get_status`` health probe from flooding the log
with identical lines while the backend stays unloaded (e.g. the model
is not downloaded).

These tests verify:
  - The subscriber fires when get_active() hits the last-resort branch.
  - The subscriber fires ONLY ONCE per transition (not on every call).
  - The subscriber does NOT fire when a ready backend is available.
  - The subscriber does NOT fire when the last-resort backend is loaded.
  - The latch resets on recovery so a re-fallback re-notifies.
  - The event_bus event is published with the correct payload.
  - A subscriber that raises does NOT block the others (defence in depth).
  - The return value of get_active() is unchanged (still returns the
    last-resort backend — preserves the existing return contract).
  - The add/remove subscriber API and the backward-compatible property
    setter both work.
  - The WARNING log fires ONCE per transition; repeats are DEBUG.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from voice_typer.server.asr_registry import AsrBackendRegistry

# ── Helpers ────────────────────────────────────────────────────────────


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
    """Minimal config stub — only ``asr_backend`` is read by ``get_active``."""

    def __init__(self, asr_backend: str = "parakeet") -> None:
        self.asr_backend = asr_backend


def _make_registry_with_only_unloaded_primary(
    *, primary_name: str = "parakeet"
) -> tuple[AsrBackendRegistry, MagicMock]:
    """Construct a registry whose ONLY backend is the configured primary,
    and that primary is unloaded. ``get_active()`` will fall through to
    the last-resort branch and return the unloaded primary.

    This is the trigger condition: the configured backend
    isn't loaded, no whisper fallback is registered, and the only
    non-None backend in the dict is the unloaded primary.
    """
    registry = AsrBackendRegistry(_Config(primary_name))
    primary = _make_unloaded_backend()
    registry.register(primary_name, primary)
    return registry, primary


# ── Test classes ───────────────────────────────────────────────────────


class TestLastResortNotificationFires:
    """the subscriber + event_bus event fire when get_active()
    falls through to an unloaded last-resort backend."""

    def test_subscriber_fires_when_get_active_hits_last_resort_branch(self):
        """A registered ``on_last_resort`` subscriber must be called with
        the configured backend name when ``get_active()`` returns an
        unloaded last-resort backend.

        Pre-fix, this branch only logged a WARNING — the subscriber
        never fired and the user got no tray notification.
        """
        registry, primary = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        result = registry.get_active()

        # Return contract preserved ( fix is ADDITIVE):
        assert result is primary, "get_active() must still return the last-resort backend (return contract unchanged)."
        # The notification must fire:
        assert notifications == ["parakeet"], (
            "on_last_resort subscriber must fire with the "
            f"configured backend name when get_active() falls through to "
            f"the unloaded last-resort backend. Got {notifications!r}."
        )

    def test_event_bus_event_published_on_last_resort(self, monkeypatch):
        """An ``asr_last_resort_unloaded`` event must be published on the
        global ``event_bus`` so the IPC push channel and any diagnostics
        aggregator are notified independently of the per-registry
        subscribers (mirrors the ``asr_backend_disabled`` event from
        ``_record_failure``)."""
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
        # can render a useful message in the renderer.
        last_resort_events = [e for e in published if e.get("type") == "asr_last_resort_unloaded"]
        # payload fields now wrapped under the canonical ``data``
        # key (matching every other event_bus.publish caller) so the Rust
        # WS reader + usePythonEvent forwarding actually surface them.
        assert last_resort_events[0]["data"]["backend"] == "parakeet", (
            f"asr_last_resort_unloaded event must include the backend name under data. Got {last_resort_events[0]!r}."
        )
        # The event must include a timestamp so diagnostics can correlate.
        assert "timestamp" in last_resort_events[0]["data"], (
            "asr_last_resort_unloaded event must include a timestamp under data (mirrors asr_backend_disabled)."
        )

    def test_subscriber_receives_configured_backend_name_not_actual_backend_name(self):
        """The subscriber must receive the *configured* backend name
        (``config.asr_backend``), matching the WARNING log message —
        NOT the name of whatever backend the loop happened to return.

        Pre-fix the WARNING used ``name`` (the configured backend),
        which is what the user picked in Settings. The notification
        must do the same so the tray message makes sense to the user
        (e.g. "Voice Typer: Active backend 'parakeet' is not loaded").
        """
        # Configure 'qwen' as the active backend, but only register an
        # unloaded 'whisper' backend. The last-resort loop will return
        # whisper, but the notification must say 'qwen' (the configured
        # backend that the user picked — matching the WARNING log).
        registry = AsrBackendRegistry(_Config("qwen"))
        whisper = _make_unloaded_backend()
        registry.register("whisper", whisper)

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        result = registry.get_active()
        assert result is whisper
        assert notifications == ["qwen"], (
            "subscriber must receive the configured backend "
            "name (matches the WARNING log), not the actual returned "
            f"backend name. Got {notifications!r}."
        )


class TestLastResortNotificationOncePerTransition:
    """the notification fires only ONCE per last-resort
    transition — not on every ``get_active()`` call while the registry
    is stuck in the last-resort state. The latch resets when a ready
    backend becomes available (re-fallback re-notifies)."""

    def test_notification_fires_only_once_for_repeated_calls(self):
        """Multiple ``get_active()`` calls in a row (e.g. from
        ``DictationPipeline._transcribe`` running every dictation cycle)
        must NOT spam the tray — the notification fires once and is
        suppressed until recovery."""
        registry, _ = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        # Call get_active() 10 times — simulates 10 dictation cycles
        # while the backend is broken.
        for _ in range(10):
            registry.get_active()

        assert notifications == ["parakeet"], (
            "notification must fire ONCE per last-resort "
            f"transition (latch), not on every get_active() call. "
            f"Got {len(notifications)} notifications: {notifications!r}."
        )

    def test_latch_resets_when_ready_backend_becomes_available(self):
        """When ``get_active()`` finds a ready backend (the configured
        primary or the whisper fallback), the latch must reset so a
        SUBSEQUENT fall-through re-notifies the user.

        Recovery → re-fallback sequence:
          1. Last-resort fallback → notification fires, latch set.
          2. Backend becomes ready, get_active() returns ready backend,
             latch cleared.
          3. Backend breaks again, get_active() falls through →
             notification fires AGAIN (latch was cleared in step 2).
        """
        registry, primary = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        # Step 1: first fall-through — notification fires.
        registry.get_active()
        assert notifications == ["parakeet"]

        # Step 2: backend becomes ready (e.g. user clicked "Retry load").
        primary.is_loaded = True
        result = registry.get_active()
        assert result is primary, "ready configured backend must be returned"
        # No new notification during recovery:
        assert notifications == ["parakeet"], "No notification should fire when a ready backend is available."

        # Step 3: backend breaks again — notification must fire AGAIN.
        primary.is_loaded = False
        registry.get_active()
        assert notifications == ["parakeet", "parakeet"], (
            "after recovery, the next fall-through must "
            f"re-notify (latch was cleared by the ready-backend branch). "
            f"Got {notifications!r}."
        )

    def test_latch_resets_on_record_success(self):
        """``_record_success(name)`` (called by ``load_with_fallback``
        on primary-backend load success) must clear the latch so a
        subsequent fall-through re-notifies.

        This covers the production recovery path: user clicks "Retry
        load" → ``load_with_fallback`` succeeds → ``_record_success``
        fires → latch cleared → if the backend breaks again later, the
        next ``get_active()`` fall-through re-notifies.
        """
        registry, primary = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        # First fall-through — notification fires.
        registry.get_active()
        assert notifications == ["parakeet"]

        # Simulate a successful primary load: _record_success is called
        # by load_with_fallback on success. Even though the backend
        # object is still unloaded in this test (we don't actually call
        # load()), _record_success must clear the latch regardless.
        registry._record_success("parakeet")
        # The latch must now be False — verified by the next fall-through
        # firing a NEW notification.
        assert not registry._last_resort_notified, "_record_success must clear the last-resort latch."

        # Next fall-through must re-notify.
        registry.get_active()
        assert notifications == ["parakeet", "parakeet"], (
            f"after _record_success cleared the latch, the next fall-through must re-notify. Got {notifications!r}."
        )

    def test_latch_resets_on_whisper_fallback_load_success(self, monkeypatch):
        """``load_with_fallback``'s whisper-fallback success path must
        clear the latch (so a subsequent fall-through re-notifies).

        This covers the production path: primary backend fails to load
        → ``load_with_fallback`` falls back to whisper → whisper loads
        successfully → user is now in a recovered state → if whisper
        later unloads and ``get_active`` falls through, the notification
        must re-fire.
        """
        # Set up: primary parakeet (unloaded, load fails), no whisper
        # registered yet. load_with_fallback will construct + load whisper.
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
        # TranscriptionEngine module.
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
        # success path.
        assert not registry._last_resort_notified, (
            "load_with_fallback's whisper-fallback success path must clear the last-resort latch."
        )

        # Now unload whisper and call get_active — must re-notify.
        whisper_engine.is_loaded = False
        # Parakeet is also still unloaded (its load failed).
        registry.get_active()
        assert notifications == ["parakeet", "parakeet"], (
            "after whisper-fallback success cleared the latch, "
            f"the next fall-through must re-notify. Got {notifications!r}."
        )


class TestLastResortNotificationDoesNotFire:
    """the notification must NOT fire when there's no need —
    i.e. when a ready backend is available, or when the last-resort
    backend is actually loaded."""

    def test_no_notification_when_configured_backend_is_ready(self):
        """When the configured backend is loaded, ``get_active`` returns
        it directly — no last-resort branch, no notification."""
        registry = AsrBackendRegistry(_Config("parakeet"))
        parakeet = _make_loaded_backend()
        registry.register("parakeet", parakeet)

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        result = registry.get_active()
        assert result is parakeet
        assert notifications == [], "No notification should fire when the configured backend is ready."

    def test_no_notification_when_whisper_fallback_is_ready(self):
        """When the configured backend isn't ready but whisper is,
        ``get_active`` returns whisper (the second branch) — no
        last-resort branch, no notification."""
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
        """When ``get_active()`` reaches the last-resort loop but the
        first non-None backend IS loaded, it returns it silently (no
        WARNING log, no notification). The notification is only for the
        *unloaded* last-resort case — the silent-failure case addresses."""
        registry = AsrBackendRegistry(_Config("parakeet"))
        # Only one backend, and it's loaded — but it's NOT the configured
        # backend (parakeet) and NOT whisper. So get_active will fall
        # through to the last-resort loop, find qwen (loaded), return it.
        qwen = _make_loaded_backend()
        registry.register("qwen", qwen)

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        result = registry.get_active()
        assert result is qwen, "last-resort loop must still return the first non-None backend even if it's loaded."
        assert notifications == [], (
            "No notification should fire when the last-resort backend is "
            "loaded — the notification is only for the UNLOADED case."
        )


class TestLastResortSubscriberDefenceInDepth:
    """a subscriber that raises must be logged and skipped —
    one buggy subscriber must NOT block the others (same contract as
    ``_record_failure``'s subscriber loop)."""

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

        assert result is not None, "return contract preserved"
        assert notifications == ["parakeet"], (
            "a buggy subscriber must NOT block the others — "
            f"the good subscriber must still fire. Got {notifications!r}."
        )
        # The buggy subscriber's exception must be logged (defensive
        # visibility — same pattern as _record_failure).
        assert any("on_last_resort subscriber raised" in rec.message for rec in caplog.records), (
            "a subscriber exception must be logged with the "
            "message 'on_last_resort subscriber raised' so the failure is "
            "visible in the log file."
        )

    def test_event_bus_publish_exception_does_not_break_get_active(self, monkeypatch):
        """If ``event_bus.publish`` raises, ``get_active`` must still
        return the last-resort backend (return contract preserved) and
        the per-registry subscriber must still have fired (defence in
        depth — the two notification paths are independent)."""
        registry, _ = _make_registry_with_only_unloaded_primary()

        def boom_publish(_msg: dict) -> bool:
            raise RuntimeError("event_bus broken")

        monkeypatch.setattr("voice_typer.server.event_bus.publish", boom_publish)

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        # Must not raise.
        result = registry.get_active()

        assert result is not None, (
            "get_active() must still return the last-resort backend even if event_bus.publish raises."
        )
        assert notifications == ["parakeet"], (
            "per-registry subscriber must fire INDEPENDENTLY of "
            "the event_bus publish (the two paths are wrapped in separate "
            "try/except)."
        )


class TestLastResortEventGate:
    """the event_bus publish can be suppressed by an installed gate
    (ModelManager wires it so the renderer toast matches the tray
    notification's suppressions — the toast can't see them otherwise).

    The gate is checked at the top of ``fire_last_resort_subscribers``:
    returning True suppresses the ENTIRE fan-out (subscribers AND the
    ``asr_last_resort_unloaded`` event_bus publish); returning False /
    no gate preserves the existing behavior exactly."""

    def test_gate_true_suppresses_subscribers_and_event(self, monkeypatch):
        """A gate returning True must suppress BOTH the subscriber
        fan-out and the event_bus publish (the whole alert is skipped)."""
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

        # Return contract preserved (the fix is additive).
        assert result is not None, "get_active() must still return the last-resort backend"
        assert notifications == [], f"a suppressing gate must skip the subscriber fan-out — got {notifications!r}"
        assert not any(e.get("type") == "asr_last_resort_unloaded" for e in published), (
            f"a suppressing gate must skip the event_bus publish. Got {published!r}."
        )

    def test_gate_false_keeps_existing_behavior(self, monkeypatch):
        """A gate returning False must preserve the existing behavior —
        subscribers fire AND the event is published (the gate only
        suppresses, never blocks a genuine alert)."""
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
        """The gate must receive the configured backend name (same value
        as the subscribers + WARNING log) so ModelManager can check the
        per-backend deliberate-unload flag."""
        registry, _ = _make_registry_with_only_unloaded_primary()

        received: list[str] = []
        registry.set_last_resort_event_gate(lambda name: received.append(name) or False)
        registry.get_active()

        assert received == ["parakeet"], (
            "the event gate must receive the configured backend name "
            f"(matches the subscriber + WARNING log). Got {received!r}."
        )

    def test_clear_gate_restores_publish(self, monkeypatch):
        """``set_last_resort_event_gate(None)`` must restore the default
        publish behavior (used if a future caller wants to detach the
        suppression)."""
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
        """The gate is scoped to the LAST-RESORT fan-out only — it must
        NOT suppress the ``asr_backend_disabled`` event published from
        ``_record_failure`` (a completely different notification path).
        Locks the scope boundary so a future refactor can't silently
        gate the wrong event."""
        registry, _ = _make_registry_with_only_unloaded_primary()

        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: published.append(msg),
        )

        # Install a gate that suppresses EVERYTHING — the
        # asr_backend_disabled publish must still fire regardless.
        registry.set_last_resort_event_gate(lambda name: True)

        # The breaker publishes asr_backend_disabled only once the
        # consecutive-failure counter trips (_MAX_CONSECUTIVE_FAILURES=3).
        for _ in range(3):
            registry._record_failure("parakeet")

        assert any(e.get("type") == "asr_backend_disabled" for e in published), (
            "the last-resort event gate must NOT suppress the "
            "asr_backend_disabled publish from _record_failure "
            f"(scope boundary). Got {published!r}."
        )

    def test_gate_exception_fails_open(self, monkeypatch, caplog):
        """A gate that raises must FAIL OPEN — the genuine alert is
        still delivered (subscribers fire + event published), and the
        exception is logged so the broken gate is diagnosable. A
        suppressing gate is best-effort, never a safety interlock."""
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

        assert result is not None, "return contract preserved"
        assert notifications == ["parakeet"], "a raising gate must fail open — subscribers must still fire"
        assert any(e.get("type") == "asr_last_resort_unloaded" for e in published), (
            "a raising gate must fail open — the event_bus publish must still fire"
        )
        assert any("event gate raised" in rec.message for rec in caplog.records), (
            "the gate exception must be logged (message contains 'event gate raised')"
        )


class TestBackendDisabledEventGate:
    """the ``asr_backend_disabled`` event_bus publish can be suppressed
    by an installed gate (ModelManager wires it so the renderer event
    matches the tray's deliberate-unload suppressions — a backend that
    was deliberately unloaded / is mid-load must not publish a spurious
    'disabled' event when the switch's own transient failure trips the
    breaker).

    Mirrors ``TestLastResortEventGate`` exactly, but for
    ``set_backend_disabled_event_gate`` + the ``_record_failure`` trip
    fan-out: returning True suppresses the ENTIRE fan-out (subscribers
    AND the ``asr_backend_disabled`` event_bus publish) while the
    circuit-breaker STATE mutation (disabling the backend) still
    happens; returning False / no gate preserves the existing behavior
    exactly."""

    @staticmethod
    def _trip(registry, name: str = "parakeet", times: int = 3) -> None:
        """Drive ``_record_failure`` past the trip threshold
        (``_MAX_CONSECUTIVE_FAILURES = 3``)."""
        for _ in range(times):
            registry._record_failure(name)

    def test_gate_true_suppresses_subscribers_and_event(self, monkeypatch):
        """A gate returning True must suppress BOTH the
        ``on_backend_disabled`` subscriber fan-out and the
        ``asr_backend_disabled`` event_bus publish — the whole alert is
        skipped, but the backend is still disabled (state mutation not
        gated)."""
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
            f"a suppressing gate must skip the backend-disabled subscriber fan-out — got {disable_calls!r}"
        )
        assert not any(e.get("type") == "asr_backend_disabled" for e in published), (
            f"a suppressing gate must skip the event_bus publish. Got {published!r}."
        )
        # State mutation is NOT gated — the backend must still be
        # disabled so load_with_fallback skips it (gate only suppresses
        # the notification surface).
        assert "parakeet" in registry._disabled_backends, (
            "the gate must NOT prevent the circuit breaker from disabling "
            "the backend — only the notification fan-out is suppressed."
        )

    def test_gate_false_keeps_existing_behavior(self, monkeypatch):
        """A gate returning False must preserve the existing behavior —
        subscribers fire AND the event is published (the gate only
        suppresses, never blocks a genuine alert)."""
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
            f"a non-suppressing gate must NOT block the backend-disabled subscriber fan-out — got {disable_calls!r}"
        )
        assert any(e.get("type") == "asr_backend_disabled" for e in published), (
            "a non-suppressing gate must NOT block the event_bus publish"
        )

    def test_gate_receives_backend_name(self):
        """The gate must receive the backend name so ModelManager can
        check the per-backend deliberate-unload flag."""
        registry, _ = _make_registry_with_only_unloaded_primary()

        received: list[str] = []
        registry.set_backend_disabled_event_gate(lambda name: received.append(name) or False)
        self._trip(registry)

        assert received == ["parakeet"], (
            "the backend-disabled gate must receive the backend name "
            f"(matches the subscriber + WARNING log). Got {received!r}."
        )

    def test_clear_gate_restores_publish(self, monkeypatch):
        """``set_backend_disabled_event_gate(None)`` must restore the
        default publish behavior."""
        registry, _ = _make_registry_with_only_unloaded_primary()

        disable_calls: list[tuple] = []
        registry.add_backend_disabled_subscriber(lambda name, count: disable_calls.append((name, count)))
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: published.append(msg),
        )

        # First trip with a suppressing gate: nothing fires, backend
        # disabled.
        registry.set_backend_disabled_event_gate(lambda name: True)
        self._trip(registry)
        assert disable_calls == [] and not published

        # Clear the gate + re-enable the backend, then trip again: the
        # fan-out must fire.
        registry.set_backend_disabled_event_gate(None)
        registry.reset_failures("parakeet")
        self._trip(registry)

        assert disable_calls == [("parakeet", 3)], (
            f"after clearing the gate, the backend-disabled subscriber fan-out must fire again — got {disable_calls!r}"
        )
        assert any(e.get("type") == "asr_backend_disabled" for e in published), (
            "after clearing the gate, the event_bus publish must fire again"
        )

    def test_gate_exception_fails_open(self, monkeypatch, caplog):
        """A gate that raises must FAIL OPEN — the genuine alert is
        still delivered (subscribers fire + event published), and the
        exception is logged so the broken gate is diagnosable."""
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

        assert disable_calls == [("parakeet", 3)], "a raising gate must fail open — subscribers must still fire"
        assert any(e.get("type") == "asr_backend_disabled" for e in published), (
            "a raising gate must fail open — the event_bus publish must still fire"
        )
        assert any("backend-disabled event gate raised" in rec.message for rec in caplog.records), (
            "the gate exception must be logged (message contains 'backend-disabled event gate raised')"
        )

    def test_backend_disabled_gate_does_not_gate_last_resort(self, monkeypatch):
        """Scope boundary (reverse direction of
        ``test_gate_does_not_gate_asr_backend_disabled``): the
        backend-disabled gate must NOT suppress the
        ``asr_last_resort_unloaded`` fan-out from ``get_active`` — the
        two gates are independent surfaces."""
        registry, _ = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: published.append(msg),
        )

        # Install a backend-disabled gate that suppresses EVERYTHING —
        # the last-resort fan-out must still fire regardless.
        registry.set_backend_disabled_event_gate(lambda name: True)

        result = registry.get_active()

        assert result is not None, "get_active() must still return the last-resort backend"
        assert notifications == ["parakeet"], (
            "the backend-disabled gate must NOT suppress the last-resort "
            f"subscriber fan-out (scope boundary). Got {notifications!r}."
        )
        assert any(e.get("type") == "asr_last_resort_unloaded" for e in published), (
            "the backend-disabled gate must NOT suppress the last-resort event_bus publish"
        )


class TestLastResortSubscriberApi:
    """the add/remove subscriber API and the
    backward-compatible ``on_last_resort`` property setter."""

    def test_add_and_remove_last_resort_subscriber(self):
        """``add_last_resort_subscriber`` + ``remove_last_resort_subscriber``
        must register and unregister subscribers."""
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
        """Assigning a callable to ``registry.on_last_resort = fn`` must
        add ``fn`` to the subscriber set (mirrors the legacy
        ``on_backend_disabled`` property-setter pattern)."""
        registry, _ = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.on_last_resort = lambda name: notifications.append(name)

        registry.get_active()
        assert notifications == ["parakeet"], (
            "assigning a callable to on_last_resort must register it as a subscriber (mirrors on_backend_disabled)."
        )

    def test_on_last_resort_property_setter_none_clears_set(self):
        """Assigning None to ``registry.on_last_resort`` must clear the
        subscriber set (mirrors ``on_backend_disabled``)."""
        registry, _ = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.on_last_resort = lambda name: notifications.append(name)
        assert len(registry.on_last_resort) == 1

        registry.on_last_resort = None
        assert len(registry.on_last_resort) == 0, "Assigning None to on_last_resort must clear the subscriber set."

        registry.get_active()
        assert notifications == [], "No subscribers should fire after None-clear."

    def test_remove_nonexistent_subscriber_is_noop(self):
        """``remove_last_resort_subscriber`` on a non-registered callable
        is a no-op (no error)."""
        registry, _ = _make_registry_with_only_unloaded_primary()
        # Must not raise:
        registry.remove_last_resort_subscriber(lambda name: None)


class TestLastResortReturnContractPreserved:
    """the fix is ADDITIVE — it adds a notification, it must
    NOT change ``get_active()``'s return value (callers that check
    ``is_loaded`` rely on the existing return contract)."""

    def test_get_active_still_returns_last_resort_backend_when_unloaded(self):
        """The last-resort branch must still return the unloaded backend
        (the existing return contract) — the notification is fired IN
        ADDITION, not instead."""
        registry, primary = _make_registry_with_only_unloaded_primary()

        # Add a subscriber (the  fix):
        registry.add_last_resort_subscriber(lambda name: None)

        result = registry.get_active()

        assert result is primary, (
            "get_active() must still return the last-resort "
            "backend (return contract unchanged). Pre-fix behavior: "
            "callers like active_transcriber() rely on the backend "
            "reference even when is_loaded=False so they can call "
            "backend.transcribe_with_fallback(...) (which silently "
            "returns empty)."
        )
        assert not primary.is_loaded, "Sanity: the returned backend IS unloaded (the trigger condition)."

    def test_get_active_still_returns_none_when_no_backends_registered(self):
        """If no backends are registered at all, ``get_active`` returns
        None (no last-resort loop iteration, no notification)."""
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
    """The WARNING log line is gated by the SAME one-shot latch as
    the notification — it fires at WARNING once per last-resort
    transition, then drops to DEBUG on repeats.

    Regression: the renderer's 15s ``get_status`` health probe calls
    ``get_active()`` continuously while the backend stays unloaded
    (e.g. the model is not downloaded). Pre-fix, every call logged the
    WARNING unconditionally — ~1,500 identical lines over a 2-hour
    session in the real log. Post-fix, the first call logs at WARNING
    (so the state is visible in the log) and repeats are DEBUG until a
    ready backend / successful load resets the latch.
    """

    @staticmethod
    def _records(caplog) -> list:
        return [r for r in caplog.records if "unloaded backend" in r.getMessage()]

    def test_warning_fires_once_for_repeated_calls(self, caplog):
        """10 consecutive ``get_active()`` calls while the backend is
        stuck unloaded must produce exactly ONE WARNING record (the
        rest are DEBUG)."""
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
        """After the backend becomes ready (latch cleared), a new
        fall-through must log the WARNING again — the one-shot latch
        must not suppress the diagnostic forever."""
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

            # Recovery: backend becomes ready (clears the latch via the
            # ready-backend branch), then breaks again → WARNING re-fires.
            primary.is_loaded = True
            registry.get_active()
            primary.is_loaded = False
            registry.get_active()
            warnings = [r for r in self._records(caplog) if r.levelno == logging.WARNING]
            assert len(warnings) == 2, (
                "after recovery, the next fall-through must log the "
                f"WARNING again. Got {len(warnings)} WARNING records."
            )
