"""Tests for ``OutputMixin._send_error_envelope`` and ``_LazyInt``.

Covers two review.md entries:

- **ZR-76 (TCP-path extension):** the inline ``err = {"type": "error",
  "data": {"code": ..., "message": ...}}; if "id" in msg: err["id"] =
  msg["id"]; self._send(err, _client=client)`` blocks at the
  ``client.rate_limited`` and ``server.internal_error`` sites in
  ``ipc/transport_tcp.py`` are now routed through a single
  ``OutputMixin._send_error_envelope`` helper (defined in
  ``ipc/sender.py``). The stdin-path sites were already consolidated
  via ``_send_stdin_error_envelope`` (see
  ``tests/server/test_ipc_server_regressions.py``); this file covers
  the TCP-path sibling.

- **ER-84:** the eager ``len(str(msg))`` on the no-client push-event
  path is wrapped in ``_LazyInt`` so the recursive ``dict.__str__``
  cost is deferred until the logging framework actually renders the
  format string. These tests pin the lazy-evaluation contract: the
  wrapped callable runs ONLY when ``int()`` is invoked (which the
  ``%d`` format specifier does at render time), not at construction
  time.
"""

from __future__ import annotations

import inspect
import logging
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc.sender import OutputMixin, _LazyInt
from voice_typer.server.ipc_server import IPCServer

# ─── ZR-76: _send_error_envelope helper ───────────────────────────


class TestSendErrorEnvelopeHelper:
    """ZR-76 (TCP-path): ``_send_error_envelope`` consolidates the
    inline error-envelope construction at the ``client.rate_limited``
    and ``server.internal_error`` sites in ``transport_tcp.py``.
    """

    @pytest.fixture
    def server(self) -> IPCServer:
        """Minimal ``IPCServer`` with ``_send`` mocked, so we can
        assert on the envelope shape without spinning up TCP."""
        srv = IPCServer.__new__(IPCServer)
        srv._send = MagicMock()  # type: ignore[method-assign]
        return srv

    def test_helper_exists_on_output_mixin(self) -> None:
        """``_send_error_envelope`` is defined on ``OutputMixin`` so
        every ``IPCServer`` inherits it via the mixin composition."""
        assert hasattr(OutputMixin, "_send_error_envelope"), (
            "ZR-76: OutputMixin must define _send_error_envelope to "
            "consolidate the TCP-path inline error-envelope sites."
        )

    def test_helper_sends_canonical_envelope_shape(self, server) -> None:
        """The helper builds ``{"type": "error", "data": {"code": ...,
        "message": ...}}`` and forwards it to ``_send``."""
        server._send_error_envelope("client.rate_limited", "rate limit exceeded; backing off")
        server._send.assert_called_once()
        sent_msg = server._send.call_args.args[0]
        assert sent_msg["type"] == "error"
        assert sent_msg["data"]["code"] == "client.rate_limited"
        assert sent_msg["data"]["message"] == "rate limit exceeded; backing off"

    def test_helper_propagates_id_when_present(self, server) -> None:
        """When ``msg`` is a dict containing ``id``, the helper
        propagates ``id`` to the envelope root for client-side
        request/response correlation (B-6)."""
        server._send_error_envelope(
            "server.internal_error",
            "internal error",
            msg={"id": 42, "type": "get_status"},
        )
        sent_msg = server._send.call_args.args[0]
        assert sent_msg["id"] == 42

    def test_helper_omits_id_when_msg_is_none(self, server) -> None:
        """When ``msg`` is None (push-event / unsolicited error path),
        no ``id`` field is added to the envelope."""
        server._send_error_envelope("server.internal_error", "internal error")
        sent_msg = server._send.call_args.args[0]
        assert "id" not in sent_msg

    def test_helper_omits_id_when_msg_has_no_id(self, server) -> None:
        """When ``msg`` is a dict without an ``id`` key, no ``id``
        field is added (the helper uses ``"id" in msg``, not
        ``msg.get("id")``)."""
        server._send_error_envelope(
            "server.internal_error",
            "internal error",
            msg={"type": "heartbeat"},
        )
        sent_msg = server._send.call_args.args[0]
        assert "id" not in sent_msg

    def test_helper_forwards_client_kwarg(self, server) -> None:
        """The ``_client`` kwarg is forwarded to ``_send`` so a
        concurrent fast-auth reconnect cannot redirect the error to
        the wrong socket (SEC-8 race fix preserved)."""
        local_client = MagicMock(name="local_client")
        server._send_error_envelope(
            "client.rate_limited",
            "rate limit exceeded; backing off",
            _client=local_client,
        )
        assert server._send.call_args.kwargs.get("_client") is local_client

    def test_helper_forwards_out_kwarg(self, server) -> None:
        """The ``_out`` kwarg is forwarded to ``_send`` for the legacy
        stdin/stdout transport path."""
        stdout = MagicMock(name="stdout")
        server._send_error_envelope(
            "server.internal_error",
            "internal error",
            _out=stdout,
        )
        assert server._send.call_args.kwargs.get("_out") is stdout

    def test_helper_ignores_non_dict_msg(self, server) -> None:
        """When ``msg`` is a non-dict JSON value (list / int / str /
        None — all valid JSON), the helper MUST NOT raise and MUST NOT
        add an ``id`` field. Mirrors the ``isinstance(msg, dict)``
        guard that previously lived at every inline site."""
        for non_dict in ([1, 2, 3], 42, "hello", None):
            server._send.reset_mock()
            server._send_error_envelope("server.internal_error", "internal error", msg=non_dict)  # type: ignore[arg-type]
            sent_msg = server._send.call_args.args[0]
            assert "id" not in sent_msg, f"helper must not propagate id from non-dict msg ({non_dict!r})"


