"""Output / push mixin for the IPC server (Phase 4.5 split)."""

import contextlib
import json
import logging
import select
import socket
from collections import deque
from collections.abc import Callable
from typing import TextIO

from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.rate_limiter import _TCP_WRITE_TIMEOUT_SECONDS
from voice_typer.server.ipc.transport import _TCPLineIO
from voice_typer.server.log_rate_limit import log_rate_limited

# NOTE: see docs/code-notes/ipc.md
_SHUTDOWN_ALLOWLIST: frozenset[str] = frozenset(
    {
        "relaunch_app",
        "quit_app",
        "transcription_final",
        "transcription_partial",
        "vocabulary_suggestion",
    }
)

# (``_TCP_WRITE_TIMEOUT_SECONDS`` in ``ipc/rate_limiter.py``). Both
_TCP_PENDING_DRAIN_CAP: int = 100
_TCP_PENDING_BUFFER_CAP: int = 1000

# hard size cap on a single outbound TCP frame. Matches the WS
_TCP_MAX_OUTBOUND_BYTES: int = 1 * 1024 * 1024


class _LazyInt:
    """Deferred ``int`` for ``%d`` log formatting."""

    __slots__ = ("_fn",)

    def __init__(self, fn: Callable[[], int]) -> None:
        self._fn = fn

    def __int__(self) -> int:
        return int(self._fn())


class _PendingBuffer(deque[str]):
    """Bounded FIFO buffer for ``IPCServer._pending_tcp``."""

    def __init__(self, maxlen: int | None = None) -> None:
        super().__init__(maxlen=maxlen)

    def __radd__(self, other: object) -> "_PendingBuffer":
        # matching the manual ``del self._pending_tcp[:dropped]`` cap-drop
        if isinstance(other, list):
            result: _PendingBuffer = _PendingBuffer(maxlen=self.maxlen)
            result.extend(other)
            result.extend(self)
            return result
        return NotImplemented

    def __eq__(self, other: object) -> bool:
        # ``deque.__eq__`` returns ``NotImplemented`` for non-deque
        if isinstance(other, list):
            return list(self) == other
        if isinstance(other, deque):
            return list(self) == list(other)
        return NotImplemented


def _await_socket_writable(conn: socket.socket) -> None:
    """Block until *conn* is writable or the write timeout elapses."""
    try:
        _r, w, _x = select.select([], [conn], [], _TCP_WRITE_TIMEOUT_SECONDS)
    except (OSError, ValueError, TypeError, AttributeError):
        # ``select.select`` raises on closed/invalid fds or exotic
        return
    if w:
        return
    # Cross-check with ``select.poll``: some sandboxed Linux envs
    try:
        poller = select.poll()
        poller.register(conn, select.POLLOUT)
        events = poller.poll(int(_TCP_WRITE_TIMEOUT_SECONDS * 1000))
    except (OSError, ValueError, TypeError, AttributeError):
        events = []
    if events:
        return
    raise TimeoutError(
        f"TCP write timed out after {_TCP_WRITE_TIMEOUT_SECONDS}s "
        f"(select.select + select.poll both reported socket not writable)"
    )


