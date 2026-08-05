"""Stdin runner mixin for the IPC server (split from ``ipc_server.py``).

Contains the :class:`StdinRunnerMixin` class — the legacy stdin/stdout
IPC transport (``_send_stdin_error_envelope`` + ``_run``) that is mixed
into :class:`IPCServer` via multiple inheritance.

The stdin/stdout path is the legacy transport, predating the TCP and
WebSocket transports. It is gated behind the ``VOICE_TYPER_ALLOW_STDIN_IPC=1``
env var (see ``LifecycleMixin.start``) because it bypasses the
``VOICE_TYPER_IPC_TOKEN`` handshake — a security control needed for the
network transports but redundant for the local terminal path.

The mixin accesses instance state (``self._running``, ``self._send``,
``self._dispatch``, ``self._on_ipc_client_disconnect``) which is
declared on :class:`IPCServer` itself — the mixin provides only the
method bodies.

Source-string-pinning tests (``tests/server/test_ipc_server_regressions.py``)
use ``inspect.getsource(IPCServer._run)`` and assert that the helper
``_send_stdin_error_envelope(`` is called at least three times. Because
``IPCServer._run`` resolves through MRO to ``StdinRunnerMixin._run``,
``inspect.getsource`` returns the source from this module — the body is
moved verbatim so the pinned substring appears in the source.
"""

from __future__ import annotations

import json
import sys
import typing

from voice_typer.server.handlers._log import log


