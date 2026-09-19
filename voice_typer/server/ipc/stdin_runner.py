"""Stdin runner mixin for the IPC server (split from ``ipc_server.py``)."""

from __future__ import annotations

import json
import sys
import typing

from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.rate_limiter import _get_rate_limiter


class StdinRunnerMixin:
    """Legacy stdin/stdout IPC transport for :class:`IPCServer`."""

    def _send_stdin_error_envelope(
        self,
        *,
        message: str,
        code: str | None = None,
        _out: typing.IO[str] | None = None,
    ) -> None:
        """Build + send an error envelope on the legacy stdin/stdout path."""
        data: dict[str, object] = {"message": message}
        if code is not None:
            data["code"] = code
        self._send({"type": "error", "data": data}, _out=_out)

    def _run(
        self,
        _stdin=None,
        _stdout=None,
    ) -> None:
        """Read JSON lines from stdin, dispatch, write responses to stdout."""
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
                # the ``except Exception`` dispatch-failure handler (which
                msg: dict | None = None
                try:
                    msg = json.loads(line)
                    # before dispatch. ``_dispatch`` calls
                    if not isinstance(msg, dict):
                        # route through the shared
                        self._send_stdin_error_envelope(
                            message="message must be a JSON object",
                            code="client.invalid_payload",
                            _out=stdout,
                        )
                        continue
                    # Per-process rate limiter gate. The WS
                    msg_type = msg.get("type", "")
                    if not _get_rate_limiter(self).allow(command=msg_type):
                        self._send_stdin_error_envelope(
                            message="rate limit exceeded; backing off",
                            code="client.rate_limited",
                            _out=stdout,
                        )
                        continue
                    result = self._dispatch(msg)
                    self._send(result, _out=stdout)
                except json.JSONDecodeError:
                    # bare ``{"message": "invalid JSON"}`` envelope).
                    self._send_stdin_error_envelope(
                        message="invalid JSON",
                        _out=stdout,
                    )
                except Exception as dispatch_exc:
                    # NOTE: see docs/code-notes/ipc.md
                    msg_type = msg.get("type", "<unknown>") if isinstance(msg, dict) else "<unknown>"
                    log.error(
                        "[IPC] stdin dispatch failed for type=%r: %s",
                        msg_type,
                        dispatch_exc,
                        exc_info=True,
                    )
                    # dispatch-level error handler above) so the
                    self._send_stdin_error_envelope(
                        message="internal error",
                        code="server.internal_error",
                        _out=stdout,
                    )
        except OSError:
            pass  # stdin closed (e.g. during test teardown)
        # backend stuck in ``"hotkey_capture"`` state. The helper
        self._on_ipc_client_disconnect("stdin EOF. IPC client disconnected")