class OutputMixin:
    """Output / push methods for :class:`IPCServer`."""

    def push(self, msg: dict) -> None:
        """Send an unsolicited event (no ``id`` field)."""
        self._send(msg)

    def _send_error_envelope(
        self,
        code: str,
        message: str,
        *,
        msg: dict | None = None,
        _client: _TCPLineIO | None = None,
        _out: TextIO | None = None,
    ) -> None:
        """Build + send a structured error envelope (DRY helper)."""
        err: dict[str, object] = {
            "type": "error",
            "data": {"code": code, "message": message},
        }
        if isinstance(msg, dict) and "id" in msg:
            err["id"] = msg["id"]
        self._send(err, _client=_client, _out=_out)

    def _send(
        self,
        msg: dict | None,
        _out: TextIO | None = None,
        _client: _TCPLineIO | None = None,
    ) -> None:
        """newer connection by a concurrent fast-auth client (SEC-8 race)."""
        if msg is None:
            return

        # Step 1: snapshot transport state under the lock.  This is fast
        with self._lock:
            out = _out
            # prefer the caller-provided local client (the
            tcp_client = _client if _client is not None else self._tcp_client
            tcp_mode = self._tcp_mode
            # its root: with no snapshot+clear, no other thread can
            pending: list[str] | None = None
            if tcp_client is not None and self._pending_tcp:
                pending = list(self._pending_tcp)
                self._pending_tcp.clear()

        # Step 2: serialize + write OUTSIDE the lock.  A slow client can
        line = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))

        if out is not None:
            # Stdin/stdout mode, used in tests and the legacy console
            out.write(line + "\n")
            out.flush()
            return

        if tcp_client is not None:
            # reused for both the size-cap check (below) AND the actual
            line_bytes = (line + "\n").encode("utf-8")
            # cap the outbound TCP frame size before acquiring the
            if len(line_bytes) > _TCP_MAX_OUTBOUND_BYTES:
                log.error(
                    "[IPC] outbound TCP frame exceeds %d bytes, dropping",
                    _TCP_MAX_OUTBOUND_BYTES,
                )
                # re-merge the pending snapshot so the dropped
                if pending:
                    _pending_cap_drop = _TCP_PENDING_BUFFER_CAP
                    with self._lock:
                        self._pending_tcp = pending + self._pending_tcp
                        if len(self._pending_tcp) > _pending_cap_drop:
                            _dropped = len(self._pending_tcp) - _pending_cap_drop
                            del self._pending_tcp[:_dropped]
                return
            # NOTE: see docs/code-notes/ipc.md
            _is_shutting_down = getattr(self, "_cached_shutting_down", False) is True
            msg_type = msg.get("type", "")
            # Allow critical shutdown events through; suppress others.
            _shutdown_allowlist = _SHUTDOWN_ALLOWLIST
            # dispatch responses (which carry an ``id`` field)
            if _is_shutting_down and "id" not in msg and msg_type not in _shutdown_allowlist:
                with self._lock:
                    if self._tcp_client is tcp_client:
                        with contextlib.suppress(Exception):
                            self._tcp_client.close()
                        self._tcp_client = None
                    # ``_pending_tcp`` so events queued for this (now-
                    if pending:
                        # cap from the ``tcp_mode`` branch is enforced
                        self._pending_tcp = pending + self._pending_tcp
                        if len(self._pending_tcp) > _TCP_PENDING_BUFFER_CAP:
                            _dropped = len(self._pending_tcp) - _TCP_PENDING_BUFFER_CAP
                            del self._pending_tcp[:_dropped]
                return
            # NOTE: see docs/code-notes/ipc.md
            _undrained: list[str] = []
            with self._tcp_write_lock:
                try:
                    tcp_client.write(line_bytes)
                    _await_socket_writable(tcp_client.conn)
                    tcp_client.flush()
                    # PERF- / SEC-008: drain at most the most recent
                    _drain_cap = _TCP_PENDING_DRAIN_CAP
                    if pending:
                        # entries that exceed the drain cap, these are
                        if len(pending) > _drain_cap:
                            older = list(pending[:-_drain_cap])
                            recent = list(pending[-_drain_cap:])
                        else:
                            older = []
                            recent = list(pending)
                        _drain_failed_at: int | None = None
                        # ``sendall`` syscalls under ``_tcp_write_lock``
                        for _i, p in enumerate(recent):
                            try:
                                tcp_client.write(p + "\n")
                            except Exception:
                                log.debug("[IPC] client write failed during pending drain (buffer)")
                                _drain_failed_at = _i
                                break
                        if _drain_failed_at is None:
                            # broken pipe / write timeout), treat ALL
                            try:
                                _await_socket_writable(tcp_client.conn)
                                tcp_client.flush()
                            except Exception:
                                log.debug("[IPC] client write failed during pending drain flush")
                                _drain_failed_at = 0
                        if _drain_failed_at is not None:
                            # reset the write buffer so any
                            with contextlib.suppress(Exception):
                                tcp_client._reset_write_buffer()
                            # The entries at/after the failure index were
                            _undrained = older + recent[_drain_failed_at:]
                        elif older:
                            # Drain succeeded for ``recent``; ``older``
                            _undrained = older
                except (TimeoutError, OSError) as exc:
                    log.debug("[IPC] client write failed: %s", exc)
                    # silently dropped here, up to 1000 queued push
                    if pending:
                        _undrained = list(pending)
                    # Mark the client as dead so the accept loop will pick
                    with self._lock:
                        if self._tcp_client is tcp_client:
                            with contextlib.suppress(Exception):
                                self._tcp_client.close()
                            self._tcp_client = None
            # first, then any events a concurrent thread appended
            if _undrained:
                with self._lock:
                    self._pending_tcp = _undrained + self._pending_tcp
                    if len(self._pending_tcp) > _TCP_PENDING_BUFFER_CAP:
                        _dropped = len(self._pending_tcp) - _TCP_PENDING_BUFFER_CAP
                        del self._pending_tcp[:_dropped]
                log.debug(
                    "[IPC] re-merged %d undrained pending entries",
                    len(_undrained),
                )
            return

        if tcp_mode:
            # SEC-008: cap _pending_tcp to prevent unbounded
            _pending_cap = _TCP_PENDING_BUFFER_CAP
            with self._lock:
                # concurrent thread appended between our snapshot+clear
                if pending:
                    self._pending_tcp = pending + self._pending_tcp + [line]
                else:
                    self._pending_tcp.append(line)
                if len(self._pending_tcp) > _pending_cap:
                    dropped = len(self._pending_tcp) - _pending_cap
                    del self._pending_tcp[:dropped]
                    cap_dropped = dropped
                else:
                    cap_dropped = 0
            if cap_dropped:
                log.warning(
                    "[IPC] _pending_tcp cap exceeded; dropped %d old entries",
                    cap_dropped,
                )
            return

        #    already capped by the audio callback.  Acceptable
        msg_type = msg.get("type", "unknown")
        # Waveform bubble level events are very high frequency
        if msg_type in ("bubble_level", "waveform"):
            # saturate a slow disk's log buffer. Rate-limit to every
            log_rate_limited(
                log,
                logging.DEBUG,
                "[IPC] no client; dropping high-freq %s event",
                msg_type,
                key="ipc-no-client-drop-high-freq",
                every_n=100,
            )
        else:
            # NOTE: see docs/code-notes/ipc.md
            log_rate_limited(
                log,
                logging.INFO,
                "[IPC] no client; dropping %s event (size=%d)",
                msg_type,
                _LazyInt(lambda: len(str(msg))),
                key="ipc-no-client-drop",
                every_n=100,
            )


__all__ = [
    "OutputMixin",
    "_SHUTDOWN_ALLOWLIST",
    "_TCP_PENDING_DRAIN_CAP",
    "_TCP_PENDING_BUFFER_CAP",
    # exported so tests can import the constant and assert the
    "_TCP_MAX_OUTBOUND_BYTES",
    # exported so ``ipc_server.IPCServer.__init__`` can construct the
    "_PendingBuffer",
    # exported so tests can verify the lazy-evaluation contract
    "_LazyInt",
]
