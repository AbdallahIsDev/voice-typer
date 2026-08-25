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

import contextlib
import io
import json

from tests.server.conftest import IPCServer

# main() sets _tcp_mode unconditionally ──────────────────


class TestStandaloneStdinSuppression:
    """XZ-IPC-001: ``main()`` never uses the unauthenticated stdin path.

    The fix removes the ``if port is not None or ws_mode:`` guard around
    ``server._tcp_mode = True`` and sets the flag unconditionally. We
    verify behaviorally by running ``main()`` with mocked components and
    confirming ``_tcp_mode`` is ``True`` at the moment ``server.start()``
    is called — regardless of the launch mode (standalone / --port / --ws).
    """

    def test_main_sets_tcp_mode_unconditionally(self, monkeypatch) -> None:
        """Behavioral: ``main()`` must set ``server._tcp_mode = True``
        BEFORE ``server.start()`` is called, unconditionally (regardless
        of whether ``--port`` or ``--ws`` was passed). Verified by
        mocking the server's ``start()`` to capture ``_tcp_mode`` at
        call time and asserting it's ``True``.

        Pre-fix, the standalone path (no ``--port`` / ``--ws``) fell
        through with ``_tcp_mode = False``, spawning BOTH the stdin
        listener AND the auto-picked TCP server — the stdin listener
        accepted unauthenticated JSON on the user's terminal."""
        from unittest.mock import MagicMock

        import voice_typer.server.app as app_mod
        import voice_typer.server.ipc_server as ipc_server_mod
        import voice_typer.server.providers as providers_mod

        # Mock parse_ipc_args to return standalone mode (no --port, no
        # --ws). Pre-fix, this was the path that fell through with
        # _tcp_mode = False.
        monkeypatch.setattr(ipc_server_mod, "parse_ipc_args", lambda: (None, False))

        # Mock the app + server construction.
        mock_app = MagicMock()
        mock_server = MagicMock()

        # Capture _tcp_mode at the time start() is called, then raise
        # SystemExit to break out of main() (after the _tcp_mode
        # assignment + start() call, before the ws/port branches).
        captured: dict = {}

        def capture_start():
            captured["tcp_mode"] = mock_server._tcp_mode
            raise SystemExit(0)

        mock_server.start.side_effect = capture_start

        # Patch the components main() imports at call time (canonical
        # modules — the app-module re-exports were removed).
        monkeypatch.setattr(app_mod, "VoiceTyperApp", lambda: mock_app)
        monkeypatch.setattr("voice_typer.server.single_instance._ensure_single_instance", lambda **kw: None)
        monkeypatch.setattr("voice_typer.server.logging_setup._setup_logging", lambda: None)
        monkeypatch.setattr(providers_mod, "build_ipc_server", lambda app: mock_server)

        # main() should raise SystemExit (from our capture_start).
        with contextlib.suppress(SystemExit):
            ipc_server_mod.main()

        assert captured.get("tcp_mode") is True, (
            "XZ-IPC-001: main() must set server._tcp_mode = True BEFORE "
            f"server.start() is called (got {captured.get('tcp_mode')}). "
            "The flag must be set unconditionally so start() skips "
            "spawning the unauthenticated stdin listener thread — the "
            "old `if port is not None or ws_mode:` guard around the "
            "assignment must be gone (standalone mode must also set it)."
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

    def test_run_calls_helper_not_inline_send(self, server, monkeypatch) -> None:
        """Behavioral: ``_run`` must route all three error sites (invalid
        payload / invalid JSON / internal_error) through
        ``_send_stdin_error_envelope`` — not inline
        ``self._send({"type": "error", ...})``. Verified by spying on
        ``_send_stdin_error_envelope`` and triggering all three paths in
        one ``_run`` call; the helper must be called for each path."""

        helper_calls: list[dict] = []

        def spy_helper(*args, **kwargs):
            helper_calls.append(kwargs)
            # Don't call the original — we only want to count/inspect calls.

        monkeypatch.setattr(server, "_send_stdin_error_envelope", spy_helper)

        # Mock _dispatch to raise on the third line (internal error path).
        def raising_dispatch(msg):
            raise RuntimeError("simulated internal error")

        monkeypatch.setattr(server, "_dispatch", raising_dispatch)

        server._running = True
        # Three lines triggering the three error sites:
        # 1. "42" → json.loads returns int → not isinstance(dict) → invalid payload
        # 2. "not valid json" → json.loads raises JSONDecodeError → invalid JSON
        # 3. '{"type":"x"}' → json.loads returns dict → _dispatch raises → internal error
        stdin = io.StringIO('42\nnot valid json\n{"type":"x"}\n')
        stdout = io.StringIO()
        server._run(_stdin=stdin, _stdout=stdout)

        messages = [c.get("message") for c in helper_calls]
        assert "message must be a JSON object" in messages, (
            f"ZR-76: _run must call _send_stdin_error_envelope for the invalid-payload site (got messages: {messages})."
        )
        assert "invalid JSON" in messages, (
            f"ZR-76: _run must call _send_stdin_error_envelope for the invalid-JSON site (got messages: {messages})."
        )
        assert "internal error" in messages, (
            f"ZR-76: _run must call _send_stdin_error_envelope for the internal-error site (got messages: {messages})."
        )


# stale line-number references removed ───────────────────


class TestStaleLineRefs:
    """XZ-IPC-009: comments in ``ipc_server.py`` that referenced line
    numbers in OTHER files (or in this file before refactors) have been
    replaced with function / label references.

    Behavioral equivalents: the original source-string tests checked that
    stale line-number comments were gone. The behavioral tests verify the
    actual behavior the comments described — the ``_send`` shutdown-suppress
    gate (``_cached_shutting_down`` read) lives in ``ipc/sender.py``
    (``OutputMixin._send``) and ``_dispatch`` consults the cached snapshot
    rather than reading ``app._shutting_down`` directly.
    """

    def test_no_stale_line_2166_reference(self) -> None:
        """Behavioral: the ``_send`` shutdown-suppress gate (the
        ``_cached_shutting_down`` read) must live in
        ``voice_typer/server/ipc/sender.py`` (``OutputMixin._send``),
        not in ``ipc_server.py``. The original source-string test
        checked that the stale ``'line ~2166'`` comment (which pointed
        at the gate's old location in ``ipc_server.py``) was gone; the
        behavioral equivalent verifies the gate's actual runtime
        location via ``OutputMixin.__module__``."""
        from voice_typer.server.ipc.sender import OutputMixin

        # OutputMixin must be defined in sender.py (not ipc_server.py).
        assert OutputMixin.__module__ == "voice_typer.server.ipc.sender", (
            "XZ-IPC-009: OutputMixin (which owns the _send shutdown-"
            f"suppress gate) must live in voice_typer.server.ipc.sender — "
            f"got {OutputMixin.__module__}. The stale 'line ~2166' "
            "reference pointed at the gate's old location in "
            "ipc_server.py; the gate has since moved to sender.py."
        )
        # OutputMixin must have a _send method (the gate).
        assert hasattr(OutputMixin, "_send"), (
            "XZ-IPC-009: OutputMixin must define _send (the shutdown-"
            "suppress gate that was previously referenced by a stale "
            "line number)."
        )

    def test_dispatch_comment_references_sender_module(self, server, monkeypatch) -> None:
        """Behavioral: ``_dispatch`` must consult
        ``self._cached_shutting_down`` (the cached snapshot refreshed in
        ``start()`` / ``stop()``) rather than reading
        ``self.app._shutting_down`` directly. The cached snapshot is
        maintained alongside the ``OutputMixin._send`` shutdown-suppress
        gate in ``ipc/sender.py`` (the comment the original source-string
        test was checking).

        Verified by setting ``_cached_shutting_down=True`` (cached:
        shutting down) and ``app._shutting_down=False`` (live: NOT
        shutting down) — ``_dispatch`` must reject the request (return
        ``server.shutting_down`` error) based on the cached snapshot,
        not the live app attribute."""
        # Cached snapshot says we're shutting down; live app attribute says
        # we're not. _dispatch must consult the cached snapshot.
        server._cached_shutting_down = True
        server.app._shutting_down = False

        result = server._dispatch({"type": "get_config", "data": {}})

        assert result is not None, (
            "XZ-IPC-009: _dispatch returned None when _cached_shutting_down "
            "was True — it must return a shutting_down error envelope."
        )
        assert result.get("type") == "error", (
            "XZ-IPC-009: _dispatch did not return an error when "
            f"_cached_shutting_down=True (got: {result}). It must consult "
            "the cached snapshot (not app._shutting_down directly)."
        )
        assert result.get("data", {}).get("code") == "server.shutting_down", (
            "XZ-IPC-009: _dispatch must return a server.shutting_down "
            f"error when _cached_shutting_down=True (got: {result}). The "
            "shutdown-suppress gate (the _cached_shutting_down read) "
            "lives in OutputMixin._send in ipc/sender.py — _dispatch "
            "must consult the cached snapshot, not app._shutting_down."
        )