class TestTransportTcpUsesHelper:
    """Source-level pins: the ``client.rate_limited`` and
    ``server.internal_error`` sites in ``TCPTransportMixin`` must call
    ``_send_error_envelope`` (not inline ``self._send({"type":
    "error", ...})``)."""

    def test_rate_limited_site_uses_helper(self) -> None:
        from voice_typer.server.ipc.transport_tcp import TCPTransportMixin

        src = inspect.getsource(TCPTransportMixin._handle_tcp_connection)
        assert "_send_error_envelope(" in src, (
            "ZR-76: _handle_tcp_connection must call _send_error_envelope "
            "for the rate-limited site (the inline err={...} block was "
            "consolidated into the helper)."
        )
        # The namespaced rate_limited code must still appear in the
        # source so the envelope-contract parity test
        # (test_ipc_error_envelope_parity.py) keeps passing.
        assert "client.rate_limited" in src

    def test_internal_error_site_uses_helper(self) -> None:
        from voice_typer.server.ipc.transport_tcp import TCPTransportMixin

        src = inspect.getsource(TCPTransportMixin._tcp_dispatch_and_respond)
        assert "_send_error_envelope(" in src, (
            "ZR-76: _tcp_dispatch_and_respond must call "
            "_send_error_envelope for the internal_error site (the "
            "inline err={...} block was consolidated into the helper)."
        )
        # The namespaced internal_error code must still appear in the
        # source for the parity test.
        assert "server.internal_error" in src


# ─── ER-84: _LazyInt deferred evaluation ──────────────────────────


class TestLazyInt:
    """ER-84: ``_LazyInt`` defers ``len(str(msg))`` until the logging
    framework actually renders the format string. The wrapped callable
    MUST NOT be invoked at construction time; it MUST be invoked when
    ``int()`` is called (which the ``%d`` format specifier does at
    render time)."""

    def test_callable_not_invoked_at_construction(self) -> None:
        """Building a ``_LazyInt`` MUST NOT call the wrapped callable.
        This is the core ER-84 fix: the eager ``len(str(msg))`` was
        moved out of the positional-argument evaluation path."""
        called = []

        def expensive() -> int:
            called.append(True)
            return 42

        _LazyInt(expensive)
        assert called == [], (
            "ER-84: _LazyInt must defer the wrapped callable until "
            "int() is invoked — constructing the wrapper must be free."
        )

    def test_int_invokes_callable_once(self) -> None:
        """``int(_LazyInt(fn))`` MUST call ``fn`` exactly once and
        return its result. The ``%d`` format specifier dispatches to
        ``__int__``, so this is the path the logging framework takes
        when rendering ``"... (size=%d)" % args``."""
        called = []

        def fn() -> int:
            called.append(True)
            return 7

        lazy = _LazyInt(fn)
        result = int(lazy)
        assert result == 7
        assert len(called) == 1

    def test_percent_d_format_invokes_callable(self) -> None:
        """The ``%d`` format specifier MUST invoke ``__int__`` and
        substitute the integer result — this is what the logging
        framework does at render time. Verifies the lazy wrapper is
        transparent to ``%``-formatting."""
        result = "%d" % _LazyInt(lambda: 99)  # noqa: UP031  # intentional: verifies %-formatting dispatch to __int__
        assert result == "99"

    def test_callable_can_return_dict_len(self) -> None:
        """The realistic ER-84 use case: ``lambda: len(str(msg))``
        where ``msg`` is a dict. The lambda is deferred until render
        time, so the recursive ``dict.__str__`` cost is paid only when
        the log line is actually emitted (1st + every 100th occurrence
        at INFO; suppressed occurrences are zero-cost when DEBUG is
        disabled)."""
        msg = {"type": "transcription_partial", "text": "secret"}
        lazy = _LazyInt(lambda: len(str(msg)))
        # Construction is free.
        # int() triggers the stringification.
        rendered_size = int(lazy)
        assert rendered_size == len(str(msg))
        assert rendered_size > 0


