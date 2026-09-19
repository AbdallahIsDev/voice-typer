"""(2026-07-25): regression tests for ``IPCServer._send``"""

from __future__ import annotations

import inspect
import socket
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc_server import _SHUTDOWN_ALLOWLIST, IPCServer


def test_shutdown_allowlist_is_module_level_frozenset() -> None:
    """``_SHUTDOWN_ALLOWLIST`` must be a module-level frozenset."""
    assert isinstance(_SHUTDOWN_ALLOWLIST, frozenset), (
        f"_SHUTDOWN_ALLOWLIST must be a frozenset, got {type(_SHUTDOWN_ALLOWLIST).__name__}"
    )


def test_shutdown_allowlist_has_correct_membership() -> None:
    """The allowlist must contain exactly the 5 critical event types."""
    expected = frozenset(
        {
            "relaunch_app",
            "quit_app",
            "transcription_final",
            "transcription_partial",
            "vocabulary_suggestion",
        }
    )
    assert expected == _SHUTDOWN_ALLOWLIST, (
        f"_SHUTDOWN_ALLOWLIST drift. Expected {sorted(expected)}, got {sorted(_SHUTDOWN_ALLOWLIST)}."
    )


def test_shutdown_allowlist_is_immutable() -> None:
    """``_SHUTDOWN_ALLOWLIST`` must be a frozenset (not a regular set)"""
    with pytest.raises(AttributeError):
        _SHUTDOWN_ALLOWLIST.add("evil_event")  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        _SHUTDOWN_ALLOWLIST.discard("relaunch_app")  # type: ignore[attr-defined]


def test_cached_shutting_down_initialized_in_init() -> None:
    """``IPCServer.__init__`` must initialize ``_cached_shutting_down``"""

    # Minimal stand-in for the app attribute the constructor needs.
    class _FakeApp:
        pass

    server = IPCServer(_FakeApp())
    assert server._cached_shutting_down is False, (
        "_cached_shutting_down must be initialized to False in "
        "__init__ (a fresh server has never been told to shut down)."
    )


def test_cached_shutting_down_refreshed_in_start() -> None:
    """``IPCServer.start()`` must set ``_cached_shutting_down = False``."""
    src = inspect.getsource(IPCServer.start)
    assert "_cached_shutting_down = False" in src, (
        "start() must set _cached_shutting_down = False (the canonical 'we're not shutting down' transition)."
    )


def test_cached_shutting_down_refreshed_in_stop() -> None:
    """``IPCServer.stop()`` must set ``_cached_shutting_down = True``."""
    src = inspect.getsource(IPCServer.stop)
    assert "_cached_shutting_down = True" in src, (
        "stop() must set _cached_shutting_down = True (the canonical 'we're shutting down' transition)."
    )


def test_cached_shutting_down_actually_changes_on_lifecycle() -> None:
    """
    End-to-end: construct → start → stop should flip the cache.
    The source-level tests above pin that the assignment statements
    """

    # Construct a minimal app that the IPCServer can be attached to.
    class _FakeApp:
        _ipc_server = None

        def __init__(self) -> None:
            self._thread_registry = None

    server = IPCServer.__new__(IPCServer)
    server.app = _FakeApp()
    server._cached_shutting_down = False

    server._tcp_client = None
    server._tcp_server_socket = None
    server._tcp_worker_pool = None
    server._tcp_dispatch_pool = None
    server._heartbeat_stop_event = threading.Event()
    server._push_fn = None
    server._stdin_thread = None

    # Verify initial state.
    assert server._cached_shutting_down is False

    server.stop()
    assert server._cached_shutting_down is True, (
        f"stop() must set _cached_shutting_down = True (got {server._cached_shutting_down!r})."
    )


# ── 3. _send uses cached field + module-level allowlist (source-level) ─


def test_send_uses_cached_shutting_down_not_getattr_self_app() -> None:
    """doing the previous ``getattr(self.app, \"_shutting_down\", False)"""
    src = inspect.getsource(IPCServer._send)
    assert "_cached_shutting_down" in src, (
        "_send must reference _cached_shutting_down (the cached field) instead of doing getattr(self.app, ...)."
    )
    executable_lines = [line for line in src.splitlines() if line.strip() and not line.strip().startswith("#")]
    executable_src = "\n".join(executable_lines)
    assert 'getattr(self.app, "_shutting_down"' not in executable_src, (
        "_send still has the per-call "
        '``getattr(self.app, "_shutting_down", False) is True`` pattern '
        "in executable code. Replace with the cached "
        "``getattr(self, '_cached_shutting_down', False) is True``."
    )


def test_send_uses_module_level_allowlist_not_inline_tuple() -> None:
    """``_send`` must reference ``_SHUTDOWN_ALLOWLIST`` (the module-level"""
    src = inspect.getsource(IPCServer._send)
    assert "_SHUTDOWN_ALLOWLIST" in src, (
        "_send must reference the module-level _SHUTDOWN_ALLOWLIST constant (not re-allocate the tuple inline)."
    )
    # The inline tuple allocation pattern must NOT appear in executable
    executable_lines = [line for line in src.splitlines() if line.strip() and not line.strip().startswith("#")]
    executable_src = "\n".join(executable_lines)
    assert "_shutdown_allowlist = (" not in executable_src, (
        "_send still has the inline tuple allocation pattern "
        "``_shutdown_allowlist = (...)`` in executable code. Replace "
        "with ``_shutdown_allowlist = _SHUTDOWN_ALLOWLIST``."
    )


