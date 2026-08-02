"""SA-02 targeted regression tests for the ipc_server.py fixes.

Covers:
- XZ-IPC-001: ``main()`` must set ``server._tcp_mode = True``
  UNCONDITIONALLY before ``server.start()`` so the unauthenticated
  stdin listener is never spawned alongside the token-authenticated
  TCP / WS server. The standalone path (no ``--port`` / ``--ws``)
  previously fell through with ``_tcp_mode = False``, spawning BOTH
  the stdin listener AND the auto-picked TCP server — the stdin
  listener accepted unauthenticated JSON on the user's terminal.
- ZR-76: ``_send_stdin_error_envelope`` consolidates the three
  inline error-envelope construction sites in ``_run`` (invalid
  payload / invalid JSON / internal_error) so the envelope shape is
  defined in one place. Backward-compat pins:
    * ``invalid JSON`` site MUST keep the bare ``{"message": ...}``
      shape (no ``code``) — pinned by ``tests/server/test_run_loop.py
      ::test_handles_invalid_json``.
    * ``invalid payload`` site MUST keep both ``code`` and
      ``legacy_code`` fields.
    * ``internal error`` site MUST keep the ``server.internal_error``
      namespaced code.
- XZ-IPC-009: stale line-number references in comments replaced with
  function / label references. We assert the specific stale literal
  is gone from the source.
"""

from __future__ import annotations

import inspect
import io
import json

from tests.server.conftest import IPCServer

# main() sets _tcp_mode unconditionally ──────────────────


class TestStandaloneStdinSuppression:
    """XZ-IPC-001: ``main()`` never uses the unauthenticated stdin path.

    The fix removes the ``if port is not None or ws_mode:`` guard around
    ``server._tcp_mode = True`` and sets the flag unconditionally. We
    verify by inspecting ``main()``'s source — there must be no
    conditional guard around the ``_tcp_mode = True`` assignment, and
    the assignment must run BEFORE ``server.start()``.
    """

    def test_main_sets_tcp_mode_unconditionally(self) -> None:
        from voice_typer.server import ipc_server

        src = inspect.getsource(ipc_server.main)
        # The unconditional assignment must be present.
        assert "server._tcp_mode = True" in src, (
            "XZ-IPC-001: main() must set server._tcp_mode = True unconditionally before server.start()."
        )
        # The old conditional guard must be GONE.
        assert "if port is not None or ws_mode:" not in src, (
            "XZ-IPC-001: the conditional `if port is not None or ws_mode:` "
            "guard around _tcp_mode must be removed — standalone mode must "
            "also suppress the stdin listener."
        )
        # The assignment must come BEFORE the actual ``server.start()``
        # CALL (not the comment mention). We strip comments by removing
        # lines starting with ``#`` so the substring search only sees
        # real code.
        code_only = "\n".join(ln for ln in src.split("\n") if not ln.lstrip().startswith("#"))
        tcp_mode_idx = code_only.index("server._tcp_mode = True")
        start_idx = code_only.index("server.start()")
        assert tcp_mode_idx < start_idx, (
            "XZ-IPC-001: _tcp_mode must be set BEFORE server.start() so "
            "start() observes the flag and skips spawning the stdin thread."
        )

    def test_start_skips_stdin_thread_when_tcp_mode_true(self) -> None:
        """Smoke-test the start() branch that depends on _tcp_mode.

        ``start()`` checks ``if not self._tcp_mode:`` before spawning the
        stdin listener thread. With ``_tcp_mode = True`` (set
        unconditionally by main()), the stdin thread is NOT spawned —
        ``_stdin_thread`` stays ``None``.
        """
        from unittest.mock import MagicMock

        app = MagicMock()
        # Real IPCServer.start() does a lot of wiring (heartbeat thread,
        # event_bus subscribe, tray hook). We only care about the
        # ``_tcp_mode`` branch — so call start() with _tcp_mode=True and
        # verify _stdin_thread is None.
        srv = IPCServer(app)
        srv._tcp_mode = True
        # Avoid the real heartbeat / tray-hook side effects: stop()
        # immediately after start() to clean up the daemon threads
        # start() spawns.
        try:
            srv.start()
        finally:
            srv.stop()
        assert srv._stdin_thread is None, (
            "XZ-IPC-001: with _tcp_mode=True, start() must NOT spawn the "
            "stdin listener thread — it's an unauthenticated command "
            "channel alongside the token-authenticated TCP server."
        )

    def test_start_spawns_stdin_thread_when_tcp_mode_false(self, monkeypatch) -> None:
        """Counter-test: with ``_tcp_mode = False`` (the legacy stdin
        console path used by some tests), ``start()`` DOES spawn the
        stdin listener thread when the stdin gate is explicitly
        enabled. This pins the branch logic so the
        XZ-IPC-001 fix doesn't accidentally invert it."""
        from unittest.mock import MagicMock

        # The unauthenticated stdin listener is gated behind
        # VOICE_TYPER_ALLOW_STDIN_IPC=1 (XZ-IPC-001 hardening); the
        # counter-test must opt in explicitly.
        monkeypatch.setenv("VOICE_TYPER_ALLOW_STDIN_IPC", "1")

        app = MagicMock()
        srv = IPCServer(app)
        srv._tcp_mode = False
        try:
            srv.start()
        finally:
            srv.stop()
        # The stdin thread was spawned (then joined by stop()).
        # _stdin_thread is set to None by stop()'s join path in some
        # implementations; we check the attribute was populated by
        # observing that start() ran the spawn branch. The thread
        # object may be cleared by stop(), so we re-run start() and
        # inspect before stop().
        srv2 = IPCServer(app)
        srv2._tcp_mode = False
        try:
            srv2.start()
            assert srv2._stdin_thread is not None, (
                "Counter-test for XZ-IPC-001: with _tcp_mode=False and "
                "VOICE_TYPER_ALLOW_STDIN_IPC=1, start() must spawn the "
                "stdin listener thread (legacy stdin/stdout console path)."
            )
        finally:
            srv2.stop()


