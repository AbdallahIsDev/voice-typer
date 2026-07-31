"""UE-29 regression tests for the ``_handle_connection_inner`` refactor.

The finding ``UE-29`` called ``_handle_connection_inner`` a 375-line
monolith (spaghetti). The fix extracts five named helpers and turns
the orchestrator into a ~30-line coordinator:

  * :func:`sidecar_ws._check_duplicate_auth` — XZ-R18-06 single-
    connection invariant.
  * :func:`sidecar_ws._emit_ready_if_first` — ADR-0020 round-2 ready
    event on first authenticated connection.
  * :func:`sidecar_ws._install_subscriber` — event_bus subscriber
    registration + initial ``state_changed`` snapshot.
  * :func:`sidecar_ws._start_writer` — per-connection writer task.
  * :func:`sidecar_ws._read_loop` — read/dispatch loop body.

These tests are purely structural (source inspection) — they assert
the helpers exist as module-level callables, the orchestrator is
short, and the helpers are actually invoked from the orchestrator.
They do NOT exercise the runtime behavior (that's covered by the
existing ``tests/test_sidecar_ws_auth_failed.py`` /
``tests/test_sidecar_ws_protocol_version.py`` /
``tests/test_sidecar_ws_xz_ipc_003.py`` suites, which all pass on
the refactored module).
"""

from __future__ import annotations

import inspect
import os

from voice_typer.server import sidecar_ws

_SIDECAR_WS_PATH = os.path.join(
    os.path.dirname(sidecar_ws.__file__),
    "sidecar_ws.py",
)


def _src() -> str:
    with open(_SIDECAR_WS_PATH, encoding="utf-8") as f:
        return f.read()


# ── Helpers exist as module-level callables ──────────────────────────


class TestUE29HelpersExist:
    """UE-29: the five extracted helpers must exist as module-level
    callables on :mod:`voice_typer.server.sidecar_ws`."""

    def test_check_duplicate_auth_exists(self) -> None:
        assert hasattr(sidecar_ws, "_check_duplicate_auth"), "UE-29: sidecar_ws._check_duplicate_auth helper must exist"
        assert callable(sidecar_ws._check_duplicate_auth)

    def test_emit_ready_if_first_exists(self) -> None:
        assert hasattr(sidecar_ws, "_emit_ready_if_first"), "UE-29: sidecar_ws._emit_ready_if_first helper must exist"
        assert callable(sidecar_ws._emit_ready_if_first)

    def test_install_subscriber_exists(self) -> None:
        assert hasattr(sidecar_ws, "_install_subscriber"), "UE-29: sidecar_ws._install_subscriber helper must exist"
        assert callable(sidecar_ws._install_subscriber)

    def test_start_writer_exists(self) -> None:
        assert hasattr(sidecar_ws, "_start_writer"), "UE-29: sidecar_ws._start_writer helper must exist"
        assert callable(sidecar_ws._start_writer)

    def test_read_loop_exists(self) -> None:
        assert hasattr(sidecar_ws, "_read_loop"), "UE-29: sidecar_ws._read_loop helper must exist"
        assert callable(sidecar_ws._read_loop)


# ── Orchestrator is a short coordinator ──────────────────────────────


class TestUE29OrchestratorIsShort:
    """UE-29: ``_handle_connection_inner`` must be a short coordinator
    (~30 lines) that delegates to the extracted helpers — NOT the
    original ~375-line monolith."""

    def test_orchestrator_invokes_all_five_helpers(self) -> None:
        """The orchestrator body must reference all five extracted
        helpers by name."""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        for helper in [
            "_check_duplicate_auth",
            "_emit_ready_if_first",
            "_install_subscriber",
            "_start_writer",
            "_read_loop",
        ]:
            assert helper in src, f"UE-29: _handle_connection_inner must delegate to {helper}"

    def test_orchestrator_is_under_80_lines(self) -> None:
        """The orchestrator (including docstring) must be well under
        the original 375 lines. The threshold is generous (80) to
        accommodate the docstring + the connection-lifecycle
        try/except/finally; the actual coordinator body is ~30 lines."""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        line_count = len(src.splitlines())
        assert line_count < 80, (
            f"UE-29: _handle_connection_inner must be a short coordinator "
            f"(<80 lines including docstring); got {line_count} lines. "
            f"The original monolith was ~375 lines."
        )

    def test_orchestrator_does_not_contain_inline_read_loop(self) -> None:
        """The orchestrator must NOT contain the inline ``async for raw
        in websocket:`` read loop — that belongs in ``_read_loop``."""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        assert "async for raw in websocket:" not in src, (
            "UE-29: _handle_connection_inner must NOT contain the inline read loop — extract to _read_loop"
        )

    def test_orchestrator_does_not_define_inner_push_to_ws(self) -> None:
        """The orchestrator must NOT define ``_push_to_ws`` as an inner
        closure — that belongs in ``_install_subscriber``."""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        assert "def _push_to_ws" not in src, (
            "UE-29: _handle_connection_inner must NOT define _push_to_ws inline — extract to _install_subscriber"
        )

    def test_orchestrator_does_not_define_inner_writer(self) -> None:
        """The orchestrator must NOT define ``_writer`` as an inner
        closure — that belongs in ``_start_writer``."""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        assert "async def _writer" not in src, (
            "UE-29: _handle_connection_inner must NOT define _writer inline — extract to _start_writer"
        )


