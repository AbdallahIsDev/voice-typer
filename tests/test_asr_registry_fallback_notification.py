"""XZ-14-06: regression tests for the silent unloaded-backend fallback
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

Post-fix (XZ-14-06), the same last-resort branch fires:

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
"""

from __future__ import annotations

from unittest.mock import MagicMock

from voice_typer.server.asr_registry import AsrBackendRegistry

# ── Helpers ────────────────────────────────────────────────────────────


def _make_unloaded_backend() -> MagicMock:
    """A backend whose ``is_loaded`` is False (the XZ-14-06 trigger condition)."""
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

    This is the XZ-14-06 trigger condition: the configured backend
    isn't loaded, no whisper fallback is registered, and the only
    non-None backend in the dict is the unloaded primary.
    """
    registry = AsrBackendRegistry(_Config(primary_name))
    primary = _make_unloaded_backend()
    registry.register(primary_name, primary)
    return registry, primary


# ── Test classes ───────────────────────────────────────────────────────


class TestLastResortNotificationFires:
    """XZ-14-06: the subscriber + event_bus event fire when get_active()
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

        # Return contract preserved (XZ-14-06 fix is ADDITIVE):
        assert result is primary, (
            "get_active() must still return the last-resort backend "
            "(return contract unchanged by XZ-14-06)."
        )
        # The notification must fire:
        assert notifications == ["parakeet"], (
            "XZ-14-06: on_last_resort subscriber must fire with the "
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
            "XZ-14-06: event_bus.publish must be called with "
            f"type='asr_last_resort_unloaded'. Got {published!r}."
        )
        # The event must include the backend name so the IPC push channel
        # can render a useful message in the renderer.
        last_resort_events = [e for e in published if e.get("type") == "asr_last_resort_unloaded"]
        # DT-16: payload fields now wrapped under the canonical ``data``
        # key (matching every other event_bus.publish caller) so the Rust
        # WS reader + usePythonEvent forwarding actually surface them.
        assert last_resort_events[0]["data"]["backend"] == "parakeet", (
            "XZ-14-06: asr_last_resort_unloaded event must include the "
            f"backend name under data. Got {last_resort_events[0]!r}."
        )
        # The event must include a timestamp so diagnostics can correlate.
        assert "timestamp" in last_resort_events[0]["data"], (
            "XZ-14-06: asr_last_resort_unloaded event must include a "
            "timestamp under data (mirrors asr_backend_disabled)."
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
            "XZ-14-06: subscriber must receive the configured backend "
            "name (matches the WARNING log), not the actual returned "
            f"backend name. Got {notifications!r}."
        )


class TestLastResortNotificationOncePerTransition:
    """XZ-14-06: the notification fires only ONCE per last-resort
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
            "XZ-14-06: notification must fire ONCE per last-resort "
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
        assert notifications == ["parakeet"], (
            "No notification should fire when a ready backend is available."
        )

        # Step 3: backend breaks again — notification must fire AGAIN.
        primary.is_loaded = False
        registry.get_active()
        assert notifications == ["parakeet", "parakeet"], (
            "XZ-14-06: after recovery, the next fall-through must "
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
        assert not registry._last_resort_notified, (
            "XZ-14-06: _record_success must clear the last-resort latch."
        )

        # Next fall-through must re-notify.
        registry.get_active()
        assert notifications == ["parakeet", "parakeet"], (
            "XZ-14-06: after _record_success cleared the latch, the next "
            f"fall-through must re-notify. Got {notifications!r}."
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
            "XZ-14-06: load_with_fallback's whisper-fallback success "
            "path must clear the last-resort latch."
        )

        # Now unload whisper and call get_active — must re-notify.
        whisper_engine.is_loaded = False
        # Parakeet is also still unloaded (its load failed).
        registry.get_active()
        assert notifications == ["parakeet", "parakeet"], (
            "XZ-14-06: after whisper-fallback success cleared the latch, "
            f"the next fall-through must re-notify. Got {notifications!r}."
        )


class TestLastResortNotificationDoesNotFire:
    """XZ-14-06: the notification must NOT fire when there's no need —
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
        assert notifications == [], (
            "No notification should fire when the configured backend is ready."
        )

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
        assert notifications == [], (
            "No notification should fire when whisper fallback is ready."
        )

    def test_no_notification_when_last_resort_backend_is_loaded(self):
        """When ``get_active()`` reaches the last-resort loop but the
        first non-None backend IS loaded, it returns it silently (no
        WARNING log, no notification). The notification is only for the
        *unloaded* last-resort case — the silent-failure case XZ-14-06
        addresses."""
        registry = AsrBackendRegistry(_Config("parakeet"))
        # Only one backend, and it's loaded — but it's NOT the configured
        # backend (parakeet) and NOT whisper. So get_active will fall
        # through to the last-resort loop, find qwen (loaded), return it.
        qwen = _make_loaded_backend()
        registry.register("qwen", qwen)

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        result = registry.get_active()
        assert result is qwen, (
            "last-resort loop must still return the first non-None backend "
            "even if it's loaded."
        )
        assert notifications == [], (
            "No notification should fire when the last-resort backend is "
            "loaded — the notification is only for the UNLOADED case."
        )


