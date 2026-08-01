"""Per-subsystem teardown helpers extracted out of :class:`ShutdownController`.

Phase 4.5 (OI-36) — each ``_teardown_*`` body on :class:`ShutdownController`
was a self-contained block that accessed ``self._app`` (and, in two cases,
shared synchronization state on the controller instance). Each body now
lives in its own subsystem-named module as a free function taking the
owning ``controller`` as its first argument (mirroring the
:mod:`voice_typer.server.atexit_safety` /
:mod:`voice_typer.server.signal_handlers` extraction convention).

The :class:`ShutdownController` keeps a 1-line delegate method per helper
so:

* the parallel batch in ``_do_cleanup`` (which references
  ``self._teardown_recorder`` etc.) keeps working unchanged;
* tests that ``monkeypatch.setattr(controller, "_teardown_recorder", spy)``
  / ``controller._teardown_electron()`` keep intercepting the call (see
  ``tests/test_shutdown_parallel.py`` and
  ``tests/test_shutdown_asr_unload.py``);
* the controller's class body shrinks from ~1622 LOC to an
  orchestrator-sized ~400 LOC.

The delegate bodies are intentionally NOT moved to a generic dispatcher —
the per-method names are part of the test surface (the
``test_do_cleanup_invokes_all_teardown_helpers`` test asserts each
``_teardown_*`` name exists on the controller).
"""

from __future__ import annotations

__all__: list[str] = []