# ── Helpers have the right signatures ────────────────────────────────


class TestUE29HelperSignatures:
    """UE-29: the extracted helpers must have the expected signatures
    so the orchestrator can delegate cleanly."""

    def test_check_duplicate_auth_signature(self) -> None:
        sig = inspect.signature(sidecar_ws._check_duplicate_auth)
        params = list(sig.parameters.keys())
        assert params == ["websocket", "server", "peer"], (
            f"UE-29: _check_duplicate_auth must take (websocket, server, peer); got {params}"
        )

    def test_emit_ready_if_first_signature(self) -> None:
        sig = inspect.signature(sidecar_ws._emit_ready_if_first)
        params = list(sig.parameters.keys())
        assert params == ["server"], f"UE-29: _emit_ready_if_first must take (server,); got {params}"

    def test_install_subscriber_signature(self) -> None:
        sig = inspect.signature(sidecar_ws._install_subscriber)
        params = list(sig.parameters.keys())
        assert params == ["server", "loop", "outbound"], (
            f"UE-29: _install_subscriber must take (server, loop, outbound); got {params}"
        )

    def test_start_writer_signature(self) -> None:
        sig = inspect.signature(sidecar_ws._start_writer)
        params = list(sig.parameters.keys())
        assert params == ["websocket", "outbound"], (
            f"UE-29: _start_writer must take (websocket, outbound); got {params}"
        )

    def test_read_loop_signature(self) -> None:
        sig = inspect.signature(sidecar_ws._read_loop)
        params = list(sig.parameters.keys())
        assert params == ["websocket", "server", "dispatch"], (
            f"UE-29: _read_loop must take (websocket, server, dispatch); got {params}"
        )


# ── Behavior preserved: orchestrator still owns lifecycle ───────────


class TestUE29LifecycleOwnership:
    """UE-29: the orchestrator must STILL own the connection-lifecycle
    ``try/except/finally`` — the helpers don't clean up after
    themselves; the orchestrator guarantees subscriber unsubscribe +
    writer-task cancel + active-connection slot clear on EVERY exit
    path (clean close, abnormal close, unexpected exception)."""

    def test_orchestrator_has_finally_block(self) -> None:
        """The orchestrator must have a ``finally:`` block that runs
        cleanup regardless of how the read loop exited."""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        assert "finally:" in src, (
            "UE-29: _handle_connection_inner must retain the finally block "
            "that unsubscribes the event_bus subscriber + cancels the writer "
            "task + clears the active-connection slot"
        )

    def test_orchestrator_unsubscribes_push_to_ws(self) -> None:
        """The finally block must call ``event_bus.unsubscribe(_push_to_ws)``."""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        assert "event_bus.unsubscribe(_push_to_ws)" in src, (
            "UE-29: orchestrator must unsubscribe _push_to_ws in the finally block"
        )

    def test_orchestrator_cancels_writer_task(self) -> None:
        """The finally block must cancel the writer task."""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        assert "writer_task.cancel()" in src, "UE-29: orchestrator must cancel writer_task in the finally block"

    def test_orchestrator_clears_active_connection_slot(self) -> None:
        """The finally block must clear ``server._active_ws_connection``
        under ``server._lock`` (XZ-R18-06 compare-and-clear)."""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        assert "_active_ws_connection" in src, (
            "UE-29: orchestrator must clear _active_ws_connection in the finally block"
        )

    def test_orchestrator_handles_connection_closed_ok(self) -> None:
        """The orchestrator must catch ``ConnectionClosedOK`` (clean
        WebSocket close) and log at DEBUG — NOT propagate."""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        assert "except ConnectionClosedOK:" in src, (
            "UE-29: orchestrator must catch ConnectionClosedOK for clean-close logging"
        )

    def test_orchestrator_handles_connection_closed_error(self) -> None:
        """The orchestrator must catch ``ConnectionClosedError``
        (abnormal close) and log at DEBUG."""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        assert "except ConnectionClosedError" in src, (
            "UE-29: orchestrator must catch ConnectionClosedError for abnormal-close logging"
        )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-o", "addopts="])
