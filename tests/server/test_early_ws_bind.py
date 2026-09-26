"""Early server-started (bind-before-build) launch-order contracts."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest
import voice_typer.server.app  # noqa: F401  (force-import for patch targets)
from voice_typer.server import sidecar_ws
from voice_typer.server.ipc import entrypoint
from voice_typer.server.ipc.validation import ErrorCodes


class TestEarlyServerStartedFlag:
    """``VT_EARLY_SERVER_STARTED`` selects the new launch order."""

    def test_unset_defaults_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VT_EARLY_SERVER_STARTED", raising=False)
        assert entrypoint._early_server_started_enabled() is False

    def test_zero_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VT_EARLY_SERVER_STARTED", "0")
        assert entrypoint._early_server_started_enabled() is False

    def test_one_is_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VT_EARLY_SERVER_STARTED", "1")
        assert entrypoint._early_server_started_enabled() is True

    def test_true_case_insensitive_is_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VT_EARLY_SERVER_STARTED", "TRUE")
        assert entrypoint._early_server_started_enabled() is True

    def test_junk_values_are_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for value in ("false", "yes?", "", "off", "2"):
            monkeypatch.setenv("VT_EARLY_SERVER_STARTED", value)
            assert entrypoint._early_server_started_enabled() is False, (
                f"unexpected value {value!r} must keep the default (build-then-serve) order"
            )


class _FakeApp:
    """Records construction/start; optionally blocks mid-construction."""

    def __init__(self, events: list, gate: threading.Event | None = None) -> None:
        self._events = events
        events.append("construct:done")
        if gate is not None:
            events.append("construct:waiting")
            gate.wait(timeout=15)
            events.append("construct:released")

    def start(self) -> None:
        self._events.append("app.start")


class _FakeService:
    def __init__(self, app) -> None:
        self.app = app


class _FakeServer:
    """Minimal stand-in for the IPCServer surface the entrypoint uses."""

    def __init__(self, app_arg) -> None:
        self.built_with = app_arg
        self.app = None
        self.service = None
        self.started = False
        self.tcp_port = None
        self.pushed: list[dict] = []

    def start(self) -> None:
        self.started = True

    def start_tcp(self, port) -> None:
        self.tcp_port = port

    def push(self, msg: dict) -> None:
        self.pushed.append(msg)


class TestWsStartupThreadEarlyBind:
    """The early ws-startup thread constructs the app, late-binds it into"""

    def test_constructs_binds_starts_server_flushes_then_app_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events: list[str] = []
        app_holder: list[object] = []

        def _fake_app() -> _FakeApp:
            app = _FakeApp(events)
            app_holder.append(app)
            return app

        monkeypatch.setattr("voice_typer.server.app.LausuApp", _fake_app)
        monkeypatch.setattr(
            "voice_typer.server.service.LausuService",
            lambda app: _FakeService(app),
        )

        flush_calls: list[object] = []
        monkeypatch.setattr(
            sidecar_ws,
            "flush_early_dispatch_buffer",
            lambda server: flush_calls.append(server),
        )

        server = _FakeServer(app_arg=None)
        thread = threading.Thread(
            target=entrypoint._ws_startup_thread_main_early,
            args=(server, None),
            name="ws-sidecar-startup-test",
            daemon=True,
        )
        thread.start()
        thread.join(timeout=15)
        assert not thread.is_alive(), "the early startup thread must complete"

        app = app_holder[0]
        assert server.app is app, "the thread must late-bind the constructed app into the server"
        assert isinstance(server.service, _FakeService) and server.service.app is app, (
            "the service must be rebuilt over the REAL app (the deferred None-service was never used)"
        )
        assert server.started is True, "server.start() must run on the thread AFTER the app exists"
        assert server._early_bind_app_ready is True, "the ready flag must be raised before the buffer is flushed"
        assert flush_calls == [server], "the buffered-frame flush must be requested exactly once"
        assert events == ["construct:done", "app.start"], (
            f"app.start() must run through the existing fail-fast wrapper last; got {events}"
        )

    def test_construction_failure_exits_process_with_crash_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """write the SAME construction diagnostic the main-path failure"""
        from voice_typer.__main__ import EXIT_CRASH

        exit_calls: list[int] = []
        diag_calls: list[str] = []
        monkeypatch.setattr(os, "_exit", lambda code: exit_calls.append(code))
        monkeypatch.setattr(
            "voice_typer.server.ipc_diagnostics.write_startup_diagnostic",
            lambda phase, exc=None: diag_calls.append(phase),
        )

        def _boom() -> None:
            raise RuntimeError("simulated early construction failure")

        monkeypatch.setattr("voice_typer.server.app.LausuApp", _boom)

        server = _FakeServer(app_arg=None)
        thread = threading.Thread(
            target=entrypoint._ws_startup_thread_main_early,
            args=(server, None),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=15)

        assert exit_calls == [EXIT_CRASH], (
            f"an early construction failure must force-exit with EXIT_CRASH; got {exit_calls!r}"
        )
        assert diag_calls == ["construction"], (
            "the early construction-failure path must route through the shared "
            "diagnostic helper with the same 'construction' phase label as the main path"
        )

    def test_system_exit_during_construction_also_terminates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """fail-fast semantics of the existing app.start() wrapper."""
        from voice_typer.__main__ import EXIT_CRASH

        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", lambda code: exit_calls.append(code))
        monkeypatch.setattr(
            "voice_typer.server.ipc_diagnostics.write_startup_diagnostic",
            lambda phase, exc=None: None,
        )

        def _system_exit() -> None:
            raise SystemExit(0)

        monkeypatch.setattr("voice_typer.server.app.LausuApp", _system_exit)

        server = _FakeServer(app_arg=None)
        thread = threading.Thread(
            target=entrypoint._ws_startup_thread_main_early,
            args=(server, None),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=15)

        assert exit_calls == [EXIT_CRASH], (
            "SystemExit escaping construction on the daemon thread must still "
            "force-exit the process, never strand a WS-alive-but-empty backend"
        )


class TestMainEarlyWsBind:
    """``main()`` with the flag ON (ws mode): the WS transport is entered"""

    @staticmethod
    def _patch_common(monkeypatch: pytest.MonkeyPatch, events: list) -> None:
        monkeypatch.setattr("voice_typer.server.logging_setup._setup_logging", lambda: None)
        monkeypatch.setattr(
            "voice_typer.server.single_instance._ensure_single_instance",
            lambda silent=False: None,
        )
        monkeypatch.setattr("voice_typer.server.ipc_server._set_process_metadata", lambda: None)
        import faulthandler

        monkeypatch.setattr(faulthandler, "enable", lambda: None)
        monkeypatch.setattr(faulthandler, "dump_traceback_later", lambda **kw: None)
        monkeypatch.setattr(
            "voice_typer.server.service.LausuService",
            lambda app: _FakeService(app),
        )
        _ = events  # (kept for symmetry; heavy deps stubbed above)

    def test_ws_transport_entered_before_construction_completes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events: list[str] = []
        self._patch_common(monkeypatch, events)

        construct_gate = threading.Event()
        servers: list[_FakeServer] = []
        run_server_snapshots: list[dict] = []

        class _GatedApp:
            def __init__(self) -> None:
                events.append("construct:started")
                construct_gate.wait(timeout=15)

            def start(self) -> None:
                events.append("app.start")

        monkeypatch.setattr("voice_typer.server.app.LausuApp", _GatedApp)

        class _RecordingServer(_FakeServer):
            def __init__(self, app_arg) -> None:
                super().__init__(app_arg)
                servers.append(self)

        monkeypatch.setattr("voice_typer.server.providers.build_ipc_server", _RecordingServer)

        def _fake_run(server) -> int:
            run_server_snapshots.append(
                {
                    "app_is_none": server.app is None,
                    "early_flag": getattr(server, "_early_ws_bind", False),
                    "tcp_mode": server.__dict__.get("_tcp_mode", False),
                }
            )
            events.append("run:entered")
            return 0

        monkeypatch.setattr(sidecar_ws, "run", _fake_run)

        monkeypatch.setenv("VT_EARLY_SERVER_STARTED", "1")
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--ws"])
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        try:
            with pytest.raises(SystemExit) as exc_info:
                entrypoint.main()
            assert exc_info.value.code == 0
        finally:
            os.environ.pop("TAURI_SIDECAR", None)

        # The core bind-before-build contract: the WS transport (bind +
        assert events[-1] == "run:entered", (
            f"sidecar_ws.run must be entered before construction completes; events: {events}"
        )
        assert "construct:started" in events, "the ws-startup thread must have begun construction"
        snapshot = run_server_snapshots[0]
        assert snapshot["app_is_none"] is True, (
            "at run() entry the server's app must still be the deferred placeholder (None)"
        )
        assert snapshot["early_flag"] is True, (
            "the entrypoint must mark the server with _early_ws_bind so sidecar_ws.run "
            "installs the bounded pre-app dispatch buffer"
        )
        assert snapshot["tcp_mode"] is True, "the stdin listener gate must be armed at build time"

        # Release the gated construction and let the thread finish.
        construct_gate.set()
        for _ in range(200):
            if "app.start" in events:
                break
            time.sleep(0.05)
        assert "app.start" in events, "the ws-startup thread must run app.start() after bind"
        server = servers[0]
        assert server.app is not None, "the thread must late-bind the real app"
        assert server.started is True
        assert server._early_bind_app_ready is True

    def test_port_flag_is_rejected_even_with_early_bind_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """even when ``VT_EARLY_SERVER_STARTED`` is set (the early-bind"""
        monkeypatch.setenv("VT_EARLY_SERVER_STARTED", "1")
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--port", "9876"])
        with pytest.raises(SystemExit) as exc_info:
            from voice_typer.server.ipc import entrypoint as _ep

            _ep.parse_ipc_args()
        assert exc_info.value.code == 4


class TestMainFlagOffParity:
    """Flag OFF/unset: the entrypoint keeps the exact default order —"""

    def test_phase1_order_preserved_without_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events: list[str] = []
        TestMainEarlyWsBind._patch_common(monkeypatch, events)

        class _PlainApp:
            def __init__(self) -> None:
                events.append("construct:done")

            def start(self) -> None:
                events.append("app.start")

        monkeypatch.setattr("voice_typer.server.app.LausuApp", _PlainApp)

        class _RecordingServer(_FakeServer):
            def start(self) -> None:
                events.append("server.start")
                self.started = True

        monkeypatch.setattr(
            "voice_typer.server.providers.build_ipc_server",
            lambda app: _RecordingServer(app),
        )

        def _fake_run(server) -> int:
            events.append("run:entered")
            return 0

        monkeypatch.setattr(sidecar_ws, "run", _fake_run)

        monkeypatch.delenv("VT_EARLY_SERVER_STARTED", raising=False)
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--ws"])
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        try:
            with pytest.raises(SystemExit) as exc_info:
                entrypoint.main()
            assert exc_info.value.code == 0
        finally:
            os.environ.pop("TAURI_SIDECAR", None)

        # Default main-thread order (the ws-startup thread may interleave
        main_thread_events = [e for e in events if e in ("construct:done", "server.start", "run:entered")]
        assert main_thread_events == ["construct:done", "server.start", "run:entered"], (
            "flag OFF must keep the exact default main-thread order: construct → "
            f"server.start → sidecar_ws.run (got {main_thread_events}; full: {events})"
        )
        for _ in range(200):
            if "app.start" in events:
                break
            time.sleep(0.05)
        assert "app.start" in events


class _BufferHarness:
    """Builds the wrapped dispatch around a recording inner dispatch."""

    def __init__(self) -> None:
        self.inner_calls: list[tuple[dict, object]] = []
        self.server = SimpleNamespace(
            _early_bind_app_ready=False,
            _ws_loop=None,
        )

        async def _inner(msg: dict, websocket) -> dict:
            self.inner_calls.append((msg, websocket))
            return {"type": "ok"}

        self.inner = _inner
        self.wrapped = sidecar_ws._wrap_dispatch_for_early_bind(self.server, _inner)
        self.buffer = self.server._early_bind_dispatch_buffer
        self.websockets = [SimpleNamespace(name=f"ws{i}") for i in range(40)]


class TestEarlyBindDispatchBuffer:
    """Pre-app frames are buffered (bounded); post-bind frames pass"""

    def test_pre_ready_frame_is_buffered_not_dispatched(self) -> None:
        h = _BufferHarness()
        ws = h.websockets[0]
        msg = {"type": "get_status", "id": 7}

        result = asyncio.run(h.wrapped(msg, ws))

        assert result is None, "a buffered frame must defer its response (None → nothing sent yet)"
        assert h.inner_calls == [], "no handler may run before the app is bound"
        assert list(h.buffer) == [(msg, ws)]

    def test_post_ready_frame_passes_through(self) -> None:
        h = _BufferHarness()
        h.server._early_bind_app_ready = True
        msg = {"type": "get_status", "id": 1}
        ws = h.websockets[0]

        result = asyncio.run(h.wrapped(msg, ws))

        assert result == {"type": "ok"}
        assert h.inner_calls == [(msg, ws)], "a post-bind frame must reach the real dispatch closure"
        assert list(h.buffer) == [], "nothing is buffered once the app is bound"

    def test_buffer_is_bounded_with_drop_oldest_and_busy_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """immediately answered with a retryable busy error carrying its"""
        from voice_typer.server.sidecar_ws_internals import outbound as outbound_mod

        sent: list[tuple[object, dict]] = []

        async def _fake_safe_send(websocket, event: dict) -> str:
            sent.append((websocket, event))
            return "sent"

        monkeypatch.setattr(outbound_mod, "_safe_send", _fake_safe_send)

        h = _BufferHarness()
        cap = sidecar_ws._EARLY_BIND_BUFFER_CAP

        async def _fill() -> None:
            for i in range(cap + 3):
                await h.wrapped({"type": "get_status", "id": i}, h.websockets[i])

        asyncio.run(_fill())

        assert len(h.buffer) == cap, f"the buffer must stay bounded at the cap ({cap}); got {len(h.buffer)}"
        assert len(sent) == 3, "each overflowed (dropped) frame must receive exactly one busy error"
        # The three OLDEST frames (ids 0, 1, 2) were dropped + answered.
        dropped_ids = [event["id"] for _, event in sent]
        assert dropped_ids == [0, 1, 2]
        for _ws_obj, event in sent:
            assert event["type"] == "error"
            assert event["data"]["code"] == ErrorCodes.NOT_INITIALIZED
        # The remaining buffered frames are the NEWEST ones.
        remaining_ids = [msg["id"] for msg, _ in h.buffer]
        assert remaining_ids == list(range(3, cap + 3))
        assert h.inner_calls == []

    def test_flush_drains_buffer_through_read_loop_respond(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After the app binds, ``flush_early_dispatch_buffer`` marshals"""
        from voice_typer.server.sidecar_ws_internals import read_loop as read_loop_mod

        replayed: list[tuple[dict, object, object]] = []

        async def _fake_respond(msg, request_id, websocket, dispatch) -> None:
            replayed.append((msg, request_id, websocket))
            # The drain passes the WRAPPED dispatch, post-bind it must
            result = await dispatch(msg, websocket)
            assert result == {"type": "ok"}

        monkeypatch.setattr(read_loop_mod, "_dispatch_and_respond", _fake_respond)

        h = _BufferHarness()
        buffered = [
            ({"type": "get_status", "id": 11}, h.websockets[0]),
            ({"type": "get_config", "id": 12}, h.websockets[1]),
        ]

        async def _scenario() -> None:
            for msg, ws in buffered:
                await h.wrapped(msg, ws)
            # The app lands on the ws-startup thread in production; here
            h.server._early_bind_app_ready = True
            h.server._ws_loop = asyncio.get_running_loop()
            sidecar_ws.flush_early_dispatch_buffer(h.server)
            # Let the call_soon_threadsafe callback + created tasks run.
            for _ in range(10):
                await asyncio.sleep(0)
            assert list(h.buffer) == [], "the flush must drain the buffer completely"

        asyncio.run(_scenario())

        assert replayed == [
            (buffered[0][0], 11, h.websockets[0]),
            (buffered[1][0], 12, h.websockets[1]),
        ], f"buffered frames must replay in FIFO order with their ids; got {replayed}"
        assert len(h.inner_calls) == 2, "each replayed frame must reach the real dispatch"

    def test_flush_without_a_live_loop_is_a_noop(self) -> None:
        """Flush before the WS loop exists (construction finished before"""
        h = _BufferHarness()
        msg = {"type": "get_status", "id": 3}
        ws = h.websockets[0]
        asyncio.run(h.wrapped(msg, ws))
        h.server._early_bind_app_ready = True
        # No loop set on the server.
        sidecar_ws.flush_early_dispatch_buffer(h.server)
        assert list(h.buffer) == [(msg, ws)], "a loopless flush must not drop buffered frames"
        assert h.inner_calls == []


class TestRunInstallsEarlyWrapperGated:
    """``sidecar_ws.run`` must install the early-bind buffer wrapper ONLY"""

    class _StopRunError(Exception):
        pass

    def _run_with_banner_stop(self, monkeypatch, early: bool):
        from voice_typer.server.sidecar_ws_internals import (
            dispatch as dispatch_mod,
            stdout_banner as stdout_banner_mod,
        )

        inner_calls: list[object] = []

        async def _inner(msg, websocket):  # pragma: no cover - never reached
            inner_calls.append(msg)
            return {"type": "ok"}

        monkeypatch.setattr(dispatch_mod, "_make_dispatch", lambda server: _inner)

        def _raising_banner(port, protocol=None):
            raise TestRunInstallsEarlyWrapperGated._StopRunError()

        monkeypatch.setattr(stdout_banner_mod, "_emit_server_started", _raising_banner)

        server = SimpleNamespace(
            _lock=threading.RLock(),
            _shutting_down=False,
        )
        if early:
            server._early_ws_bind = True

        exit_code = sidecar_ws.run(server)
        return server, exit_code, inner_calls

    def test_run_without_marker_installs_no_wrapper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server, exit_code, _ = self._run_with_banner_stop(monkeypatch, early=False)
        assert exit_code == 1, "the banner stop propagates to run()'s generic handler"
        assert getattr(server, "_early_bind_dispatch_buffer", None) is None, (
            "without the _early_ws_bind marker, run() must NOT install the early-bind wrapper"
        )

    def test_run_with_marker_installs_wrapper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server, exit_code, _ = self._run_with_banner_stop(monkeypatch, early=True)
        assert exit_code == 1
        assert getattr(server, "_early_bind_dispatch_buffer", None) is not None, (
            "with the _early_ws_bind marker, run() must install the bounded pre-app buffer"
        )
        assert getattr(server, "_early_bind_dispatch", None) is not None
        assert sidecar_ws._EARLY_BIND_BUFFER_CAP >= 16, "the pre-app buffer cap must stay small (bounded by design)"