# _send_stdin_error_envelope helper ───────────────────────────


class TestSendStdinErrorEnvelope:
    """ZR-76: the three inline error-envelope sites in ``_run`` now go
    through ``_send_stdin_error_envelope``. The helper preserves the
    three distinct envelope shapes that the inline sites had.
    """

    def test_helper_exists_and_sends_via_send(self) -> None:
        """The helper is defined on IPCServer and delegates to _send."""
        from unittest.mock import MagicMock

        app = MagicMock()
        srv = IPCServer(app)
        srv._send = MagicMock()
        stdout = io.StringIO()
        srv._send_stdin_error_envelope(
            message="boom",
            code="server.internal_error",
            _out=stdout,
        )
        # _send must be called once with the error envelope.
        srv._send.assert_called_once()
        sent_msg, sent_kwargs = srv._send.call_args.args[0], srv._send.call_args.kwargs
        assert sent_msg["type"] == "error"
        assert sent_msg["data"]["message"] == "boom"
        assert sent_msg["data"]["code"] == "server.internal_error"
        assert sent_kwargs == {"_out": stdout}

    def test_helper_omits_code_when_none(self) -> None:
        """The bare ``{"message": "invalid JSON"}`` shape (no code)
        is preserved — pinned by ``tests/server/test_run_loop.py
        ::test_handles_invalid_json``."""
        from unittest.mock import MagicMock

        app = MagicMock()
        srv = IPCServer(app)
        srv._send = MagicMock()
        srv._send_stdin_error_envelope(message="invalid JSON", _out=io.StringIO())
        sent_msg = srv._send.call_args.args[0]
        assert "code" not in sent_msg["data"], (
            "ZR-76: when code=None, the helper must NOT include a 'code' "
            "field — the invalid-JSON envelope is intentionally bare for "
            "IPC-5 backward compat."
        )
        assert "legacy_code" not in sent_msg["data"]
        assert sent_msg["data"]["message"] == "invalid JSON"

    def test_helper_emits_namespaced_code_and_message(self) -> None:
        """The invalid-payload site includes the namespaced ``code``
        and ``message`` (the bare ``legacy_code`` alias was removed once
        the renderer migrated to the namespaced form)."""
        from unittest.mock import MagicMock

        app = MagicMock()
        srv = IPCServer(app)
        srv._send = MagicMock()
        srv._send_stdin_error_envelope(
            message="message must be a JSON object",
            code="client.invalid_payload",
            _out=io.StringIO(),
        )
        sent_msg = srv._send.call_args.args[0]
        assert sent_msg["data"]["code"] == "client.invalid_payload"
        assert sent_msg["data"]["message"] == "message must be a JSON object"

    def test_run_invalid_json_envelope_shape_unchanged(self, server) -> None:
        """End-to-end: ``_run`` still emits the bare envelope for
        invalid JSON. This is the IPC-5 backward-compat contract."""
        stdin = io.StringIO("not valid json\n")
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)
        msg = json.loads(stdout.getvalue().strip())
        assert msg == {"type": "error", "data": {"message": "invalid JSON"}}

    def test_run_invalid_payload_envelope_shape(self, server) -> None:
        """End-to-end: ``_run`` emits the namespaced envelope for
        non-dict JSON (e.g. a bare number)."""
        stdin = io.StringIO("42\n")
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)
        msg = json.loads(stdout.getvalue().strip())
        assert msg["type"] == "error"
        assert msg["data"]["code"] == "client.invalid_payload"
        assert msg["data"]["message"] == "message must be a JSON object"

    def test_run_calls_helper_not_inline_send(self) -> None:
        """Source-level: the three error sites in _run must call
        ``_send_stdin_error_envelope`` (not inline ``self._send({"
        type": "error", ...})``)."""

        src = inspect.getsource(IPCServer._run)
        # The helper must be called three times in _run.
        assert src.count("_send_stdin_error_envelope(") >= 3, (
            "ZR-76: _run must call _send_stdin_error_envelope for all "
            "three error sites (invalid payload / invalid JSON / "
            "internal_error)."
        )


# stale line-number references removed ───────────────────


class TestStaleLineRefs:
    """XZ-IPC-009: comments in ``ipc_server.py`` that referenced line
    numbers in OTHER files (or in this file before refactors) have been
    replaced with function / label references."""

    def test_no_stale_line_2166_reference(self) -> None:
        """The specific stale reference 'line ~2166' (which pointed at
        the ``_send`` shutdown-suppress gate that has since moved to
        ``ipc/sender.py``) must be gone from the source."""
        from voice_typer.server import ipc_server

        src = inspect.getsource(ipc_server)
        assert "line ~2166" not in src, (
            "XZ-IPC-009: the stale 'line ~2166' reference (the _send "
            "shutdown-suppress gate has moved to ipc/sender.py) must be "
            "replaced with a function/label reference."
        )

    def test_dispatch_comment_references_sender_module(self) -> None:
        """The dispatch comment now references ``OutputMixin._send`` in
        ``voice_typer/server/ipc/sender.py`` (a stable module/label
        reference) instead of a line number."""

        src = inspect.getsource(IPCServer._dispatch)
        # The new comment must reference the sender module / OutputMixin.
        assert "OutputMixin._send" in src or "ipc/sender.py" in src or "_cached_shutting_down" in src, (
            "XZ-IPC-009: _dispatch comment must reference the "
            "_cached_shutting_down read in OutputMixin._send in "
            "ipc/sender.py — not a stale line number."
        )
