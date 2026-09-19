"""Regression tests for the backend ownership watchdog."""

from __future__ import annotations

import pytest
from voice_typer.server.ipc_server import IPCServer
from voice_typer.server.keyboard_ownership import keyboard_ownership

from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

# xdist isn't active. (C-TEST-5.)
pytestmark = pytest.mark.xdist_group("keyboard_ownership")


@pytest.fixture(autouse=True)
def _reset_ownership():
    """Reset the singleton to \"normal\" between tests."""
    keyboard_ownership().reset()
    yield
    keyboard_ownership().reset()


def _make_server() -> IPCServer:
    """Construct a real IPCServer via the canonical fake factories."""
    server, _fake_app, _fake_service = make_ipc_server_with_fakes()
    # Match the post-start() state so _on_ipc_client_disconnect's
    server._running = True
    return server


def test_disconnect_resets_hotkey_capture_to_normal() -> None:
    """A client disconnect while in hotkey_capture resets to normal."""
    kb = keyboard_ownership()
    kb.set_owner("hotkey_capture", reason="frontend entered capture")
    assert kb.current_owner() == "hotkey_capture"

    server = _make_server()
    server._on_ipc_client_disconnect("IPC client disconnected")

    assert kb.current_owner() == "normal"
    assert kb.is_hotkey_capture_active() is False


def test_disconnect_resets_recording_to_normal() -> None:
    """A client disconnect during a recording also resets to normal."""
    kb = keyboard_ownership()
    kb.set_owner("recording", reason="active recording")
    assert kb.current_owner() == "recording"

    server = _make_server()
    server._on_ipc_client_disconnect("IPC client disconnected")

    assert kb.current_owner() == "normal"


def test_disconnect_does_not_reset_during_shutdown() -> None:
    """The watchdog must NOT fire during server shutdown."""
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
    """Calling the handler multiple times is safe."""
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


def test_stdin_eof_resets_ownership() -> None:
    """The stdin (legacy) IPC path also triggers the watchdog on EOF."""
    import io

    kb = keyboard_ownership()
    kb.set_owner("hotkey_capture", reason="CLI client capture")
    assert kb.current_owner() == "hotkey_capture"

    server = _make_server()

    # Empty stdin, iter() returns immediately, the for-loop body
    stdin_fake = io.StringIO("")
    stdout_fake = io.StringIO()

    server._run(_stdin=stdin_fake, _stdout=stdout_fake)

    assert kb.current_owner() == "normal", "Expected ownership to be reset to 'normal' after stdin EOF"


def test_stdin_eof_does_not_reset_during_shutdown() -> None:
    """
    The stdin watchdog path also respects the shutdown guard.
    If ``self._running == False`` when stdin hits EOF, we must not
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
