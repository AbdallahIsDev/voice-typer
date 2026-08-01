"""shared IPC server helpers split out of ``ipc_server.py``.

This module exists to break the ``sys.modules`` registration hack that
``ipc_server.py`` historically used to survive being loaded as
``__main__`` (``python -m voice_typer.server.ipc_server``).  When the
file is loaded as ``__main__``, Python registers it under the name
``"__main__"`` only — NOT under its canonical dotted name.  Lazy
imports elsewhere (``providers.py``, ``sidecar_ws.py``, ``app.py``,
``__main__.py``) that do ``from voice_typer.server.ipc_server import X``
would, without the hack, trigger a FRESH import of ``ipc_server.py``
under the canonical name, producing a duplicate ``IPCServer`` class
and a duplicate ``main`` function.  The old hack registered
``sys.modules[_CANONICAL] = sys.modules["__main__"]`` so those lazy
imports found the already-loaded module.

The clean fix is to move the module-level helpers that are referenced
by both ``IPCServer`` method bodies AND by external callers into a
leaf submodule (this file) that is ALWAYS loaded under its canonical
name.  ``ipc_server.py`` then ``from voice_typer.server.ipc._helpers
import ...`` re-exports them so existing
``from voice_typer.server.ipc_server import X`` call sites keep
working unchanged.

What lives here (and why):

- ``log`` — the IPC server's logger.  ``logging.getLogger`` returns a
  process-wide singleton by name, so importing the same object from
  ``_helpers`` vs. defining it inline in ``ipc_server.py`` is
  observably identical.  Moving it here means a duplicate-load of
  ``ipc_server.py`` (one as ``__main__``, one as canonical) does NOT
  create two logger objects — both modules bind the same singleton.

- ``_READONLY_COMMANDS`` — re-exported from
  :mod:`voice_typer.server.ipc.registry` (the canonical source).
  Previously defined inline here as a legacy duplicate; the duplicate
  was deleted by the registry unification so the two locations cannot silently
  drift. The name is re-exported so existing
  ``from voice_typer.server.ipc._helpers import _READONLY_COMMANDS``
  callers keep working unchanged; both ``ipc_server.py`` and
  ``ipc._helpers`` reference the SAME frozenset object.

- ``_push_event_now`` — thin shim over :func:`event_bus.publish`.
  Domain modules and tests historically import this from
  ``ipc_server``; moving the definition here preserves the public
  import path while ensuring both load modes share the same function
  object (and therefore the same closure over ``event_bus``).

What stays in ``ipc_server.py`` (and why):

- ``IPCServer`` class — the 1700-line mixin composition.  Moving it
  here would be a much larger refactor and would break the
  :mod:`tests.test_dead_code_stays_removed` invariant that
  ``IPCServer`` is NOT re-exported from the ``ipc`` package.  The
  duplicate-load concern is mitigated by the fact that the canonical
  ``IPCServer`` (instantiated via :func:`providers.build_ipc_server`)
  is the only one ever instantiated — the ``__main__``-mode copy is
  dead code whose class object is never used.

- ``main`` / ``parse_ipc_args`` / ``_set_process_metadata`` — the
  process entry-point shims.  Same rationale: only one is ever
  invoked (the ``__main__``-mode ``main`` is the entry point; the
  canonical ``main`` is the import-target for ``app.main`` /
  ``__main__.py``).

- ``_get_rate_limiter`` — MUST stay in ``ipc_server.py`` because
  :mod:`tests.test_r4_f18_rate_limiter_concurrent_init` monkey-patches
  ``ipc_server._RateLimiter`` and relies on the function looking up
  ``_RateLimiter`` from ``ipc_server``'s module globals at call time.
  Moving it here would silently break that test contract.
"""

from __future__ import annotations

import logging

from voice_typer.server import event_bus

# Canonical source for ``_READONLY_COMMANDS`` is the leaf
# :mod:`voice_typer.server.ipc.registry` submodule (the
# ``ipc_server.py`` god-module used to host its own copy and this
# module had a parallel legacy duplicate; both were collapsed onto
# the registry by the registry unification so the two cannot silently drift).
# The name is re-exported here so existing
# ``from voice_typer.server.ipc._helpers import _READONLY_COMMANDS``
# callers keep working unchanged; the imported object is the SAME
# frozenset that ``ipc_server.py`` re-exports from the same source.
from voice_typer.server.ipc.registry import _READONLY_COMMANDS  # noqa: F401

# The IPC server's process-wide logger.  ``logging.getLogger`` returns
# a singleton by name, so importing this object from ``_helpers`` yields
# the same logger that ``ipc_server.py``'s inline definition produced.
# Tests that do ``patch.object(ipc_server.log, "error")`` are patching
# the METHOD on the logger OBJECT — both ``ipc_server.log`` and
# ``_helpers.log`` reference the same object, so the patch is observed
# regardless of which alias the caller used.
log = logging.getLogger("voice_typer.server.ipc_server")


def _push_event_now(msg: dict) -> bool:
    """Push a raw event to ALL active IPC servers, if any are wired.

        Thin shim over ``event_bus.publish``.  Domain code should
        call ``event_bus.publish`` directly; this function is preserved
        so existing lazy imports (``from voice_typer.server.ipc_server
    import _push_event_now``) continue to work after the
        refactor that moved its definition into ``ipc._helpers``.

        Returns True if at least one server accepted the event, False if
        no server is active.  Safe to call from any thread; never raises.

    previously pushed to a single global callable.  When
        two IPCServer instances existed in the same process (tests +
        production), the second start() would stomp the first's push fn,
        and the first's stop() would clear the global entirely — leaving
        the second server unable to push.  We now fan out to ALL servers
        in the registry so both receive the event.
    """
    return event_bus.publish(msg)


__all__ = [
    "_READONLY_COMMANDS",
    "_push_event_now",
    "log",
]
