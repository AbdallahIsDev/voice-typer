"""Regression tests for the backend ownership watchdog.

When the frontend crashes mid-capture (before sending
``set_esc_cancel_paused: false``), the backend would otherwise remain
stuck in ``"hotkey_capture"`` state permanently, suppressing all
hotkey interactions until restart. The IPC server's disconnect
handler now calls ``keyboard_ownership().reset()`` so a crashed
client doesn't strand the backend in a stuck-capture state.

These tests exercise:

  1. The direct ``_on_ipc_client_disconnect`` helper, the unit path.
  2. The shutdown-skip behavior, the reset must NOT fire when the
     server is shutting down (``self._running == False``), so an
     active recording isn't interrupted by the teardown sequence.
  3. Idempotency, calling the helper multiple times is safe.
  4. The stdin-EOF path, the legacy stdin IPC transport also
     triggers the watchdog on EOF.
"""

from __future__ import annotations

import pytest
from voice_typer.server.ipc_server import IPCServer
from voice_typer.server.keyboard_ownership import keyboard_ownership

from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

# Hint for xdist schedulers that respect ``xdist_group`` (loadgroup /
# loadscope): pin every test in this module, and its sibling
# ``test_keyboard_ownership.py``, onto a single worker. Both modules
# reset the ``KeyboardOwnership`` class-attribute singleton via autouse
# fixtures, and the singleton is process-wide state; the marker is
# defense-in-depth so the two modules that mutate it group onto one
# worker. xdist's default ``load`` scheduler does NOT strictly honor
# this marker, it is a hint, not a correctness guarantee. No-op when
# xdist isn't active. (C-TEST-5.)
pytestmark = pytest.mark.xdist_group("keyboard_ownership")


@pytest.fixture(autouse=True)
def _reset_ownership():
    """Reset the singleton to "normal" between tests."""
    keyboard_ownership().reset()
    yield
    keyboard_ownership().reset()


def _make_server() -> IPCServer:
    """Construct a real IPCServer via the canonical fake factories."""
    server, _fake_app, _fake_service = make_ipc_server_with_fakes()
    # Match the post-start() state so _on_ipc_client_disconnect's
    # ``self._running`` guard treats us as a live server.
    server._running = True
    return server


# ── Unit tests for the _on_ipc_client_disconnect helper ────────────────


def test_disconnect_resets_hotkey_capture_to_normal() -> None:
    """A client disconnect while in hotkey_capture resets to normal.

    This is the core regression: a crashed frontend leaves the
    backend in ``"hotkey_capture"`` state. The watchdog must
    restore ``"normal"`` on disconnect.
    """
    kb = keyboard_ownership()
    kb.set_owner("hotkey_capture", reason="frontend entered capture")
    assert kb.current_owner() == "hotkey_capture"

    server = _make_server()
    server._on_ipc_client_disconnect("IPC client disconnected")

    assert kb.current_owner() == "normal"
    assert kb.is_hotkey_capture_active() is False


def test_disconnect_resets_recording_to_normal() -> None:
    """A client disconnect during a recording also resets to normal.

    The frontend crashed mid-recording, the recording subsystem
    will be torn down by other cleanup paths, but keyboard
    ownership must not stay in ``"recording"`` state.
    """
    kb = keyboard_ownership()
    kb.set_owner("recording", reason="active recording")
    assert kb.current_owner() == "recording"

    server = _make_server()
    server._on_ipc_client_disconnect("IPC client disconnected")

    assert kb.current_owner() == "normal"


def test_disconnect_does_not_reset_during_shutdown() -> None:
    """The watchdog must NOT fire during server shutdown.

    If the backend is shutting down (``self._running == False``),
    a recording might be in progress and the teardown sequence
    will handle its own cleanup. Resetting ownership here would
    be premature, we only want to fire on an *unexpected* client
    disconnect.
    """
    kb = keyboard_ownership()
    kb.set_owner("recording", reason="active recording during shutdown")
    assert kb.current_owner() == "recording"

    server = _make_server()
    server._running = False  # simulate stop() having been called
    server._on_ipc_client_disconnect("IPC client disconnected")

    # Ownership must NOT have been reset, recording state preserved.
    assert kb.current_owner() == "recording"
    assert kb.is_recording_active() is True


def test_disconnect_handler_is_idempotent() -> None:
    """Calling the handler multiple times is safe.

    Both the WS drop path and the stdin EOF path call the
    helper. The second call must be a no-op (not raise, not
    corrupt state).
    """
    kb = keyboard_ownership()
    kb.set_owner("hotkey_capture", reason="capture in progress")

    server = _make_server()
    server._on_ipc_client_disconnect("first disconnect")
    assert kb.current_owner() == "normal"

    # Second call, must not raise, must keep owner at "normal".
    server._on_ipc_client_disconnect("second disconnect (spurious)")
    assert kb.current_owner() == "normal"


def test_disconnect_handler_safe_when_already_normal() -> None:
    """Calling the handler when ownership is already normal is safe."""
    kb = keyboard_ownership()
    assert kb.current_owner() == "normal"

    server = _make_server()
    server._on_ipc_client_disconnect("spurious disconnect")
    # No exception, no state change.
    assert kb.current_owner() == "normal"


# ── stdin EOF path ──────────────────────────────────────────────────────


def test_stdin_eof_resets_ownership() -> None:
    """The stdin (legacy) IPC path also triggers the watchdog on EOF.

    Uses an empty ``io.StringIO`` so ``iter()`` returns immediately
    with EOF, exercising the post-loop disconnect call without
    spinning up real stdin or a socket pair.
    """
    import io

    kb = keyboard_ownership()
    kb.set_owner("hotkey_capture", reason="CLI client capture")
    assert kb.current_owner() == "hotkey_capture"

    server = _make_server()

    # Empty stdin, iter() returns immediately, the for-loop body
    # never runs, and we fall through to the disconnect call.
    stdin_fake = io.StringIO("")
    stdout_fake = io.StringIO()

    server._run(_stdin=stdin_fake, _stdout=stdout_fake)

    assert kb.current_owner() == "normal", "Expected ownership to be reset to 'normal' after stdin EOF"


def test_stdin_eof_does_not_reset_during_shutdown() -> None:
    """The stdin watchdog path also respects the shutdown guard.

    If ``self._running == False`` when stdin hits EOF, we must not
    reset ownership, same constraint as the WS-drop path.
    """
    import io

    kb = keyboard_ownership()
    kb.set_owner("recording", reason="active recording during shutdown")
    assert kb.current_owner() == "recording"

    server = _make_server()
    server._running = False  # simulate stop()

    stdin_fake = io.StringIO("")
    stdout_fake = io.StringIO()
    server._run(_stdin=stdin_fake, _stdout=stdout_fake)

    assert kb.current_owner() == "recording", "Watchdog must not reset ownership during shutdown on stdin EOF"