class TestLastResortSubscriberDefenceInDepth:
    """XZ-14-06: a subscriber that raises must be logged and skipped —
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
            "XZ-14-06: a buggy subscriber must NOT block the others — "
            f"the good subscriber must still fire. Got {notifications!r}."
        )
        # The buggy subscriber's exception must be logged (defensive
        # visibility — same pattern as _record_failure).
        assert any(
            "on_last_resort subscriber raised" in rec.message
            for rec in caplog.records
        ), (
            "XZ-14-06: a subscriber exception must be logged with the "
            "message 'on_last_resort subscriber raised' so the failure is "
            "visible in the log file."
        )

    def test_event_bus_publish_exception_does_not_break_get_active(
        self, monkeypatch
    ):
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
            "XZ-14-06: get_active() must still return the last-resort "
            "backend even if event_bus.publish raises."
        )
        assert notifications == ["parakeet"], (
            "XZ-14-06: per-registry subscriber must fire INDEPENDENTLY of "
            "the event_bus publish (the two paths are wrapped in separate "
            "try/except)."
        )


class TestLastResortSubscriberApi:
    """XZ-14-06: the add/remove subscriber API and the
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
        assert notifications == [], (
            "Removed subscriber must NOT fire."
        )

    def test_on_last_resort_property_setter_adds_to_set(self):
        """Assigning a callable to ``registry.on_last_resort = fn`` must
        add ``fn`` to the subscriber set (mirrors the legacy
        ``on_backend_disabled`` property-setter pattern)."""
        registry, _ = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.on_last_resort = lambda name: notifications.append(name)

        registry.get_active()
        assert notifications == ["parakeet"], (
            "XZ-14-06: assigning a callable to on_last_resort must "
            "register it as a subscriber (mirrors on_backend_disabled)."
        )

    def test_on_last_resort_property_setter_none_clears_set(self):
        """Assigning None to ``registry.on_last_resort`` must clear the
        subscriber set (mirrors ``on_backend_disabled``)."""
        registry, _ = _make_registry_with_only_unloaded_primary()

        notifications: list[str] = []
        registry.on_last_resort = lambda name: notifications.append(name)
        assert len(registry.on_last_resort) == 1

        registry.on_last_resort = None
        assert len(registry.on_last_resort) == 0, (
            "Assigning None to on_last_resort must clear the subscriber set."
        )

        registry.get_active()
        assert notifications == [], "No subscribers should fire after None-clear."

    def test_remove_nonexistent_subscriber_is_noop(self):
        """``remove_last_resort_subscriber`` on a non-registered callable
        is a no-op (no error)."""
        registry, _ = _make_registry_with_only_unloaded_primary()
        # Must not raise:
        registry.remove_last_resort_subscriber(lambda name: None)


class TestLastResortReturnContractPreserved:
    """XZ-14-06: the fix is ADDITIVE — it adds a notification, it must
    NOT change ``get_active()``'s return value (callers that check
    ``is_loaded`` rely on the existing return contract)."""

    def test_get_active_still_returns_last_resort_backend_when_unloaded(self):
        """The last-resort branch must still return the unloaded backend
        (the existing return contract) — the notification is fired IN
        ADDITION, not instead."""
        registry, primary = _make_registry_with_only_unloaded_primary()

        # Add a subscriber (the XZ-14-06 fix):
        registry.add_last_resort_subscriber(lambda name: None)

        result = registry.get_active()

        assert result is primary, (
            "XZ-14-06: get_active() must still return the last-resort "
            "backend (return contract unchanged). Pre-fix behavior: "
            "callers like active_transcriber() rely on the backend "
            "reference even when is_loaded=False so they can call "
            "backend.transcribe_with_fallback(...) (which silently "
            "returns empty)."
        )
        assert not primary.is_loaded, (
            "Sanity: the returned backend IS unloaded (the trigger condition)."
        )

    def test_get_active_still_returns_none_when_no_backends_registered(self):
        """If no backends are registered at all, ``get_active`` returns
        None (no last-resort loop iteration, no notification)."""
        registry = AsrBackendRegistry(_Config("parakeet"))
        # No backends registered.

        notifications: list[str] = []
        registry.add_last_resort_subscriber(lambda name: notifications.append(name))

        result = registry.get_active()
        assert result is None, (
            "get_active must return None when no backends are registered."
        )
        assert notifications == [], (
            "No notification should fire when no backends are registered."
        )

    def test_latch_starts_false(self):
        """Sanity: the latch is initialized to False in __init__."""
        registry = AsrBackendRegistry(_Config("parakeet"))
        assert registry._last_resort_notified is False, (
            "XZ-14-06: _last_resort_notified latch must start as False."
        )
