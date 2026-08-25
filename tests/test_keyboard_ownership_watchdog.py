"""TASK-0010: regression tests for the backend ownership watchdog.

When the frontend crashes mid-capture (before sending
``set_esc_cancel_paused: false``), the backend would otherwise remain
stuck in ``"hotkey_capture"`` state permanently, suppressing all
hotkey interactions until restart. The IPC server's disconnect
handler now calls ``keyboard_ownership().reset()`` so a crashed
client doesn't strand the backend in a stuck-capture state.

These tests exercise:

  1. The direct ``_on_ipc_client_disconnect`` helper — the unit path.
  2. The full ``_handle_tcp_connection`` finally block via a real
     ``socket.socketpair`` — the integration path that proves the
     wiring actually fires on a TCP disconnect.
  3. The shutdown-skip behavior — the reset must NOT fire when the
     server is shutting down (``self._running == False``), so an
     active recording isn't interrupted by the teardown sequence.
  4. Idempotency — calling the helper multiple times is safe.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest
from voice_typer.server.ipc_server import IPCServer
from voice_typer.server.keyboard_ownership import keyboard_ownership

from tests.fixtures.ipc_test_helpers import make_fake_app, make_fake_service

# Hint for xdist schedulers that respect ``xdist_group`` (loadgroup /
# loadscope): pin every test in this module — and its sibling
# ``test_keyboard_ownership.py`` — onto a single worker. Both modules
# reset the ``KeyboardOwnership`` class-attribute singleton via autouse
# fixtures, and the singleton is process-wide state; the marker is
# defense-in-depth so the two modules that mutate it group onto one
# worker. xdist's default ``load`` scheduler does NOT strictly honor
# this marker — it is a hint, not a correctness guarantee. No-op when
# xdist isn't active. (C-TEST-5.)
pytestmark = pytest.mark.xdist_group("keyboard_ownership")


@pytest.fixture(autouse=True)
def _reset_ownership():
    """Reset the singleton to "normal" between tests."""
    keyboard_ownership().reset()
    yield
    keyboard_ownership().reset()


def _make_server() -> IPCServer:
    """Construct a real IPCServer with fake app/service for unit tests."""
    app = make_fake_app()
    service = make_fake_service()
    server = IPCServer(app, service=service)
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

    The frontend crashed mid-recording — the recording subsystem
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
    be premature — we only want to fire on an *unexpected* client
    disconnect.
    """
    kb = keyboard_ownership()
    kb.set_owner("recording", reason="active recording during shutdown")
    assert kb.current_owner() == "recording"

    server = _make_server()
    server._running = False  # simulate stop() having been called
    server._on_ipc_client_disconnect("IPC client disconnected")

    # Ownership must NOT have been reset — recording state preserved.
    assert kb.current_owner() == "recording"
    assert kb.is_recording_active() is True


def test_disconnect_handler_is_idempotent() -> None:
    """Calling the handler multiple times is safe.

    Both the TCP finally block and the stdin EOF path call the
    helper. The second call must be a no-op (not raise, not
    corrupt state).
    """
    kb = keyboard_ownership()
    kb.set_owner("hotkey_capture", reason="capture in progress")

    server = _make_server()
    server._on_ipc_client_disconnect("first disconnect")
    assert kb.current_owner() == "normal"

    # Second call — must not raise, must keep owner at "normal".
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


# ── Integration test: real TCP socketpair through _handle_tcp_connection ──


