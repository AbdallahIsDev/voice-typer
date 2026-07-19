# ARCH-REFAC-002 / ARCH-045: extracted from the original
# ``voice_typer/server/ipc_server.py`` god-module (Phase 4.5 split).
"""Push-event publisher (B-1: thin shim over ``event_bus``).

Module-level push hook.  Set by the active IPCServer instance when it
starts; cleared when it stops.  Using a module global (instead of
e.g. ``app._ipc_server``) means listeners from any module can push
events without needing a reference to the app or the server, and
without closure-capture surprises when multiple VoiceTyperApp
instances exist in the same process (tests, restarts, etc.).

NEW-IPC-013: this used to be a single Optional[Callable].  When two
IPCServer instances existed in the same process (e.g. a test fixture
plus the production server), the second start() would stomp the
first server's push fn, and the first server's stop() would clear
the global — leaving the second server unable to push events.  We
now keep a registry (set) of push functions; _push_event_now fans
out to ALL registered servers.  Each IPCServer registers on start
and unregisters on stop, so the registry stays consistent across
any number of concurrent instances.

B-1: the registry and helpers below are now THIN SHIMS over
``voice_typer.server.event_bus``.  Domain modules should call
``event_bus.publish(event)`` directly; the names here are kept so
existing lazy imports (``from voice_typer.server.ipc_server import
directly (``ipc_server._push_event_now``) and tests that manipulate the
registry set directly (``event_bus._subscribers.clear()``) continue to
work.  The shims reference the SAME underlying set and lock objects
as ``event_bus._subscribers`` / ``event_bus._lock`` so manipulating
one affects the other.
B-1 FIX-12: the _push_event_registry/_push_event_registry_lock aliases and
_set_push_event/_clear_push_event shims have been removed.  Domain code and
tests now call ``event_bus.subscribe`` / ``event_bus.unsubscribe`` directly.
"""

from voice_typer.server import event_bus


def _push_event_now(msg: dict) -> bool:
    """Push a raw event to ALL active IPC servers, if any are wired.

    B-1: thin shim over ``event_bus.publish``.  Domain code should
    call ``event_bus.publish`` directly; this function is preserved
    so existing lazy imports continue to work.

    Returns True if at least one server accepted the event, False if
    no server is active.  Safe to call from any thread; never raises.

    NEW-IPC-013: previously pushed to a single global callable.  When
    two IPCServer instances existed in the same process (tests +
    production), the second start() would stomp the first's push fn,
    and the first's stop() would clear the global entirely — leaving
    the second server unable to push.  We now fan out to ALL servers
    in the registry so both receive the event.
    """
    return event_bus.publish(msg)


__all__ = ["_push_event_now"]
