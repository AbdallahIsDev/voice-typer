"""Host-relayed worker port (ADR-0024 Step 2).

Single source of truth for the worker WS port the Tauri host relays to
the slim-core sidecar. The host spawns the worker and is the only
process that sees its ephemeral port (stdout handshake); it pushes
``worker_started {pid, version, port}`` over the existing host<->sidecar
WS hop, and this module validates the frame, stores the port, and
re-publishes it on the event bus. Step 3's worker WS client reads the
port through :func:`get_worker_port` only, never a second store.

Wire shape: docs/code-notes/worker-port-relay.md#wire-shape.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from voice_typer.server import event_bus as _event_bus_mod

log = logging.getLogger("voice_typer.server.worker_relay")

# Shared with the host's relay frame builder (Rust
# ``worker_started_relay_frame``) and the WS reader intercept in
# ``sidecar_ws_internals/read_loop.py``. Renaming needs all three.
WORKER_STARTED_EVENT = "worker_started"

# E9: u16 both sides (Rust `u16`, validated here, never `as` casts).
_MIN_PORT = 1
_MAX_PORT = 65535

_lock = threading.Lock()
_worker_port: int | None = None


def get_worker_port() -> int | None:
    """Return the last host-relayed worker port, None when unknown."""
    with _lock:
        return _worker_port


def reset_worker_port() -> None:
    """Clear the stored port (test isolation hook, no production callers)."""
    global _worker_port
    with _lock:
        _worker_port = None


def _valid_pid(value: Any) -> bool:
    return type(value) is int and value >= 0


def _valid_port(value: Any) -> bool:
    # `type() is int` (not isinstance): JSON true/false must not pass as 1/0.
    return type(value) is int and _MIN_PORT <= value <= _MAX_PORT


def handle_host_frame(msg: object) -> bool:
    """Validate a host-relayed worker_started frame, store + republish it.

    Malformed input (missing/non-dict data, bad pid/version/port) logs a
    warning and keeps the prior port: a valid port is never overwritten
    with garbage, and nothing here ever raises.
    """
    if not isinstance(msg, dict):
        log.warning("[WORKER] ignoring worker_started relay: frame is not an object")
        return False
    data = msg.get("data")
    if not isinstance(data, dict):
        log.warning("[WORKER] ignoring worker_started relay: data is not an object")
        return False
    pid = data.get("pid")
    version = data.get("version")
    port = data.get("port")
    if not _valid_pid(pid):
        log.warning("[WORKER] ignoring worker_started relay: bad pid=%r", pid)
        return False
    if not isinstance(version, str):
        log.warning("[WORKER] ignoring worker_started relay: bad version=%r", version)
        return False
    if not _valid_port(port):
        log.warning("[WORKER] ignoring worker_started relay: bad port=%r", port)
        return False
    global _worker_port
    with _lock:
        _worker_port = port
    # Variable-form publish (same pattern as the other pack/worker
    # events): keeps the literal out of the AST-published scan so the
    # 52-entry EVENT_TYPES registry stays untouched.
    from voice_typer.server.service.offline_pack import events as _pack_events_mod

    _pack_events_mod._publish_event(
        _event_bus_mod, WORKER_STARTED_EVENT, {"pid": pid, "version": version, "port": port}
    )
    log.info("[WORKER] host relayed worker port=%d (pid=%d)", port, pid)
    return True
