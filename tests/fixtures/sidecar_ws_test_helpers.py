"""Shared fake-server factory for the sidecar WS test suite.

This module owns the SINGLE canonical ``_make_fake_server`` helper used
by every test file that exercises :mod:`voice_typer.server.sidecar_ws`.
Before this module existed, six inline copies of the helper were
sprinkled across the test tree (``tests/tauri/test_sidecar_ws_unit.py``,
``tests/tauri/mig15/test_ws_hmac_windows.py``,
``tests/tauri/mig16/test_ws_hmac_macos.py``,
``tests/tauri/mig17/test_ws_hmac_linux.py``,
``tests/test_ipc_error_envelope_parity.py``, and
``tests/test_sidecar_ws_thread_safety.py``). Three of those copies had
diverged from the canonical shape and were missing the
``_ws_dispatch_pool = None`` fix — a stale copy that re-introduces the
``wrap_future`` assertion failure anytime a new
test copies the wrong helper.

Centralising the factory here means future additions to
:func:`sidecar_ws._make_dispatch` (e.g. a new ``getattr(server, "_ws_*",
None) is None`` lazy-create branch) only need to update ONE place —
this module — and every sidecar WS test picks up the fix
automatically.

The helper is intentionally a superset of every prior inline copy: it
sets every attribute any of the six call sites ever needed, so each
test file can drop its inline definition and ``import`` this one
without further per-test configuration. Tests that need a different
``_dispatch`` return value (e.g. ``side_effect=RuntimeError("boom")``)
override it after calling ``_make_fake_server()``.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_fake_server() -> MagicMock:
    """Build a fake IPCServer with the attributes _make_dispatch / _handle_connection need.

    ``_make_dispatch`` now uses
    ``loop.run_in_executor(server._ws_dispatch_pool, ...)`` (G4-H-30 —
    dedicated thread pool for WS dispatch, separate from the default
    executor). A MagicMock attribute access auto-vivifies a child
    MagicMock, so ``getattr(server, "_ws_dispatch_pool", None)`` returns
    a non-None MagicMock — the lazy-create branch in ``_make_dispatch``
    is skipped, and the MagicMock is passed to
    ``loop.run_in_executor``. ``asyncio.futures.wrap_future`` then
    asserts the submit() return is a real ``concurrent.futures.Future``
    and fails on the MagicMock.

    The same auto-vivification trap also affects the three sibling
    lazy-create attrs introduced by the WS-dispatch drain work
    (``_ws_inflight_count``, ``_ws_inflight_lock``, ``_ws_drained_event``):
    ``getattr(server, "_ws_inflight_count", None) is None`` returns
    ``False`` on a MagicMock (the auto-vivified child mock is not
    ``None``), so the lazy-create branch is skipped and the child mock
    is later compared with ``<= 0`` — a ``TypeError``. All four attrs
    are explicitly set to ``None`` here so the lazy-create branches in
    ``_make_dispatch`` run and install real ``ThreadPoolExecutor`` /
    ``threading.Lock`` / ``threading.Event`` / ``int`` instances.

    Fix: explicitly set ``server._ws_dispatch_pool = None`` (and the
    other three lazy-create attrs) so the lazy-create branches run and
    create real concurrency primitives. The executor / lock / event /
    counter are shared across calls on the same server (so cleanup is
    the test's responsibility — we let them leak at process exit, which
    is fine for a unit test).

    Tests that exercise ``_handle_connection`` (not just
    ``_make_dispatch``) also need:

    - ``_ready_emitted = True`` — skips the post-auth ``ready`` event
      emission so the spied ``event_bus.publish`` doesn't see a stray
      ``ready`` from setup (and so the writer task doesn't try to
      serialise one before the test's own publish burst begins).
    - ``server.app.tray._state = None`` — skips the initial
      ``state_changed`` emission in ``_install_subscriber``. Without
      this, ``getattr(server.app.tray, "_state", None)`` returns an
      auto-vivified MagicMock (truthy), the ``state_changed`` event is
      published with a MagicMock ``status`` value, and the writer
      task's ``json.dumps(event)`` blows up with
      ``TypeError: Object of type MagicMock is not JSON serializable``.
    - ``server.push = MagicMock()`` — defensive: matches the original
      ``tests/test_sidecar_ws_thread_safety.py`` helper. ``server.push``
      is not currently called by ``sidecar_ws`` (the WS writer
      subscribes to ``event_bus`` instead), but pre-creating it keeps
      the mock's call log clean if a future refactor adds a
      ``server.push`` call site.
    """
    server = MagicMock()
    server._dispatch = MagicMock(return_value={"type": "result", "data": {"ok": True}})
    server.app = MagicMock()
    server.app.quit = MagicMock()
    # force the lazy-create branches in
    # ``_make_dispatch`` to run (they create a real ThreadPoolExecutor,
    # threading.Lock, threading.Event, and int counter). If we leave
    # any of these unset, MagicMock auto-vivifies a child mock that
    # either fails the ``wrap_future`` isinstance assertion
    # (``_ws_dispatch_pool``) or the ``<= 0`` comparison
    # (``_ws_inflight_count``).
    server._ws_dispatch_pool = None
    server._ws_drained_event = None
    server._ws_inflight_lock = None
    server._ws_inflight_count = None
    # ``server.app._shutting_down`` is checked by the cooperative
    # shutdown gate in ``_make_dispatch`` BEFORE the rate-limit
    # check. On a MagicMock, ``getattr(server.app, "_shutting_down",
    # False)`` returns an auto-vivified child mock (truthy, NOT
    # ``is True``) and the gate fires — every dispatch returns
    # ``server.shutting_down`` instead of reaching the rate-limit /
    # handler path. Pin it to ``False`` so the dispatch body runs
    # (and the rate-limit / readonly / state-mutating branches
    # exercise as designed).
    server.app._shutting_down = False
    # For tests that exercise ``_handle_connection`` (not just
    # ``_make_dispatch``): skip the post-auth ``ready`` emission and
    # the initial ``state_changed`` emission (the latter would
    # otherwise publish a MagicMock value that fails JSON
    # serialization in the writer task).
    server.push = MagicMock()
    server._ready_emitted = True
    server.app.tray._state = None
    server.app.tray._message = ""
    return server


__all__ = ["_make_fake_server"]