class TestSendNoClientLazySizeHint:
    """ER-84 source-level pin: ``OutputMixin._send`` must wrap
    ``len(str(msg))`` in ``_LazyInt`` so the eager stringification
    is deferred. The source-string assertion in
    ``tests/test_ipc_no_client_log_redaction.py`` pins the literal
    ``"len(str(msg))"`` and ``"event (size=%d)"`` — both must remain
    in the source (the lambda body preserves the literal)."""

    def test_source_uses_lazy_int_wrapper(self) -> None:
        src = inspect.getsource(IPCServer._send)
        # The literal ``len(str(msg))`` MUST still appear in the source
        # (pinned by test_ipc_no_client_log_redaction.py line ~79).
        assert "len(str(msg))" in src, (
            "ER-84: _send source must still contain the literal "
            "'len(str(msg))' (the source-string regression test pins it)."
        )
        # The ``_LazyInt`` wrapper MUST be present so the computation
        # is deferred until render time.
        assert "_LazyInt" in src, (
            "ER-84: _send must wrap len(str(msg)) in _LazyInt so the "
            "recursive dict.__str__ cost is deferred until the logging "
            "framework actually renders the format string."
        )
        # The format string MUST still use the size hint.
        assert "event (size=%d)" in src
        # The old body-logging format MUST NOT be present.
        assert "event: %s" not in src

    def test_no_client_path_does_not_eagerly_stringify(self) -> None:
        """Runtime pin: when both INFO and DEBUG are disabled, the
        ``len(str(msg))`` callable MUST NOT be invoked at all (the
        logging framework short-circuits before formatting, and the
        ``_LazyInt`` wrapper never has ``__int__`` called). This is
        the behavioral fix for ER-84 — the eager stringification was
        the bug."""
        srv = IPCServer.__new__(IPCServer)
        srv.app = MagicMock()
        srv.app._shutting_down = False
        import threading

        srv._lock = threading.RLock()
        srv._pending_tcp = []
        srv._tcp_mode = False  # no client, no TCP mode → "no client" log path
        srv._tcp_client = None

        # Use a sentinel dict whose __str__ raises if invoked — proves
        # the stringification never happens when the log level filters
        # out the record.
        class _ExplodingDict(dict):
            def __str__(self) -> str:  # noqa: D401
                raise AssertionError(
                    "ER-84: len(str(msg)) was evaluated eagerly — the "
                    "_LazyInt wrapper should have deferred this until "
                    "the logging framework rendered the format string, "
                    "and the format string should NOT have been rendered "
                    "because both INFO and DEBUG are disabled."
                )

        msg = _ExplodingDict({"type": "transcription_final", "text": "secret"})

        # Disable both INFO and DEBUG on the IPC server logger so the
        # logging framework short-circuits before formatting. The
        # ``log_rate_limited`` helper still goes through its counter
        # increment, but the actual ``logger.log`` / ``logger.debug``
        # calls return without creating a LogRecord (or create one
        # that is filtered before formatting).
        ipc_logger = logging.getLogger("voice_typer.server.ipc_server")
        original_level = ipc_logger.level
        ipc_logger.setLevel(logging.WARNING)  # INFO and DEBUG both disabled
        try:
            # This MUST NOT raise — _LazyInt defers the str(msg) call,
            # and the logger short-circuits before formatting.
            srv._send(msg)
        finally:
            ipc_logger.setLevel(original_level)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
