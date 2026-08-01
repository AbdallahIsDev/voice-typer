"""Shutdown subsystem package.

Houses the helpers extracted out of :mod:`voice_typer.server.shutdown_controller`
so the controller can focus on orchestration. The controller retains thin
delegate methods (one per ``_teardown_*`` helper) so existing call sites,
test spies, and the ``_do_cleanup`` parallel batch wiring keep working
unchanged.

Submodules
----------
* :mod:`.teardowns` — per-subsystem teardown helpers
  (each takes the owning :class:`ShutdownController` as its first arg).
"""

from __future__ import annotations

__all__: list[str] = []
