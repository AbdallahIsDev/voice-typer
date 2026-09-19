"""UE-29 regression tests for the ``_handle_connection_inner`` refactor."""

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


class TestHelpersExist:
    """UE-29: the five extracted helpers must exist as module-level"""

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


class TestOrchestratorIsShort:
    """UE-29: ``_handle_connection_inner`` must be a short coordinator"""

    def test_orchestrator_invokes_all_five_helpers(self) -> None:
        """The orchestrator body must reference all five extracted"""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        for helper in [
            "_check_duplicate_auth",
            "_emit_ready_if_first",
            "_install_subscriber",
            "_start_writer",
            "_read_loop",
        ]:
            assert helper in src, f"UE-29: _handle_connection_inner must delegate to {helper}"

    def test_orchestrator_is_under_120_lines(self) -> None:
        """The orchestrator (including docstring) must remain well under"""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        line_count = len(src.splitlines())
        assert line_count < 120, (
            f"UE-29: _handle_connection_inner must remain a short coordinator "
            f"(<120 lines including docstring); got {line_count} lines. "
            f"The original monolith was ~375 lines."
        )

    def test_orchestrator_does_not_contain_inline_read_loop(self) -> None:
        """The orchestrator must NOT contain the inline ``async for raw"""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        assert "async for raw in websocket:" not in src, (
            "UE-29: _handle_connection_inner must NOT contain the inline read loop, extract to _read_loop"
        )

    def test_orchestrator_does_not_define_inner_push_to_ws(self) -> None:
        """The orchestrator must NOT define ``_push_to_ws`` as an inner"""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        assert "def _push_to_ws" not in src, (
            "UE-29: _handle_connection_inner must NOT define _push_to_ws inline, extract to _install_subscriber"
        )

    def test_orchestrator_does_not_define_inner_writer(self) -> None:
        """The orchestrator must NOT define ``_writer`` as an inner"""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        assert "async def _writer" not in src, (
            "UE-29: _handle_connection_inner must NOT define _writer inline, extract to _start_writer"
        )


class TestHelperSignatures:
    """UE-29: the extracted helpers must have the expected signatures"""

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


class TestLifecycleOwnership:
    """UE-29: the orchestrator must STILL own the connection-lifecycle"""

    def test_orchestrator_has_finally_block(self) -> None:
        """The orchestrator must have a ``finally:`` block that runs"""
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
        """The finally block must clear ``server._active_ws_connection``"""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        assert "_active_ws_connection" in src, (
            "UE-29: orchestrator must clear _active_ws_connection in the finally block"
        )

    def test_orchestrator_handles_connection_closed_ok(self) -> None:
        """The orchestrator must catch ``ConnectionClosedOK`` (clean"""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        assert "except ConnectionClosedOK:" in src, (
            "UE-29: orchestrator must catch ConnectionClosedOK for clean-close logging"
        )

    def test_orchestrator_handles_connection_closed_error(self) -> None:
        """The orchestrator must catch ``ConnectionClosedError``"""
        src = inspect.getsource(sidecar_ws._handle_connection_inner)
        assert "except ConnectionClosedError" in src, (
            "UE-29: orchestrator must catch ConnectionClosedError for abnormal-close logging"
        )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-o", "addopts="])
