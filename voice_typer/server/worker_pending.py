"""Pending offline-transcription queue for the runtime-pack worker (master plan §7.4)."""

from __future__ import annotations

import logging
import threading
import time
import typing

from voice_typer.server.duration import format_duration

log = logging.getLogger("voice_typer.server.ipc_server")

_MAX_PENDING = 64

_lock = threading.Lock()
_pending: list[dict[str, object]] = []


def pending_depth() -> int:
    """Return the number of queued requests."""
    with _lock:
        return len(_pending)


def enqueue(payload: dict[str, object]) -> int:
    """Queue *payload* for the worker; return the depth after enqueue."""
    with _lock:
        if len(_pending) >= _MAX_PENDING:
            _pending.pop(0)
            log.warning("[WORKER] pending transcribe queue full, dropped oldest")
        _pending.append(dict(payload))
        return len(_pending)


def drain_pending(send_fn: typing.Callable[[dict[str, object]], bool]) -> int:
    """Send every queued request via *send_fn*; return the sent count."""
    global _pending
    with _lock:
        batch = _pending
        _pending = []
    t0 = time.perf_counter()
    sent = 0
    failed: list[dict[str, object]] = []
    for payload in batch:
        try:
            if send_fn(payload):
                sent += 1
            else:
                failed.append(payload)
        except Exception:
            log.debug("[WORKER] queued transcribe send failed, re-queueing", exc_info=True)
            failed.append(payload)
    if failed:
        with _lock:
            _pending[0:0] = failed
    log.info("[WORKER] drained %d queued transcribe request(s)%s", sent, format_duration(time.perf_counter() - t0))
    return sent


def clear_pending() -> int:
    """Drop every queued request; return the dropped count (tests only)."""
    global _pending
    with _lock:
        dropped = len(_pending)
        _pending = []
    return dropped


def try_forward(payload: dict[str, object]) -> bool:
    """Forward *payload* via the Step-3 shared worker client.

    Returns ``True`` when the client accepted it (queued on its
    outbound hop), ``False`` when there is nowhere to send it yet
    (unknown port, full outbound queue) so the caller queues locally.
    Never raises.
    """
    try:
        from voice_typer.server import worker_client
    except ImportError:
        return False
    get_client = getattr(worker_client, "get_shared_client", None)
    if not callable(get_client):
        return False
    try:
        client = get_client()
    except Exception:
        log.debug("[WORKER] shared client lookup failed", exc_info=True)
        return False
    send = getattr(client, "send_transcribe", None)
    if not callable(send):
        return False
    try:
        return (
            send(
                str(payload.get("audio_path")),
                int(typing.cast(int, payload.get("sample_rate"))),
                typing.cast(str | None, payload.get("language")),
            )
            is not None
        )
    except Exception:
        log.exception("[WORKER] worker forward failed: queueing")
        return False


__all__ = [
    "clear_pending",
    "drain_pending",
    "enqueue",
    "pending_depth",
    "try_forward",
]