def test_tcp_disconnect_finally_block_resets_ownership(monkeypatch) -> None:
    """End-to-end: closing the TCP client triggers the reset.

    This exercises the actual ``_handle_tcp_connection`` finally
    block — the wiring that production relies on. We use a real
    ``socket.socketpair`` so the server's read loop sees a genuine
    EOF when the client side is closed.

    SEC-2 / IPC-10 (2026-07-18): the handler now refuses connections
    when ``expected_token`` is empty (SEC-2 hardening) and reads the
    token from the ``VOICE_TYPER_IPC_TOKEN`` env var when the
    ``expected_token`` parameter is ``None`` (IPC-10 fix).  The test
    sets the env var and sends a valid auth line before closing so
    the handler enters the dispatch loop and reaches the finally
    block that calls ``_on_ipc_client_disconnect``.
    """
    _test_token = "watchdog-test-token-ipc10"
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _test_token)

    kb = keyboard_ownership()
    kb.set_owner("hotkey_capture", reason="frontend capture before crash")
    assert kb.current_owner() == "hotkey_capture"

    server = _make_server()
    client_sock, server_sock = socket.socketpair()

    # Pass the expected token directly (the handler requires a non-empty
    # expected_token; it does not fall back to the env var when None, so
    # we hand it the same value we set on VOICE_TYPER_IPC_TOKEN).
    handler_thread = threading.Thread(
        target=server._handle_tcp_connection,
        args=(server_sock, ("127.0.0.1", 0), _test_token),
        daemon=True,
    )
    handler_thread.start()

    # Send valid auth so the handler enters the dispatch loop (reaches
    # the try/finally that calls _on_ipc_client_disconnect on EOF).
    client_sock.sendall((__import__("json").dumps({"type": "auth", "token": _test_token}) + "\n").encode("utf-8"))

    # Poll for the server-side auth completion signal
    # (``server._tcp_client`` is assigned to ``auth_client`` inside
    # ``self._lock`` AFTER the auth token check succeeds and BEFORE
    # the dispatch loop starts — see ``_handle_tcp_connection`` in
    # ``voice_typer/server/ipc/transport_tcp.py``). When this attribute
    # is non-None, the handler has finished the auth handshake and is
    # about to enter (or has just entered) the dispatch ``for line in
    # client`` loop — exactly the precondition the original
    # ``time.sleep(0.15)`` was trying to wait for. Replacing the fixed
    # sleep with a bounded poll (1.5s deadline, 5ms granularity) makes
    # the test exit early on fast machines and tolerant of slow CI.
    _auth_deadline = time.monotonic() + 1.5
    while time.monotonic() < _auth_deadline:
        if getattr(server, "_tcp_client", None) is not None:
            break
        time.sleep(0.005)

    # Close the client side — server's readline() returns "" (EOF),
    # the for-loop exits, the finally block fires _on_ipc_client_disconnect.
    client_sock.close()

    # Wait for the handler to finish (it should exit promptly on EOF).
    handler_thread.join(timeout=5.0)
    assert not handler_thread.is_alive(), "TCP handler thread did not exit after client disconnect"

    # The watchdog must have reset ownership to "normal".
    assert kb.current_owner() == "normal", (
        f"Expected ownership to be reset to 'normal' after TCP client disconnect, got {kb.current_owner()!r}"
    )


def test_tcp_disconnect_during_shutdown_preserves_recording() -> None:
    """The watchdog must skip the reset when the server is shutting down.

    Mirrors ``test_disconnect_does_not_reset_during_shutdown`` but
    through the real ``_handle_tcp_connection`` path — proving the
    ``self._running`` guard fires in the finally block, not just in
    the helper.
    """
    kb = keyboard_ownership()
    kb.set_owner("recording", reason="active recording during shutdown")
    assert kb.current_owner() == "recording"

    server = _make_server()
    # Simulate stop() having been called BEFORE the client disconnect.
    server._running = False

    client_sock, server_sock = socket.socketpair()
    handler_thread = threading.Thread(
        target=server._handle_tcp_connection,
        args=(server_sock, ("127.0.0.1", 0), ""),
        daemon=True,
    )
    handler_thread.start()

    client_sock.close()
    handler_thread.join(timeout=5.0)
    assert not handler_thread.is_alive()

    # Ownership must be preserved — the watchdog correctly skipped.
    assert kb.current_owner() == "recording", "Watchdog must not reset ownership during server shutdown"


# ── stdin EOF path ──────────────────────────────────────────────────────


def test_stdin_eof_resets_ownership() -> None:
    """The stdin (legacy) IPC path also triggers the watchdog on EOF.

    Uses an empty ``io.StringIO`` so ``iter()`` returns immediately
    with EOF — exercising the post-loop disconnect call without
    spinning up real stdin or a socket pair.
    """
    import io

    kb = keyboard_ownership()
    kb.set_owner("hotkey_capture", reason="CLI client capture")
    assert kb.current_owner() == "hotkey_capture"

    server = _make_server()

    # Empty stdin — iter() returns immediately, the for-loop body
    # never runs, and we fall through to the disconnect call.
    stdin_fake = io.StringIO("")
    stdout_fake = io.StringIO()

    server._run(_stdin=stdin_fake, _stdout=stdout_fake)

    assert kb.current_owner() == "normal", "Expected ownership to be reset to 'normal' after stdin EOF"


def test_stdin_eof_does_not_reset_during_shutdown() -> None:
    """The stdin watchdog path also respects the shutdown guard.

    If ``self._running == False`` when stdin hits EOF, we must not
    reset ownership — same constraint as the TCP path.
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
