"""In-process event bus for broadcasting events to subscribers.

B-1: extracted from ``voice_typer.server.ipc_server._push_event_now``
to break the tight coupling between 12+ domain modules and the IPC
transport layer.

Architecture
------------
This module is the LEAF of the dependency tree.  It imports nothing
from ``voice_typer.*`` (only stdlib) so that any other module can
import it without risk of a circular import.

Domain modules (recording, service, app, tray, hotkey_dispatcher,
level_monitor, dictation_pipeline, startup_tasks, recording_controller,
handlers/config_handlers, handlers/system_handlers, tray_window) call
``publish(event)`` to broadcast a JSON-lines event.

The IPC server (``voice_typer.server.ipc_server.IPCServer``) calls
``subscribe(self.push)`` on ``start()`` and ``unsubscribe(self.push)``
on ``stop()`` so that every published event is forwarded to the
connected Electron renderer over TCP (or to stdout in stdin/stdout
mode).

Other transports (CLI, gRPC, future WebSocket) can subscribe the same
way without touching the domain modules.

Thread safety
-------------
``subscribe`` / ``unsubscribe`` / ``publish`` are all thread-safe.
A re-entrant lock (``threading.RLock``) guards the subscriber set so
that a subscriber which itself calls ``publish`` (re-entrant publish)
does not deadlock.  Re-entrant publish is not encouraged but is
guaranteed not to deadlock.

Subscriber exception isolation
------------------------------
A subscriber that raises is logged at DEBUG level (with ``exc_info``)
and skipped.  Other subscribers still receive the event.  This matches
the previous ``_push_event_now`` semantics and is verified by
``tests/test_event_bus.py::TestSubscriberExceptionIsolation``.
"""

from __future__ import annotations

import logging
import threading
import typing

log = logging.getLogger("voice_typer.server.event_bus")

# Module-level singleton state.  No class instance needed — this is
# process-global by design so that domain modules can publish without
# holding a reference to any particular IPC server or app instance.
#
# A ``set`` (not a ``list``) so duplicate ``subscribe`` calls for the
# same callable are deduplicated; this matters because ``IPCServer``'s
# ``start()`` is idempotent across stop/start cycles in tests and we
# do not want the same callable registered twice.
_subscribers: set[typing.Callable[[dict], None]] = set()

# RLock (not Lock) so a subscriber that calls publish() re-entrantly
# does not deadlock.  Re-entrant publish is discouraged but supported.
_lock = threading.RLock()


def subscribe(callback: typing.Callable[[dict], None] | None) -> None:
    """Register *callback* to receive every published event.

    Calling with ``None`` is a no-op (matches the previous
    ``_set_push_event`` semantics where ``None`` was used as a
    sentinel and rejected).

    Duplicate callbacks are stored only once (set semantics).
    Safe to call from any thread.
    """
    if callback is None:
        return
    with _lock:
        _subscribers.add(callback)


def unsubscribe(callback: typing.Callable[[dict], None] | None) -> None:
    """Unregister *callback*.

    Safe to call with a callback that was never registered (no-op).
    Safe to call with ``None`` (no-op).  Safe to call from any thread.
    """
    if callback is None:
        return
    with _lock:
        _subscribers.discard(callback)


def publish(event: dict) -> bool:
    """Broadcast *event* to every subscriber, synchronously.

    Returns
    -------
    bool
        ``True`` if at least one subscriber accepted the event
        (returned without raising).  ``False`` if there are no
        subscribers or every subscriber raised.

    Notes
    -----
    - Synchronous: subscribers are called in the publisher's thread.
      This preserves the previous ``_push_event_now`` semantics
      (existing tests assert that the callable was invoked by the
      time ``publish`` returns).
    - Exception isolation: a subscriber that raises is logged at
      DEBUG level and skipped.  Other subscribers still receive
      the event.  See ``TestSubscriberExceptionIsolation``.
    - The subscriber list is snapshotted under the lock before
      iteration, so ``unsubscribe`` from within a subscriber
      callback does not raise ``RuntimeError: Set changed size
      during iteration`` and the unsubscribed callback will not be
      re-invoked on subsequent publishes.
    """
    with _lock:
        fns = list(_subscribers)
    if not fns:
        return False
    delivered = False
    for fn in fns:
        try:
            fn(event)
            delivered = True
        except Exception:
            log.debug("[event_bus] subscriber raised", exc_info=True)
    return delivered


def _subscriber_count() -> int:
    """Return the current number of subscribers (for tests/diagnostics).

    Not part of the public API; exposed for assertions in
    ``tests/test_event_bus.py`` and for the backward-compat shims
    in ``ipc_server.py``.
    """
    with _lock:
        return len(_subscribers)
