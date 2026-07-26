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
    # XZ-R17-02: shutdown flag the dictation/repaste/ESC callbacks
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


# ─── Shutdown guards (XZ-R17-02) ────────────────────────────────────────


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