def test_send_uses_select_not_settimeout() -> None:
    """
    single ``select.select`` call via ``_await_socket_writable``. This
    ``_await_socket_writable`` and must NOT capture ``_prev_timeout``
    """
    src = inspect.getsource(IPCServer._send)
    assert "_await_socket_writable" in src, (
        "_send must call _await_socket_writable to gate writes on "
        "socket write-readiness (replaces the per-write settimeout dance)."
    )
    assert "_prev_timeout" not in src, (
        "_send must NOT capture _prev_timeout, the select-based approach "
        "doesn't mutate the socket timeout, so there's nothing to restore."
    )
    assert "finally:" not in src, (
        "_send must NOT have a finally block, without the settimeout dance there's no timeout state to restore."
    )


def _make_send_test_server():
    """Construct a minimal IPCServer for testing ``_send`` in isolation."""
    server = IPCServer.__new__(IPCServer)
    server.app = MagicMock()
    server.app._shutting_down = False
    server._lock = threading.RLock()
    server._tcp_write_lock = threading.RLock()
    server._pending_tcp = []
    server._tcp_mode = True
    server._tcp_client = None
    return server


def test_send_suppresses_non_allowlisted_push_when_shutting_down() -> None:
    """When ``_cached_shutting_down = True``, a push event (no ``id``)"""
    server = _make_send_test_server()
    # Set up a real socketpair so we can detect whether the write
    srv, cli = socket.socketpair()
    try:
        from voice_typer.server.ipc_server import _TCPLineIO

        tcp_client = _TCPLineIO(srv)
        server._tcp_client = tcp_client
        server._cached_shutting_down = True

        # Non-allowlisted push event (no id, type=bubble_level).
        server._send({"type": "bubble_level", "level": 0.5})

        # The suppression path closes the tcp_client and sets
        assert server._tcp_client is None, (
            "regression: when _cached_shutting_down=True and the "
            "event type is NOT in _SHUTDOWN_ALLOWLIST, _send must close "
            "the tcp_client and set _tcp_client=None (suppression path)."
        )
    finally:
        srv.close()
        cli.close()


def test_send_delivers_allowlisted_push_when_shutting_down() -> None:
    """When ``_cached_shutting_down = True``, a push event whose"""
    server = _make_send_test_server()
    srv, cli = socket.socketpair()
    try:
        from voice_typer.server.ipc_server import _TCPLineIO

        tcp_client = _TCPLineIO(srv)
        server._tcp_client = tcp_client
        server._cached_shutting_down = True

        # Reader thread to drain the socket so sendall doesn't block.
        received: list[bytes] = []
        reader = threading.Thread(
            target=lambda: received.append(cli.recv(65536)),
            daemon=True,
        )
        reader.start()

        # Allowlisted push event. MUST be delivered.
        server._send({"type": "relaunch_app"})

        reader.join(timeout=2.0)
        assert received, (
            "regression: when _cached_shutting_down=True but the "
            "event type IS in _SHUTDOWN_ALLOWLIST (relaunch_app), _send "
            "MUST still write the event to the TCP client. No bytes "
            "were received."
        )
        assert b"relaunch_app" in received[0], f"expected 'relaunch_app' in the written bytes; got {received[0]!r}."
    finally:
        srv.close()
        cli.close()


def test_send_delivers_non_allowlisted_push_when_not_shutting_down() -> None:
    """When ``_cached_shutting_down = False``, ALL push events must be"""
    server = _make_send_test_server()
    srv, cli = socket.socketpair()
    try:
        from voice_typer.server.ipc_server import _TCPLineIO

        tcp_client = _TCPLineIO(srv)
        server._tcp_client = tcp_client
        # Cache is False (default after __init__ or start()).
        server._cached_shutting_down = False

        received: list[bytes] = []
        reader = threading.Thread(
            target=lambda: received.append(cli.recv(65536)),
            daemon=True,
        )
        reader.start()

        # Non-allowlisted push event, MUST be delivered because we're
        server._send({"type": "bubble_level", "level": 0.5})

        reader.join(timeout=2.0)
        assert received, (
            "regression: when _cached_shutting_down=False, _send "
            "must deliver ALL push events (even non-allowlisted ones "
            "like bubble_level). No bytes were received."
        )
        assert b"bubble_level" in received[0]
    finally:
        srv.close()
        cli.close()


def test_send_works_when_cached_shutting_down_not_set() -> None:
    """Test fixtures that construct ``IPCServer.__new__(IPCServer)``"""
    server = _make_send_test_server()
    server._pending_tcp = ['{"old":1}']
    server._tcp_client = None  # so we don't actually write anywhere

    # Should not raise AttributeError.
    server._send({"type": "test", "id": 1})

    # The event should have been buffered in _pending_tcp (since
    assert len(server._pending_tcp) == 2, (
        f"_send should have appended the new event to "
        f"_pending_tcp when tcp_client is None. Got "
        f"{len(server._pending_tcp)} entries."
    )
