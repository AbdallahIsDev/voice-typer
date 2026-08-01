"""S2-CR-57: direct unit tests for ``voice_typer/server/hotkey_dispatcher.py``.

Prior to this file, ``HotkeyDispatcher`` was tested only indirectly
through ``VoiceTyperApp`` integration tests in ``tests/app/test_hotkeys.py``
and ``tests/test_app.py``. The most complex method, ``_on_esc_release()``
(ESC-KEYUP-FIX), and the XZ-R17-02 shutdown guards inside the dictation /
repaste callbacks had ZERO direct unit tests. A regression in
``_on_esc_release`` (e.g. forgetting to publish ``hotkey_capture_cancel``)
would silently leave the frontend stuck in capture mode after ESC
release — and no test would have caught it.

This file adds focused unit tests for the three callback helpers,
exercising every branch of ``_on_esc_release`` and the shutdown-guard
paths of the dictation / repaste callbacks. Each test uses a minimal
mock app (no real ``VoiceTyperApp``, no real backends, no heavy imports)
so the dispatcher's branching logic is exercised in isolation.

Existing coverage in ``tests/app/test_hotkeys.py`` covers the
ownership-guard paths of the callbacks (HOTKEY-FIX-001). The tests here
are complementary — they focus on the paths that file does NOT cover:

* ``_on_esc_release`` no-op when the pending flag is not set
* ``_on_esc_release`` publishes ``hotkey_capture_cancel`` + resets
  ownership + clears the legacy ``_esc_cancel_paused`` alias
* ``_on_esc_release`` clears the release callback on the backend so
  the next ESC press doesn't re-fire the release handler
* ``_on_esc_release`` tolerates a missing backend (no AttributeError)
* dictation callback is a no-op during app shutdown
  (``_shutting_down=True``) — even when ownership is normal
* repaste callback is a no-op during app shutdown
* ESC callback is a no-op during app shutdown
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from voice_typer.server import event_bus
from voice_typer.server.branding import APP_NAME
from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher
from voice_typer.server.keyboard_ownership import keyboard_ownership

# ─── Fixtures ────────────────────────────────────────────────────────────


def _make_mock_app() -> SimpleNamespace:
    """Build a minimal mock app satisfying the HotkeyDispatcher contract.

    The real ``VoiceTyperApp`` pulls in sounddevice / faster_whisper /
    pynput / pystray / PIL / pyperclip and a cross-process config lock —
    none of which we need to exercise the dispatcher's branching logic.
    """
    app = SimpleNamespace()
    app.config = SimpleNamespace(
        hotkey="<f2>",
        recording_mode="toggle",
        esc_cancel_enabled=False,
        repaste_hotkey=None,
    )
    app.tray = MagicMock()
    app._stop_dictation = MagicMock()
    app.toggle_dictation = MagicMock()
    app._cancel_dictation = MagicMock()
    app.repaste_last = MagicMock()
    # Legacy alias that ``_on_esc_release`` keeps in sync with the
    # canonical owner. Real app initializes this to ``False``.
    app._esc_cancel_paused = False
    # shutdown flag the dictation/repaste/ESC callbacks
    # consult before doing anything else.
    app._shutting_down = False
    return app


@pytest.fixture
def dispatcher() -> HotkeyDispatcher:
    """Build a ``HotkeyDispatcher`` backed by a minimal mock app.

    The mock app is also attached as ``dispatcher._app`` so individual
    tests can mutate it (e.g. flip ``_shutting_down`` to True).
    """
    app = _make_mock_app()
    return HotkeyDispatcher(app)


@pytest.fixture(autouse=True)
def _reset_keyboard_ownership():
    """Ensure each test starts with ``keyboard_ownership`` in the
    ``normal`` state and ends the same way (no cross-test leakage)."""
    keyboard_ownership().reset()
    yield
    keyboard_ownership().reset()


# ─── _on_esc_release ────────────────────────────────────────────────────


class TestOnEscRelease:
    """Direct unit tests for ``HotkeyDispatcher._on_esc_release``.

    The method is the most complex in the dispatcher — it's the
    key-up handler installed by ``_esc_callback`` when ESC is pressed
    during hotkey capture. Its contract has four observable side
    effects that the frontend depends on:

    1. ``keyboard_ownership`` transitions from ``hotkey_capture`` →
       ``normal`` (otherwise the next hotkey press is silently
       dropped).
    2. ``hotkey_capture_cancel`` is published to ``event_bus`` (the
       frontend's HotkeyPicker listens for this to exit capture mode).
    3. The legacy ``_esc_cancel_paused`` alias is cleared (ESC-FIX-001
       divergence fix).
    4. The release callback is uninstalled from the ESC backend so a
       subsequent ESC press during normal operation does not re-fire
       the release handler.
    """

    def test_noop_when_pending_event_not_set(self, dispatcher: HotkeyDispatcher):
        """If the ESC key-down never set the pending flag (e.g. release
        fired spuriously outside a capture session), the method must
        short-circuit and NOT touch ownership / event_bus / backend."""
        dispatcher._esc_pending_capture_exit_event.clear()
        backend = MagicMock()
        dispatcher._esc_backend = backend

        # Capture event_bus state to ensure NO publication.
        published: list[dict] = []
        orig_publish = event_bus.publish
        event_bus.publish = lambda event: published.append(event)  # type: ignore[assignment]
        try:
            dispatcher._on_esc_release()
        finally:
            event_bus.publish = orig_publish  # type: ignore[assignment]

        # No side effects.
        assert keyboard_ownership().current_owner() == "normal"
        assert published == []
        # Backend's set_on_release must NOT have been called.
        backend.set_on_release.assert_not_called()

    def test_publishes_cancel_event_and_resets_ownership(self, dispatcher: HotkeyDispatcher):
        """Happy path: pending flag is set → ownership reset to
        ``normal``, ``hotkey_capture_cancel`` published, legacy alias
        cleared, release callback uninstalled."""
        # Pre-conditions: ownership is hotkey_capture, pending flag is
        # set, legacy alias is True (simulating divergence).
        keyboard_ownership().set_owner("hotkey_capture", reason="test setup")
        dispatcher._esc_pending_capture_exit_event.set()
        dispatcher._app._esc_cancel_paused = True

        backend = MagicMock()
        dispatcher._esc_backend = backend

        published: list[dict] = []
        orig_publish = event_bus.publish
        event_bus.publish = lambda event: published.append(event)  # type: ignore[assignment]
        try:
            dispatcher._on_esc_release()
        finally:
            event_bus.publish = orig_publish  # type: ignore[assignment]

        # (1) Ownership back to normal.
        assert keyboard_ownership().current_owner() == "normal"
        # (2) Cancel event published exactly once with the right shape.
        assert len(published) == 1
        assert published[0]["type"] == "hotkey_capture_cancel"
        # (3) Legacy alias cleared.
        assert dispatcher._app._esc_cancel_paused is False
        # (4) Pending flag cleared (so a second release is a no-op).
        assert dispatcher._esc_pending_capture_exit_event.is_set() is False
        # (5) Release callback uninstalled from the backend.
        backend.set_on_release.assert_called_once_with(None)

    def test_tolerates_missing_backend(self, dispatcher: HotkeyDispatcher):
        """If ``_esc_backend`` is None (e.g. ESC backend stopped
        between key-down and key-up), the method must NOT raise — it
        still resets ownership and publishes the cancel event so the
        frontend exits capture mode."""
        keyboard_ownership().set_owner("hotkey_capture", reason="test setup")
        dispatcher._esc_pending_capture_exit_event.set()
        dispatcher._esc_backend = None

        published: list[dict] = []
        orig_publish = event_bus.publish
        event_bus.publish = lambda event: published.append(event)  # type: ignore[assignment]
        try:
            dispatcher._on_esc_release()  # must not raise
        finally:
            event_bus.publish = orig_publish  # type: ignore[assignment]

        assert keyboard_ownership().current_owner() == "normal"
        assert len(published) == 1
        assert published[0]["type"] == "hotkey_capture_cancel"

    def test_release_callback_uninstall_failure_is_swallowed(self, dispatcher: HotkeyDispatcher):
        """If ``backend.set_on_release(None)`` raises (e.g. the backend
        was already torn down), the method must not propagate — the
        cancel event has already been published and ownership already
        reset, so the user-visible state is correct."""
        keyboard_ownership().set_owner("hotkey_capture", reason="test setup")
        dispatcher._esc_pending_capture_exit_event.set()

        backend = MagicMock()
        backend.set_on_release.side_effect = RuntimeError("backend torn down")
        dispatcher._esc_backend = backend

        # Must not raise.
        dispatcher._on_esc_release()

        # Side effects still applied.
        assert keyboard_ownership().current_owner() == "normal"
        assert dispatcher._app._esc_cancel_paused is False


# Shutdown guards () ────────────────────────────────────────


class TestShutdownGuards:
    """XZ-R17-02: all three hotkey callbacks must short-circuit when
    ``app._shutting_down`` is True.

    Without these guards, a callback firing during the 5-second
    ``stop_all()`` window could re-enter ``toggle_dictation`` /
    ``repaste_last`` / ``_cancel_dictation`` and undo shutdown cleanup
    (racing ``recorder.stop()`` / ``discard()``).
    """

    def test_dictation_callback_noop_during_shutdown(self, dispatcher: HotkeyDispatcher):
        callback = dispatcher._make_dictation_callback()
        dispatcher._app._shutting_down = True
        # Ownership is normal — without the shutdown guard this would
        # fire toggle_dictation. With the guard, it must be a no-op.
        keyboard_ownership().set_owner("normal", reason="test")

        callback()

        dispatcher._app.toggle_dictation.assert_not_called()

    def test_dictation_callback_fires_when_not_shutting_down(self, dispatcher: HotkeyDispatcher):
        """Sanity: the guard does not false-positive."""
        callback = dispatcher._make_dictation_callback()
        dispatcher._app._shutting_down = False
        keyboard_ownership().set_owner("normal", reason="test")

        callback()

        dispatcher._app.toggle_dictation.assert_called_once()

    def test_repaste_callback_noop_during_shutdown(self, dispatcher: HotkeyDispatcher):
        callback = dispatcher._make_repaste_callback()
        dispatcher._app._shutting_down = True
        keyboard_ownership().set_owner("normal", reason="test")

        callback()

        dispatcher._app.repaste_last.assert_not_called()

    def test_repaste_callback_fires_when_not_shutting_down(self, dispatcher: HotkeyDispatcher):
        callback = dispatcher._make_repaste_callback()
        dispatcher._app._shutting_down = False
        keyboard_ownership().set_owner("normal", reason="test")

        callback()

        dispatcher._app.repaste_last.assert_called_once()

    def test_esc_callback_noop_during_shutdown(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """The ESC key-down callback (built in ``register_esc``) must
        also short-circuit during shutdown — even when ownership is
        NOT hotkey_capture (the path that would otherwise call
        ``app._cancel_dictation``)."""
        # Mock the backend factory so register_esc doesn't touch the
        # real pynput / native hotkey machinery.
        mock_backend = MagicMock()
        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            lambda spec: mock_backend,
        )

        dispatcher._app._shutting_down = True
        dispatcher.register_esc()

        # The callback registered with backend.start() is the inner
        # _esc_callback closure. Extract it.
        assert mock_backend.start.called
        esc_callback = mock_backend.start.call_args.args[0]
        assert callable(esc_callback)

        # Ownership is normal (not hotkey_capture), so without the
        # shutdown guard this would call _cancel_dictation. With the
        # guard it must be a no-op.
        keyboard_ownership().set_owner("normal", reason="test")
        esc_callback()

        dispatcher._app._cancel_dictation.assert_not_called()


# ─── stop_all ───────────────────────────────────────────────────────────


class TestStopAll:
    """``stop_all`` is the shutdown entry point — must clear all three
    backends and swallow ``stop()`` failures so a poisoned backend
    doesn't abort the rest of shutdown."""

    def test_stop_all_clears_all_backends(self, dispatcher: HotkeyDispatcher):
        main = MagicMock()
        esc = MagicMock()
        repaste = MagicMock()
        dispatcher._hotkey_backend = main
        dispatcher._esc_backend = esc
        dispatcher._repaste_backend = repaste

        dispatcher.stop_all()

        main.stop.assert_called_once()
        esc.stop.assert_called_once()
        repaste.stop.assert_called_once()
        assert dispatcher._hotkey_backend is None
        assert dispatcher._esc_backend is None
        assert dispatcher._repaste_backend is None

    def test_stop_all_swallows_stop_failures(self, dispatcher: HotkeyDispatcher):
        """If one backend's ``stop()`` raises, ``stop_all`` must still
        stop the other two and clear all three references."""
        main = MagicMock()
        main.stop.side_effect = RuntimeError("join timed out")
        esc = MagicMock()
        repaste = MagicMock()
        dispatcher._hotkey_backend = main
        dispatcher._esc_backend = esc
        dispatcher._repaste_backend = repaste

        dispatcher.stop_all()  # must not raise

        main.stop.assert_called_once()
        esc.stop.assert_called_once()
        repaste.stop.assert_called_once()
        assert dispatcher._hotkey_backend is None
        assert dispatcher._esc_backend is None
        assert dispatcher._repaste_backend is None

    def test_stop_all_noop_when_no_backends(self, dispatcher: HotkeyDispatcher):
        """If nothing was ever registered, ``stop_all`` is a no-op."""
        dispatcher._hotkey_backend = None
        dispatcher._esc_backend = None
        dispatcher._repaste_backend = None

        dispatcher.stop_all()  # must not raise

        assert dispatcher._hotkey_backend is None
        assert dispatcher._esc_backend is None
        assert dispatcher._repaste_backend is None


# registration-failure surfaces to tray ───────────────────────


class TestRegistrationFailureSurfacesToTray:
    """FR-20: when ``register_esc`` / ``register_repaste`` fail (e.g.
    the OS already claimed the key via Win32 ``RegisterHotKey`` or an X11
    grab), the failure must be surfaced to the user via the tray's
    safety channel (``tray.notify_safety``) — not just silently
    ``log.warning``'d.

    Previously the except blocks in ``register_esc`` (line ~351) and
    ``register_repaste`` (line ~471) only emitted a ``log.warning`` and
    nulled the backend reference. The user had no signal that ESC cancel
    or repaste was unavailable until they pressed the key and nothing
    happened. ``register()`` (the main dictation hotkey) already called
    ``app.tray.notify`` on failure — this contract is now extended to
    ESC and repaste via the stronger ``notify_safety`` channel (which
    bypasses the user's notification-toggle preference, since these are
    safety-critical: a missing ESC cancel means the user cannot abort a
    misfired recording).
    """

    def test_register_esc_calls_notify_safety_on_factory_failure(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """ESC: ``create_hotkey_backend("<esc>")`` raises →
        ``tray.notify_safety`` must be called once with ``APP_NAME`` as
        the title and a message that mentions ESC."""

        def _raise(spec):
            raise RuntimeError("RegisterHotKey failed: atom already claimed")

        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            _raise,
        )

        dispatcher.register_esc()  # must not raise

        dispatcher._app.tray.notify_safety.assert_called_once()
        args = dispatcher._app.tray.notify_safety.call_args.args
        assert args[0] == APP_NAME
        assert "ESC" in args[1]
        # contract preserved: failed backend is nulled.
        assert dispatcher._esc_backend is None
        assert dispatcher._esc_spec is None
        # The non-safety ``notify`` channel must NOT be used (
        # mandates the safety channel so the message bypasses the
        # notification toggle).
        dispatcher._app.tray.notify.assert_not_called()

    def test_register_esc_calls_notify_safety_on_start_failure(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """ESC: ``create_hotkey_backend`` succeeds but ``backend.start()``
        raises → same ``notify_safety`` contract."""
        mock_backend = MagicMock()
        mock_backend.start.side_effect = RuntimeError("listener thread died")

        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            lambda spec: mock_backend,
        )

        dispatcher.register_esc()  # must not raise

        dispatcher._app.tray.notify_safety.assert_called_once()
        args = dispatcher._app.tray.notify_safety.call_args.args
        assert args[0] == APP_NAME
        assert "ESC" in args[1]
        assert dispatcher._esc_backend is None
        assert dispatcher._esc_spec is None

    def test_register_esc_does_not_notify_on_success(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """Sanity: when ESC registration succeeds, ``notify_safety`` is
        NOT called (the notification is reserved for failures only)."""
        mock_backend = MagicMock()
        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            lambda spec: mock_backend,
        )

        dispatcher.register_esc()

        dispatcher._app.tray.notify_safety.assert_not_called()
        assert dispatcher._esc_backend is mock_backend
        assert dispatcher._esc_spec == "<esc>"

    def test_register_repaste_calls_notify_safety_on_factory_failure(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """Repaste: ``create_hotkey_backend`` raises →
        ``tray.notify_safety`` must be called once with a message that
        mentions repaste."""
        # ``<f8>`` is a single non-alphanumeric function key — passes
        # all 8 validation stages in ``_validate_hotkey``.
        dispatcher._app.config.repaste_hotkey = "<f8>"

        def _raise(spec):
            raise RuntimeError("RegisterHotKey failed: F8 already claimed")

        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            _raise,
        )

        dispatcher.register_repaste()  # must not raise

        dispatcher._app.tray.notify_safety.assert_called_once()
        args = dispatcher._app.tray.notify_safety.call_args.args
        assert args[0] == APP_NAME
        assert "Repaste" in args[1] or "repaste" in args[1].lower()
        assert dispatcher._repaste_backend is None
        assert dispatcher._repaste_spec is None
        dispatcher._app.tray.notify.assert_not_called()

    def test_register_repaste_calls_notify_safety_on_start_failure(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """Repaste: ``backend.start()`` raises → same ``notify_safety``
        contract."""
        dispatcher._app.config.repaste_hotkey = "<f8>"

        mock_backend = MagicMock()
        mock_backend.start.side_effect = RuntimeError("start failed")

        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            lambda spec: mock_backend,
        )

        dispatcher.register_repaste()  # must not raise

        dispatcher._app.tray.notify_safety.assert_called_once()
        args = dispatcher._app.tray.notify_safety.call_args.args
        assert args[0] == APP_NAME
        assert "Repaste" in args[1] or "repaste" in args[1].lower()
        assert dispatcher._repaste_backend is None
        assert dispatcher._repaste_spec is None

    def test_register_repaste_does_not_notify_on_success(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """Sanity: when repaste registration succeeds, ``notify_safety``
        is NOT called."""
        dispatcher._app.config.repaste_hotkey = "<f8>"

        mock_backend = MagicMock()
        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            lambda spec: mock_backend,
        )

        dispatcher.register_repaste()

        dispatcher._app.tray.notify_safety.assert_not_called()
        assert dispatcher._repaste_backend is mock_backend
        assert dispatcher._repaste_spec == "<f8>"


# stop_all 3s timeout budget ──────────────────────────────────


class TestStopAllTimeoutBudget:
    """FR-25: ``stop_all`` wraps each ``backend.stop()`` in a
    ``concurrent.futures.ThreadPoolExecutor`` worker with a hard 3s
    budget shared across all three backends. A hung backend (e.g. a
    Win32 ``UnregisterHotKey`` that never returns, or a pynput listener
    thread join that blocks forever) cannot block the shutdown sequence
    for more than 3s — previously the worst case was ~15s (3 backends ×
    5s sequential join each).

    These tests verify the budget ENFORCES the timeout (a slow backend
    is leaked, not joined) and that the existing contracts (clears all
    three backend references, swallows ``stop()`` exceptions) still
    hold under the new concurrent implementation.
    """

    def test_stop_all_3s_budget_leaks_hung_backend(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """A ``backend.stop()`` that blocks for longer than the 3s budget
        must NOT block ``stop_all`` for that long. The future is
        cancelled (logged as "did not stop within 3s budget") and the
        method returns promptly.

        Note: the test patches the 3.0 budget down to 0.2s and sleeps 2s
        on the hung backend — the relative ordering is what we're
        verifying, not the absolute 3s. If the budget were NOT enforced,
        elapsed would be ~2s.
        """
        import time

        import voice_typer.server.hotkey_dispatcher as hd_mod

        # Patch the 3.0 budget down to 0.2s so the test is fast.
        original_wait = hd_mod.concurrent.futures.wait

        def _fast_wait(futures, timeout=None):
            return original_wait(futures, timeout=0.2)

        monkeypatch.setattr(hd_mod.concurrent.futures, "wait", _fast_wait)

        main = MagicMock()
        # Sleep 2s — far longer than the patched 0.2s budget.
        main.stop.side_effect = lambda: time.sleep(2.0)
        esc = MagicMock()
        repaste = MagicMock()
        dispatcher._hotkey_backend = main
        dispatcher._esc_backend = esc
        dispatcher._repaste_backend = repaste

        start = time.monotonic()
        dispatcher.stop_all()  # must return in ~0.2s, NOT ~2s
        elapsed = time.monotonic() - start

        # Budget is 0.2s; allow generous slack for CI scheduling jitter.
        # If the budget were NOT enforced, elapsed would be ~2s.
        assert elapsed < 1.0, f"stop_all took {elapsed:.2f}s — 3s budget not enforced"
        # The two fast backends stopped normally.
        esc.stop.assert_called_once()
        repaste.stop.assert_called_once()

    def test_stop_all_swallows_stop_failures_under_pool(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """FR-25 preserves the prior contract: a ``stop()`` that raises
        does NOT propagate out of ``stop_all`` (the future's exception
        is logged at debug level, not re-raised). All three backend
        references are still cleared."""
        main = MagicMock()
        main.stop.side_effect = RuntimeError("join timed out")
        esc = MagicMock()
        esc.stop.side_effect = OSError("EBADF")
        repaste = MagicMock()
        dispatcher._hotkey_backend = main
        dispatcher._esc_backend = esc
        dispatcher._repaste_backend = repaste

        dispatcher.stop_all()  # must not raise

        main.stop.assert_called_once()
        esc.stop.assert_called_once()
        repaste.stop.assert_called_once()
        assert dispatcher._hotkey_backend is None
        assert dispatcher._esc_backend is None
        assert dispatcher._repaste_backend is None
