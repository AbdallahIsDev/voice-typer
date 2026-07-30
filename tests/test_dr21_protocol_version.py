"""DR-21 (S1-CR-78): IPC wire protocol versioning tests.

Validates that the TCP auth handshake in
``voice_typer.server.ipc.transport_tcp._handle_tcp_connection``:

1. Accepts auth frames that OMIT ``protocol_version`` (backward compat
   with legacy senders).
2. Accepts auth frames that send ``protocol_version: 1`` (the current
   :data:`IPC_PROTOCOL_VERSION`).
3. Rejects auth frames that send an explicit non-matching
   ``protocol_version`` (e.g. ``2``) BEFORE the token check, emitting a
   structured ``server.protocol_version_mismatch`` error envelope.

These tests use mock socket/line-IO objects (the Linux sandbox doesn't
support ``socket.socketpair(AF_INET, SOCK_STREAM)`` — see the
``socket.timeout`` / ``OSError`` handling notes inline).
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc.transport_tcp import (
    IPC_PROTOCOL_VERSION,
    PROTOCOL_VERSION_MISMATCH_CODE,
    TCPTransportMixin,
)


class _FakeLineIO:
    """Minimal stand-in for ``ipc.transport._TCPLineIO``.

    Returns a canned auth line on ``readline()`` and records any error
    envelope written via ``write()`` / ``flush()`` / ``close()``. Lets
    us exercise the protocol-version branch of ``_handle_tcp_connection``
    without a real socket (which is unavailable in this sandbox —
    ``socket.socketpair(AF_INET)`` returns ``EOPNOTSUPP``).
    """

    def __init__(self, auth_line: str) -> None:
        self._auth_line = auth_line
        self.written: list[str] = []
        self.closed = False

    def readline(self) -> str:
        return self._auth_line

    def write(self, data: str) -> None:
        self.written.append(data)

    def flush(self) -> None:  # noqa: D401 - simple no-op for tests
        pass

    def close(self) -> None:
        self.closed = True


class _MinimalTcpHandler(TCPTransportMixin):
    """Minimal host class for ``TCPTransportMixin`` testing.

    Mirrors ``tests/test_tcp_nodelay.py::_MinimalTcpHandler`` — bare
    subclass with no instance state, sufficient for the early-auth code
    paths (the protocol-version mismatch and empty-token refuse paths
    return BEFORE any instance attribute is accessed).
    """

    def __init__(self) -> None:
        pass


def _run_auth(auth_frame: dict) -> tuple[_FakeLineIO, object]:
    """Run ``_handle_tcp_connection`` with a fake socket + line-IO.

    Returns the (fake_lineio, conn) pair so callers can inspect what
    the handler wrote (if anything) before closing.
    """
    server = _MinimalTcpHandler()
    fake_conn = MagicMock()
    fake_line_io = _FakeLineIO(json.dumps(auth_frame) + "\n")
    # Patch _TCPLineIO inside the transport_tcp module so the handler
    # picks up our fake. The handler does `auth_client = _TCPLineIO(conn)`.
    import voice_typer.server.ipc.transport_tcp as mod

    original = mod._TCPLineIO
    mod._TCPLineIO = lambda conn: fake_line_io
    try:
        server._handle_tcp_connection(fake_conn, ("127.0.0.1", 0), "expected-token")
    finally:
        mod._TCPLineIO = original
    return fake_line_io, fake_conn


def test_ipc_protocol_version_constant_is_int_and_positive() -> None:
    """Sanity: :data:`IPC_PROTOCOL_VERSION` is a positive int.

    A future bump that accidentally sets it to ``0`` or a non-int would
    silently accept all auth frames (``0 == 0``) or raise a TypeError
    during the comparison — this test pins the contract.
    """
    assert isinstance(IPC_PROTOCOL_VERSION, int)
    assert IPC_PROTOCOL_VERSION > 0


def test_protocol_version_mismatch_code_is_namespaced() -> None:
    """The mismatch error code uses the ``server.`` namespace prefix.

    Consistent with the rest of the namespaced error registry in
    ``voice_typer.server.ipc.validation.ErrorCodes``.
    """
    assert PROTOCOL_VERSION_MISMATCH_CODE.startswith("server.")
    assert "protocol_version_mismatch" in PROTOCOL_VERSION_MISMATCH_CODE


def test_source_contains_protocol_version_check_before_token_check() -> None:
    """DR-21: the source of ``_handle_tcp_connection`` contains a
    ``protocol_version`` check that appears BEFORE the
    ``hmac.compare_digest`` token check. This is a structural test
    that doesn't require a real socket.
    """
    src = inspect.getsource(TCPTransportMixin._handle_tcp_connection)
    assert "protocol_version" in src, (
        "DR-21: _handle_tcp_connection must reference 'protocol_version' "
        "in its auth-handshake block."
    )
    assert "IPC_PROTOCOL_VERSION" in src, (
        "DR-21: _handle_tcp_connection must reference the "
        "IPC_PROTOCOL_VERSION constant."
    )
    assert PROTOCOL_VERSION_MISMATCH_CODE in src, (
        "DR-21: _handle_tcp_connection must emit the "
        "PROTOCOL_VERSION_MISMATCH_CODE on mismatch."
    )
    # The version check must come BEFORE the token check. Use the actual
    # call site ``hmac.compare_digest(auth_msg`` (not the bare mention,
    # which also appears in the docstring) as the anchor.
    version_idx = src.index("protocol_version")
    token_idx = src.index("hmac.compare_digest(auth_msg")
    assert version_idx < token_idx, (
        "DR-21: the protocol_version check must run BEFORE the "
        "hmac.compare_digest token check so a stale client gets a "
        "structured rejection instead of an opaque auth_failed."
    )


def test_auth_accepts_frame_without_protocol_version() -> None:
    """DR-21: a legacy auth frame without ``protocol_version`` is
    accepted on the version-check path and proceeds to the token check.
    With a wrong token, the token check rejects — but NOT with the
    version-mismatch code.
    """
    fake_io, _ = _run_auth({"type": "auth", "token": "wrong-token"})
    # The handler closed the connection (token-mismatch path).
    assert fake_io.closed, (
        "DR-21: legacy auth frame (no protocol_version) should reach the "
        "token check and be rejected with auth_failed, then closed."
    )
    # If anything was written, it should NOT be a protocol_version_mismatch.
    written = "".join(fake_io.written)
    assert "protocol_version_mismatch" not in written, (
        "DR-21 regression: a legacy auth frame WITHOUT protocol_version "
        "was rejected on the version-mismatch path. Server wrote: "
        f"{written!r}"
    )


def test_auth_accepts_frame_with_matching_protocol_version() -> None:
    """DR-21: an auth frame with ``protocol_version: <current>`` is
    accepted on the version-check path and proceeds to the token check.
    With a wrong token, the token check rejects — but NOT with the
    version-mismatch code.
    """
    fake_io, _ = _run_auth(
        {
            "type": "auth",
            "token": "wrong-token",
            "protocol_version": IPC_PROTOCOL_VERSION,
        }
    )
    assert fake_io.closed, (
        "DR-21: auth frame with matching protocol_version should reach "
        "the token check and be rejected with auth_failed, then closed."
    )
    written = "".join(fake_io.written)
    assert "protocol_version_mismatch" not in written, (
        "DR-21 regression: an auth frame with the CORRECT "
        "protocol_version was rejected on the version-mismatch path. "
        f"Server wrote: {written!r}"
    )


def test_auth_rejects_frame_with_mismatched_protocol_version() -> None:
    """DR-21: an auth frame with a non-matching ``protocol_version``
    (e.g. ``<current>+1``) is rejected BEFORE the token check with a
    structured ``server.protocol_version_mismatch`` error envelope.
    The envelope carries the client and server version numbers for
    debugging.

    Note: the token in this frame is CORRECT — the test verifies the
    version check runs first and rejects before the token check would
    have accepted.
    """
    bogus_version = IPC_PROTOCOL_VERSION + 1
    fake_io, _ = _run_auth(
        {
            "type": "auth",
            "token": "expected-token",  # correct — version check runs FIRST
            "protocol_version": bogus_version,
        }
    )
    assert fake_io.closed, (
        "DR-21: mismatched-protocol_version auth frame should close the "
        "connection after emitting the mismatch error envelope."
    )
    written = "".join(fake_io.written).strip()
    assert written, (
        "DR-21 regression: a mismatched-protocol_version auth frame did "
        "NOT produce an error envelope — the server should have emitted "
        "a structured protocol_version_mismatch error before closing."
    )
    decoded = json.loads(written)
    assert decoded["type"] == "error", (
        f"Expected error envelope, got type={decoded.get('type')!r}"
    )
    data_obj = decoded["data"]
    assert data_obj["code"] == PROTOCOL_VERSION_MISMATCH_CODE, (
        f"Expected code={PROTOCOL_VERSION_MISMATCH_CODE!r}, got "
        f"{data_obj.get('code')!r}"
    )
    assert data_obj["client_protocol_version"] == bogus_version
    assert data_obj["server_protocol_version"] == IPC_PROTOCOL_VERSION


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