class StdinRunnerMixin:
    """Legacy stdin/stdout IPC transport for :class:`IPCServer`.

    Provides ``_send_stdin_error_envelope`` and ``_run``. The mixin
    assumes the host class declares ``_running`` (instance attribute)
    and provides ``_send``, ``_dispatch`` and ``_on_ipc_client_disconnect``
    methods (themselves provided by other mixins in the IPCServer
    composition).
    """

    def _send_stdin_error_envelope(
        self,
        *,
        message: str,
        code: str | None = None,
        _out: typing.IO[str] | None = None,
    ) -> None:
        """Build + send an error envelope on the legacy stdin/stdout path.

        consolidates the three inline error-envelope construction
        sites in :meth:`_run` (invalid payload / invalid JSON /
        internal_error) into a single helper so the envelope shape is
        defined in one place. The TCP / WS paths use
        :meth:`_shutting_down_error` (which returns the envelope; the
        caller sends it via ``_send`` with the TCP ``_client`` kwarg);
        this stdin-path helper sends directly because every call site
        uses ``_out=stdout`` (the TextIO variant of ``_send``).

        ``code`` is optional so the helper can express the bare
        ``{"message": "invalid JSON"}`` envelope ( backward-compat
        with ``tests/test_server.py``'s ``test_handles_invalid_json``,
        which asserts the no-``code`` shape).
        """
        data: dict[str, object] = {"message": message}
        if code is not None:
            data["code"] = code
        self._send({"type": "error", "data": data}, _out=_out)

    def _run(
        self,
        _stdin=None,
        _stdout=None,
    ) -> None:
        """Read JSON lines from stdin, dispatch, write responses to stdout.

        Parameters
        ----------
        _stdin : Optional[TextIO]
            Input stream (default ``sys.stdin``).  Provided for testing.
        _stdout : Optional[TextIO]
            Output stream (default ``sys.stdout``).  Provided for testing.
        """
        stdin = _stdin or sys.stdin
        stdout = _stdout or sys.stdout
        try:
            iterator = iter(stdin)
        except OSError:
            return  # stdin not available (e.g. during testing)
        try:
            for line in iterator:
                if not self._running:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    # validate that the parsed JSON is a dict
                    # before dispatch. ``_dispatch`` calls
                    # ``msg.get("type")`` which raises ``AttributeError``
                    # if ``msg`` is a list/int/str/None (all valid JSON).
                    # Previously the ``except json.JSONDecodeError`` did
                    # NOT catch ``AttributeError``, so a single non-dict
                    # JSON line on stdin killed the IPC thread silently
                    # (keyboard ownership was not reset, app became
                    # unresponsive with no diagnostic). The TCP path was
                    # hardened by  but the stdin path was not
                    # updated in lockstep.
                    if not isinstance(msg, dict):
                        # route through the shared
                        # ``_send_stdin_error_envelope`` helper so the
                        # envelope shape is defined in one place.
                        # Namespaced form (canonical).
                        self._send_stdin_error_envelope(
                            message="message must be a JSON object",
                            code="client.invalid_payload",
                            _out=stdout,
                        )
                        continue
                    result = self._dispatch(msg)
                    self._send(result, _out=stdout)
                except json.JSONDecodeError:
                    #  note: the TCP path now emits
                    # ``{"code": "invalid_payload", "message": "invalid JSON"}``
                    # to match the WS path (see ``_handle_tcp_connection``).
                    # The stdin/stdout (legacy console) path is
                    # intentionally left WITHOUT the ``code`` field to
                    # preserve backward compatibility with the
                    # existing ``test_handles_invalid_json`` contract
                    # in ``tests/test_server.py`` (which asserts the
                    # bare ``{"message": "invalid JSON"}`` envelope).
                    # The stdin path is not in the  parity scope
                    # (the directive only mentions TCP vs WS); a
                    # future task may align all three paths.
                    # route through the shared helper. ``code``
                    # is intentionally omitted to preserve the
                    #  backward-compat contract pinned by
                    # ``tests/test_server.py::test_handles_invalid_json``
                    # (bare ``{"message": "invalid JSON"}`` envelope).
                    self._send_stdin_error_envelope(
                        message="invalid JSON",
                        _out=stdout,
                    )
                except Exception as dispatch_exc:
                    # mirror the TCP path's  hardening —
                    # catch ANY exception from ``_dispatch`` so a
                    # handler bug doesn't silently kill the stdin
                    # thread. Log server-side with traceback; return a
                    # generic ``internal_error`` envelope to the client.
                    #
                    # PII guard: log ONLY the command ``type`` (extracted
                    # from the already-parsed ``msg`` BEFORE the dispatch
                    # failure) — NOT the raw stdin line. The raw line is
                    # 120 chars of stdin JSON which can include API keys
                    # (``set_config`` carries ``cloud_api_key`` /
                    # ``openai_api_key``) or transcription text
                    # (``transcribe_final`` carries the user's audio
                    # payload). Logging those would leak PII / secrets
                    # into the server log. ``msg`` is guaranteed to be a
                    # dict at this point (the ``isinstance(msg, dict)``
                    # gate above dispatched non-dict JSON to the
                    # invalid-payload branch via ``continue``), so
                    # ``msg.get("type", "<unknown>")`` is safe; the
                    # defensive ``isinstance`` re-check is belt-and-
                    # suspenders for the pathological case where
                    # ``_dispatch`` itself mutated ``msg``.
                    msg_type = msg.get("type", "<unknown>") if isinstance(msg, dict) else "<unknown>"
                    log.error(
                        "[IPC] stdin dispatch failed for type=%r: %s",
                        msg_type,
                        dispatch_exc,
                        exc_info=True,
                    )
                    #  align to the namespaced
                    # ``server.internal_error`` form (same as the TCP
                    # dispatch-level error handler above) so the
                    # renderer can switch on a single canonical prefix.
                    # route through the shared helper.
                    self._send_stdin_error_envelope(
                        message="internal error",
                        code="server.internal_error",
                        _out=stdout,
                    )
        except OSError:
            pass  # stdin closed (e.g. during test teardown)
        # stdin EOF (or OSError on read) means the IPC
        # client is gone. If we're still running, reset keyboard
        # ownership so a crashed CLI client doesn't leave the
        # backend stuck in ``"hotkey_capture"`` state. The helper
        # is a no-op during shutdown (``self._running == False``).
        self._on_ipc_client_disconnect("stdin EOF — IPC client disconnected")
