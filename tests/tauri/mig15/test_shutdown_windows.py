"""cooperative shutdown validation."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
# Don't assert the literal repo-dir name, the repo may be cloned under any
assert (_REPO_ROOT / "pyproject.toml").is_file(), (
    f"_REPO_ROOT does not look like the lausu project root (no pyproject.toml found): {_REPO_ROOT}"
)
assert (_REPO_ROOT / "src-tauri" / "Cargo.toml").is_file(), (
    f"_REPO_ROOT does not look like the lausu project root (no src-tauri/Cargo.toml found): {_REPO_ROOT}"
)

_SIDECAR_CMDS_RS = _REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds.rs"
_SIDECAR_CMDS_DIR = _REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds"


def _read_sidecar_cmds_module() -> str:
    """Concatenate sidecar_cmds.rs + sidecar_cmds/*.rs (EO-35 split)."""
    files = [_SIDECAR_CMDS_RS] + sorted(_SIDECAR_CMDS_DIR.glob("*.rs"))
    return "\n\n".join(p.read_text(encoding="utf-8") for p in files)


def _shutdown_sidecar_body() -> str:
    """Extract the body of ``shutdown_sidecar`` from sidecar_cmds.rs."""
    src = _read_sidecar_cmds_module()
    m = re.search(r"pub async fn shutdown_sidecar\b.*?\n\}", src, re.DOTALL)
    assert m, "shutdown_sidecar function not found in sidecar_cmds.rs"
    body = m.group(0)
    m_inner = re.search(
        r"pub(?:\(super\))? async fn shutdown_sidecar_inner\b.*?\n\}",
        src,
        re.DOTALL,
    )
    if m_inner:
        body += "\n\n" + m_inner.group(0)
    return body


_SUPERVISOR_RS = _REPO_ROOT / "src-tauri" / "src" / "sidecar" / "supervisor.rs"
_UTIL_RS = _REPO_ROOT / "src-tauri" / "src" / "util.rs"
_STATE_RS = _REPO_ROOT / "src-tauri" / "src" / "state.rs"
_SIDECAR_WS_PY = _REPO_ROOT / "voice_typer" / "server" / "sidecar_ws.py"


def _read(path: Path) -> str:
    """Read a source file as a string (source-inspection helper)."""
    assert path.is_file(), f"source file missing: {path}"
    return path.read_text(encoding="utf-8")


class TestShutdownSidecarSource:
    """Source-inspection tests for the Rust `shutdown_sidecar` command."""

    def test_source_file_exists(self):
        """Guard: the file under test must exist (catches path moves)."""
        assert _SIDECAR_CMDS_RS.is_file(), f"shutdown_sidecar source missing: {_SIDECAR_CMDS_RS}"

    def test_sets_shutting_down_atomic_flag(self):
        """Step 1: `state.shutting_down.store(true, Ordering::SeqCst)`."""
        body = _shutdown_sidecar_body()
        # The command path routes the flag set through `state.begin_shutdown()`
        flag_match = re.search(
            r"shutting_down\s*\.\s*(?:swap|store)\(true,\s*(?:std::sync::atomic::)?Ordering::SeqCst\)",
            body,
        )
        begin_shutdown_match = re.search(r"state\.begin_shutdown\(\)", body)
        assert flag_match is not None or begin_shutdown_match is not None, (
            "shutdown_sidecar must set state.shutting_down = true (atomic flag) "
            "via `shutting_down.swap(true, Ordering::SeqCst)` or "
            "`shutting_down.store(true, Ordering::SeqCst)` or the canonical "
            "`state.begin_shutdown()` routing (swap + notify_one, pinned by "
            "state_tests.rs) so supervisor doesn't respawn during shutdown"
        )
        # The flag set must come BEFORE the WS frame send.
        idx_flag = flag_match.start() if flag_match is not None else begin_shutdown_match.start()
        idx_frame = body.index('json!({"type": "shutdown"})')
        assert idx_flag < idx_frame, (
            "shutting_down flag must be set BEFORE the shutdown frame is sent "
            "(otherwise supervisor could respawn between flag-set and frame-send)"
        )

    def test_sends_shutdown_ws_frame(self):
        """Step 2: sends `{\"type\":\"shutdown\"}` via the WS writer channel."""
        body = _shutdown_sidecar_body()
        # The frame literal.
        assert 'json!({"type": "shutdown"})' in body, 'shutdown_sidecar must send a {"type":"shutdown"} WS frame'
        # Sent via the WS writer channel (ws_tx), not via stdout/stdin.
        assert "ws_tx" in body, (
            "shutdown frame must be sent via state.ws_tx (WS writer channel), "
            "not via stdout/stdin (ADR-0020 §1: sidecar stdout is reserved for "
            "the server_started JSON only)"
        )
        # Sent as a WS Text message (tokio_tungstenite::Message::Text).
        assert "Message::Text" in body, "shutdown frame must be a WS Text message (Message::Text)"

    def test_waits_for_command_event_terminated_with_timeout(self):
        """`SHUTDOWN_ACK_TIMEOUT_MS` deadline via `tokio::time::timeout`."""
        body = _shutdown_sidecar_body()
        # References the configured timeout constant.
        assert "SHUTDOWN_ACK_TIMEOUT_MS" in body, (
            "shutdown_sidecar must wait for SHUTDOWN_ACK_TIMEOUT_MS (the configured cooperative-shutdown deadline)"
        )
        # Uses tokio::time::timeout on the child exit receiver.
        assert "tokio::time::timeout" in body, (
            "shutdown_sidecar must use tokio::time::timeout to bound the wait for CommandEvent::Terminated"
        )
        # Matches on CommandEvent::Terminated specifically.
        assert "CommandEvent::Terminated" in body, (
            "shutdown_sidecar must match on CommandEvent::Terminated to detect graceful sidecar exit"
        )
        # Reads from the child_exit_rx (the per-sidecar event receiver).
        assert "child_exit_rx" in body, (
            "shutdown_sidecar must read from state.child_exit_rx (the event receiver captured at spawn time)"
        )

    def test_force_kills_child_as_backstop(self):
        """Step 4/5: force-kills the child via `child.kill()` as backstop."""
        body = _shutdown_sidecar_body()
        assert "mutex_lock(&state.child).take()" in body, (
            "shutdown_sidecar must take() the child handle (single-use after kill)"
        )
        # Calls .kill_tree().await on the child (ADR-0020 §10: recursive
        assert "child.kill_tree().await" in body, (
            "shutdown_sidecar must call child.kill_tree().await as the force-kill "
            "backstop (no-op if already exited, guarantees no zombie; ADR-0020 §10 "
            "uses kill_tree so grandchildren are reaped too)"
        )
        # The kill is reached on BOTH paths (graceful + timeout), verify
        idx_timeout = body.index("tokio::time::timeout")
        idx_kill = body.index("child.kill_tree().await")
        assert idx_kill > idx_timeout, (
            "child.kill() must run AFTER the wait block (tokio::time::timeout) "
            "so it fires on both the graceful-exit and timeout paths"
        )

    def test_logs_graceful_and_force_kill_outcomes(self):
        """Both outcomes produce a `[SHUTDOWN]` log line for runbook §6.6."""
        body = _shutdown_sidecar_body()
        # Graceful-exit log (Terminated received).
        assert "sidecar exited gracefully" in body, (
            "shutdown_sidecar must log '[SHUTDOWN] sidecar exited gracefully' "
            "when CommandEvent::Terminated is received (runbook §6.6 verification)"
        )
        # Force-kill backstop log (timeout fired).
        assert "did not exit within" in body or "force-killing" in body, (
            "shutdown_sidecar must log a warning when the sidecar doesn't exit "
            "within SHUTDOWN_ACK_TIMEOUT_MS (force-kill backstop trigger)"
        )
        # Final kill-completion log with graceful flag.
        assert "sidecar kill completed (graceful=" in body, (
            "shutdown_sidecar must log '[SHUTDOWN] sidecar kill completed "
            "(graceful=...)' so operators can tell from the log alone whether "
            "the sidecar acked or had to be force-killed"
        )

    def test_has_dev_mode_fallback_path(self):
        """Dev-mode sidecars (tokio::process::Child) have no"""
        body = _shutdown_sidecar_body()
        # The refactored dev-mode fallback sleeps once for the full
        assert "tokio::time::sleep" in body, (
            "shutdown_sidecar must use tokio::time::sleep for the dev-mode "
            "bounded-sleep fallback (no CommandEvent stream available)"
        )
        assert "dev-mode" in body.lower(), (
            "shutdown_sidecar must have an explicit dev-mode fallback branch "
            "(tokio::process::Child has no CommandEvent receiver)"
        )


class TestShutdownConstants:
    """Constants that govern the shutdown + supervisor dance (ADR-0020 §10)."""

    def test_shutdown_ack_timeout_is_2000ms(self):
        """SHUTDOWN_ACK_TIMEOUT_MS = 2000 (2s graceful window)."""
        src = _read(_UTIL_RS)
        m = re.search(
            r"pub\(crate\)\s+const\s+SHUTDOWN_ACK_TIMEOUT_MS\s*:\s*u64\s*=\s*(\d+)",
            src,
        )
        assert m, "SHUTDOWN_ACK_TIMEOUT_MS constant not found in util.rs"
        assert int(m.group(1)) == 2000, (
            f"SHUTDOWN_ACK_TIMEOUT_MS must be 2000 (2s graceful window per ADR-0020 §10), got {m.group(1)}"
        )

    def test_dev_mode_fallback_uses_full_deadline(self):
        """The dev-mode fallback sleeps for the full SHUTDOWN_ACK_TIMEOUT_MS"""
        body = _shutdown_sidecar_body()
        # The dev-mode branch sleeps for the full deadline duration, then
        assert "tokio::time::sleep(deadline_dur)" in body, (
            "shutdown_sidecar's dev-mode fallback must sleep for the full "
            "SHUTDOWN_ACK_TIMEOUT_MS deadline (single bounded sleep) before "
            "the force-kill backstop"
        )
        # The deadline itself is still the 2s cooperative-shutdown window.
        const_re = re.compile(
            r"SHUTDOWN_ACK_TIMEOUT_MS\s*:\s*u64\s*=\s*2000\s*;",
            re.MULTILINE,
        )
        assert const_re.search(_read(_UTIL_RS)), (
            "SHUTDOWN_ACK_TIMEOUT_MS must still be 2000 in util.rs (the dev-mode fallback sleeps this full window)"
        )

    def test_supervisor_backoff_schedule_is_doubling_5_steps(self):
        """SUPERVISOR_BACKOFF_MS = [500, 1000, 2000, 4000, 8000]."""
        src = _read(_UTIL_RS)
        m = re.search(
            r"pub\(crate\)\s+const\s+SUPERVISOR_BACKOFF_MS\s*:\s*&\[u64\]\s*=\s*&\[(.*?)\]",
            src,
        )
        assert m, "SUPERVISOR_BACKOFF_MS constant not found in util.rs"
        steps = [int(x.strip()) for x in m.group(1).split(",")]
        assert steps == [500, 1000, 2000, 4000, 8000], (
            f"SUPERVISOR_BACKOFF_MS must be [500, 1000, 2000, 4000, 8000] (doubling "
            f"schedule, 5 steps per ADR-0020 §10), got {steps}"
        )
        # Verify the doubling property explicitly.
        for i in range(1, len(steps)):
            assert steps[i] == steps[i - 1] * 2, (
                f"backoff step {i} must be 2x step {i - 1}: got {steps[i]} vs {steps[i - 1]}"
            )

    def test_supervisor_max_retries_is_5(self):
        """Retry cap = SUPERVISOR_BACKOFF_MS.len() = 5 → after 5 failed"""
        src = _read(_UTIL_RS)
        m = re.search(
            r"pub\(crate\)\s+const\s+SUPERVISOR_BACKOFF_MS\s*:\s*&\[u64\]\s*=\s*&\[(.*?)\]",
            src,
        )
        assert m, "SUPERVISOR_BACKOFF_MS schedule not found in util.rs"
        steps = [int(x.strip()) for x in m.group(1).split(",") if x.strip()]
        assert len(steps) == 5, (
            f"SUPERVISOR_BACKOFF_MS.len() (the supervisor retry cap) must be 5 "
            f"(then full-app relaunch per ADR-0020 §10), got {len(steps)}"
        )

    def test_pre_restart_delay_is_500ms(self):
        """PRE_RESTART_DELAY_MS = 500, delay between `supervisor_relaunching`"""
        src = _read(_UTIL_RS)
        m = re.search(
            r"pub\(crate\)\s+const\s+PRE_RESTART_DELAY_MS\s*:\s*u64\s*=\s*(\d+)",
            src,
        )
        assert m, "PRE_RESTART_DELAY_MS constant not found in util.rs"
        assert int(m.group(1)) == 500, (
            f"PRE_RESTART_DELAY_MS must be 500 (banner-render delay before app.restart()), got {m.group(1)}"
        )

    def test_backoff_schedule_length_matches_retry_cap(self):
        """The backoff schedule has 5 steps, the retry cap the supervisor"""
        src = _read(_UTIL_RS)
        sched_m = re.search(
            r"pub\(crate\)\s+const\s+SUPERVISOR_BACKOFF_MS\s*:\s*&\[u64\]\s*=\s*&\[(.*?)\]",
            src,
        )
        assert sched_m, "SUPERVISOR_BACKOFF_MS schedule not found in util.rs"
        steps = [int(x.strip()) for x in sched_m.group(1).split(",")]
        # The retry cap IS the schedule length; it must stay 5.
        cap = 5
        assert len(steps) == cap, (
            f"SUPERVISOR_BACKOFF_MS.len() ({len(steps)}) must stay equal to the "
            f"retry cap ({cap}) so the supervisor loop iterates exactly N times before "
            f"falling back to app.restart()"
        )


class TestSupervisorSource:
    """Source-inspection tests for the supervisor (supervisor.rs)."""

    def test_source_file_exists(self):
        assert _SUPERVISOR_RS.is_file(), f"supervisor source missing: {_SUPERVISOR_RS}"

    def test_respawn_is_serialized_via_atomic_flag(self):
        """`respawn_in_progress` AtomicBool serializes concurrent"""
        src = _read(_SUPERVISOR_RS)
        assert "respawn_in_progress" in src, (
            "supervisor must use state.respawn_in_progress to serialize concurrent respawn attempts"
        )
        assert "compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)" in src, (
            "supervisor must acquire respawn_in_progress via compare_exchange(false → true) (atomic test-and-set)"
        )

    def test_iterates_backoff_schedule(self):
        """The supervisor iterates `SUPERVISOR_BACKOFF_MS` with `enumerate()`."""
        src = _read(_SUPERVISOR_RS)
        assert "SUPERVISOR_BACKOFF_MS.iter().enumerate()" in src, (
            "supervisor must iterate SUPERVISOR_BACKOFF_MS with enumerate() so "
            "each retry sleeps for the corresponding backoff delay"
        )

    def test_caps_restarts_at_supervisor_max_retries(self):
        """After SUPERVISOR_MAX_RETRIES (5) attempts, emit `supervisor_relaunching`"""
        src = _read(_SUPERVISOR_RS)
        assert "backoff_exhausted" in src, (
            "supervisor must cap retries via the post-loop "
            "exhaustion path (reason 'backoff_exhausted'), the in-loop "
            "attempt>=SUPERVISOR_MAX_RETRIES guard was removed as dead code "
            "(NF-R19-2), since SUPERVISOR_BACKOFF_MS.len() == SUPERVISOR_MAX_RETRIES."
        )
        # Emits the relaunch event so the UI can show a banner. The emit
        assert '"supervisor_relaunching"' in src, (
            "supervisor must emit a 'supervisor_relaunching' Tauri event before "
            "app.restart() so the UI can render a 'restarting…' banner"
        )
        # Calls app.restart() (the whole-app relaunch).
        assert "app.restart()" in src, (
            "supervisor must call app.restart() (full-app relaunch) after "
            "exhausting SUPERVISOR_MAX_RETRIES, NOT just another sidecar respawn"
        )
        # The relaunch path includes the banner-render delay.
        assert "PRE_RESTART_DELAY_MS" in src, (
            "supervisor must sleep PRE_RESTART_DELAY_MS before app.restart() "
            "so the webview has time to render the 'restarting…' banner"
        )

    def test_respects_shutting_down_flag(self):
        """If `state.shutting_down` is true, the supervisor bails early"""
        src = _read(_SUPERVISOR_RS)
        assert "state.shutting_down.load(Ordering::SeqCst)" in src, (
            "supervisor must check state.shutting_down and bail early if a "
            "cooperative shutdown is in flight (don't respawn during quit)"
        )

    def test_returns_ok_on_successful_respawn(self):
        """On a successful `reconnect_ws`, the supervisor returns"""
        src = _read(_SUPERVISOR_RS)
        # The success branch returns Ok(()).
        assert "respawn succeeded" in src, "supervisor must log 'respawn succeeded' when reconnect_ws succeeds"
        # Find the success branch and verify it returns Ok(()) inside
        idx_log = src.index("respawn succeeded")
        idx_return = src.index("return Ok(())", idx_log)
        idx_err_arm = src.index("Err(e) =>", idx_log)
        assert idx_return < idx_err_arm, (
            "`return Ok(())` after 'respawn succeeded' log must be in the "
            "success `Ok(()) =>` match arm (before the `Err(e) =>` arm), "
            "the supervisor must return immediately on successful "
            "reconnect_ws (reset-on-success: the loop exits early, the "
            "next crash starts a fresh backoff schedule)"
        )

    def test_emits_reconnected_event_on_success(self):
        """On successful respawn, emit `supervisor_reconnected` so the UI"""
        src = _read(_SUPERVISOR_RS)
        assert 'app.emit("supervisor_reconnected"' in src, (
            "supervisor must emit a 'supervisor_reconnected' Tauri event on "
            "successful respawn so the UI can clear its 'reconnecting…' banner"
        )

    def test_rotates_token_on_respawn(self):
        """Each respawn generates a fresh token (per ADR-0020 §3) so a"""
        src = _read(_SUPERVISOR_RS)
        assert "generate_token()" in src, (
            "supervisor must call generate_token() on each respawn to "
            "rotate the bearer token (ADR-0020 §3: per-respawn token rotation)"
        )

    def test_rotates_child_exit_rx_on_respawn(self):
        """each respawn rotates `state.child_exit_rx` so the next"""
        src = _read(_SUPERVISOR_RS)
        assert "child_exit_rx" in src, (
            "supervisor must rotate state.child_exit_rx on respawn so the "
            "next shutdown_sidecar call polls the new sidecar's exit events"
        )

    def test_has_exhaustion_relaunch_after_loop(self):
        """Defensive: if the loop exits without returning (SUPERVISOR_BACKOFF_MS"""
        src = _read(_SUPERVISOR_RS)
        assert "backoff_exhausted" in src, (
            "supervisor must have a post-loop exhaustion path that emits "
            "'supervisor_relaunching' with reason 'backoff_exhausted' and calls "
            "app.restart() (defensive guard if the schedule shrinks below "
            "SUPERVISOR_MAX_RETRIES)"
        )


class TestSidecarStateFields:
    """
    SidecarState must expose the fields the shutdown path reads/writes.
    Pins the field names so a refactor can't silently rename
    """

    def test_shutting_down_atomic_bool(self):
        src = _read(_STATE_RS)
        assert re.search(r"pub\(crate\)\s+shutting_down\s*:\s*AtomicBool", src), (
            "SidecarState must have a `shutting_down: AtomicBool` field (set by shutdown_sidecar, read by respawn)"
        )

    def test_child_mutex_option(self):
        src = _read(_STATE_RS)
        assert re.search(r"pub\(crate\)\s+child\s*:\s*Mutex<Option<SidecarHandle>>", src), (
            "SidecarState must have a `child: Mutex<Option<SidecarHandle>>` field "
            "(take()'n by shutdown_sidecar for the force-kill backstop)"
        )

    def test_ws_tx_mutex_option(self):
        src = _read(_STATE_RS)
        assert re.search(r"pub\(crate\)\s+ws_tx\s*:\s*Mutex<Option<WsWriterTx>>", src), (
            "SidecarState must have a `ws_tx: Mutex<Option<WsWriterTx>>` field "
            "(used by shutdown_sidecar to send the shutdown frame)"
        )

    def test_child_exit_rx_async_mutex(self):
        src = _read(_STATE_RS)
        assert re.search(
            r"pub\(crate\)\s+child_exit_rx\s*:\s*AsyncMutex<Option<mpsc::Receiver<CommandEvent>>>",
            src,
        ), (
            "SidecarState must have a `child_exit_rx: AsyncMutex<Option<...>>` field "
            "(polled by shutdown_sidecar for CommandEvent::Terminated)"
        )

    def test_respawn_in_progress_atomic_bool(self):
        src = _read(_STATE_RS)
        assert re.search(r"pub\(crate\)\s+respawn_in_progress\s*:\s*AtomicBool", src), (
            "SidecarState must have a `respawn_in_progress: AtomicBool` field (serializes concurrent supervisors)"
        )


def _import_sidecar_ws():
    """Import sidecar_ws lazily so a missing `websockets` dep doesn't"""
    from voice_typer.server import sidecar_ws

    return sidecar_ws


class TestPythonShutdownHandler:
    """Mock-heavy tests for the Python sidecar's shutdown frame handler."""

    def _make_dispatch(self):
        """Build the WS dispatch closure around a real IPCServer with fakes."""
        from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

        sw = _import_sidecar_ws()
        server, _app, _service = make_ipc_server_with_fakes()
        dispatch = sw._make_dispatch(server)
        return dispatch, server

    @staticmethod
    def _wait_for(predicate, timeout: float = 2.0) -> bool:
        """Poll *predicate* until truthy or *timeout* elapses (the cleanup"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.005)
        return predicate()

    @pytest.mark.asyncio
    async def test_shutdown_returns_ack_envelope(self):
        """The handler must return `{\"type\":\"result\",\"data\":{\"ack\":True}}`"""
        dispatch, server = self._make_dispatch()
        result = await dispatch({"type": "shutdown"}, websocket=MagicMock())
        assert result == {"type": "result", "data": {"ack": True}}, (
            'shutdown handler must return {"type":"result","data":'
            '{"ack":True}}, the host correlates this ack with the '
            "shutdown frame it just sent"
        )
        # The service-layer quit must be scheduled on the background thread.
        assert self._wait_for(lambda: server.service.quit.call_count >= 1), (
            "service.quit() must be invoked (on the background cleanup thread)"
        )

    @pytest.mark.asyncio
    async def test_shutdown_logs_release_mic_message(self, caplog):
        """The handler logs `[SIDECAR-WS] shutdown received, releasing"""
        _import_sidecar_ws()
        with caplog.at_level("INFO"):
            dispatch, _server = self._make_dispatch()
            await dispatch({"type": "shutdown"}, websocket=MagicMock())
        joined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "shutdown received: releasing mic and exiting" in joined, (
            "shutdown handler must log '[SIDECAR-WS] shutdown received, "
            "releasing mic and exiting' (runbook §6.6 sidecar.log verification)"
        )

    @pytest.mark.asyncio
    async def test_shutdown_schedules_quit_on_background_thread(self):
        """The handler schedules `service.quit()` on a daemon thread"""
        import types

        import voice_typer.server.ipc.dispatcher as dispatcher_mod

        dispatch, server = self._make_dispatch()
        captured: dict = {}

        class FakeThread:
            def __init__(self, target=None, name=None, daemon=None, **kw):
                captured["target"] = target
                captured["name"] = name
                captured["daemon"] = daemon

            def start(self):
                # Don't actually start, the test invokes target()
                captured["started"] = True

        fake_threading = types.SimpleNamespace(
            Thread=FakeThread,
            Lock=threading.Lock,
            Event=threading.Event,
        )
        with patch.object(dispatcher_mod, "threading", fake_threading):
            result = await dispatch({"type": "shutdown"}, websocket=MagicMock())

        # Ack returned immediately (before quit runs).
        assert result == {"type": "result", "data": {"ack": True}}
        # Thread was configured as a daemon (so it doesn't block process
        assert captured.get("daemon") is True, (
            "shutdown thread must be a daemon so a hung quit() doesn't block "
            "process exit (the host's force-kill backstop will reap it)"
        )
        # Thread name is stable for log grepping.
        assert captured.get("name") == "ipc-shutdown-cleanup", (
            "shutdown thread must be named 'ipc-shutdown-cleanup' for log/metric attribution"
        )
        # Target was captured + started.
        assert captured.get("target") is not None
        assert captured.get("started") is True
        assert server.service.quit.call_count == 0, (
            "service.quit() must NOT be called inline, it must be "
            "scheduled on the background thread so the ack returns first"
        )
        # Now invoke the captured target and verify quit() runs.
        captured["target"]()
        assert server.service.quit.call_count == 1, "the background-thread target must call service.quit() exactly once"

    @pytest.mark.asyncio
    async def test_shutdown_ack_returns_before_quit_completes(self):
        """quit thread completes, even if quit() takes 500ms."""
        _import_sidecar_ws()
        quit_started = threading.Event()
        quit_can_finish = threading.Event()

        def slow_quit():
            quit_started.set()
            # Block until the test releases us, simulates a slow mic
            quit_can_finish.wait(timeout=5.0)

        dispatch, server = self._make_dispatch()
        server.service.quit = slow_quit

        # Use the REAL threading.Thread (not patched) so the timing
        t0 = time.monotonic()
        result = await dispatch({"type": "shutdown"}, websocket=MagicMock())
        ack_latency = time.monotonic() - t0

        # Ack must return in well under the host's 2s force-kill window,
        assert result == {"type": "result", "data": {"ack": True}}
        assert ack_latency < 1.0, (
            f"ack must return before quit() completes (got ack in "
            f"{ack_latency * 1000:.1f}ms; expected <1000ms even with a slow quit)"
        )
        assert quit_started.wait(timeout=2.0), "background quit thread must have started after the ack returned"
        # Release the slow quit so the test doesn't hang.
        quit_can_finish.set()

    @pytest.mark.asyncio
    async def test_shutdown_does_not_swallow_quit_exceptions(self, caplog):
        """exception (not swallow it silently) so the operator can diagnose"""
        _import_sidecar_ws()

        def boom():
            raise RuntimeError("quit blew up")

        dispatch, server = self._make_dispatch()
        server.service.quit = boom
        with caplog.at_level("ERROR"):
            await dispatch({"type": "shutdown"}, websocket=MagicMock())
            # The background thread logs the exception asynchronously —
            assert self._wait_for(lambda: any("service.quit() raised" in rec.getMessage() for rec in caplog.records)), (
                "shutdown handler must log (at ERROR, exc_info) any exception "
                "from service.quit() so a stuck shutdown is diagnosable"
            )

    @pytest.mark.asyncio
    async def test_shutdown_does_not_hit_rate_limiter(self):
        """200-burst budget) can still shut down cleanly."""
        _import_sidecar_ws()
        # Wire a rate limiter that always rejects BEFORE _make_dispatch
        limiter = MagicMock()
        limiter.allow.return_value = False
        with patch("voice_typer.server.ipc_server._get_rate_limiter", return_value=limiter):
            dispatch, server = self._make_dispatch()
            result = await dispatch({"type": "shutdown"}, websocket=MagicMock())
        assert result == {"type": "result", "data": {"ack": True}}, (
            "shutdown must bypass the rate limiter (it's a control frame, not "
            "a dispatch frame), got an error envelope instead of an ack"
        )
        assert limiter.allow.call_count == 0, (
            "shutdown frame must NOT call the rate limiter, the dispatch gate exempts shutdown frames"
        )
        assert self._wait_for(lambda: server.service.quit.call_count >= 1)

    @pytest.mark.asyncio
    async def test_shutdown_envelope_is_json_serializable(self):
        """The ack envelope must round-trip through `json.dumps` →"""
        dispatch, _server = self._make_dispatch()
        result = await dispatch({"type": "shutdown"}, websocket=MagicMock())
        # Round-trip.
        wire = json.dumps(result)
        parsed = json.loads(wire)
        assert parsed == result
        # The `ack` value must serialize to JSON `true` (Python True →
        assert '"ack": true' in wire, f"ack must serialize to JSON true, got wire form: {wire}"

    @pytest.mark.asyncio
    async def test_non_shutdown_frame_does_not_trigger_quit(self):
        """Sanity: a non-shutdown frame must NOT schedule quit()."""
        dispatch, server = self._make_dispatch()
        # A regular dispatch frame, routed through the real server._dispatch
        result = await dispatch({"type": "get_status", "data": {}}, websocket=MagicMock())
        assert server.service.quit.call_count == 0, "non-shutdown frames must NOT schedule service.quit()"
        assert result is not None, "get_status must produce a response envelope"
        assert result.get("type") in ("status", "result"), f"unexpected envelope: {result!r}"


class TestShutdownAckTimeoutConstant:
    """ADR-0020 §10: the cooperative-shutdown hard timeout is defined"""

    def test_rust_shutdown_ack_timeout_ms_is_2000(self):
        """The Rust host's ``SHUTDOWN_ACK_TIMEOUT_MS`` constant in"""
        src = _read(_UTIL_RS)
        # ``pub(crate) const SHUTDOWN_ACK_TIMEOUT_MS: u64 = 2000;``
        const_re = re.compile(
            r"SHUTDOWN_ACK_TIMEOUT_MS\s*:\s*u64\s*=\s*2000\s*;",
            re.MULTILINE,
        )
        assert const_re.search(src), (
            "src-tauri/src/util.rs must define "
            "SHUTDOWN_ACK_TIMEOUT_MS: u64 = 2000 (ADR-0020 §10: 2s "
            "cooperative-shutdown hard timeout, the Rust host's "
            "kill-children backstop fires after this window)."
        )

    def test_python_sidecar_does_not_define_dead_timeout_constant(self):
        """``sidecar_ws.py`` must NOT define the dead"""
        src = _read(_SIDECAR_WS_PY)
        # The constant declaration must be gone (only a comment
        const_re = re.compile(
            r"^\s*_SHUTDOWN_ACK_TIMEOUT_SECONDS\s*=\s*",
            re.MULTILINE,
        )
        assert not const_re.search(src), (
            "DT-54: sidecar_ws.py must NOT define the dead "
            "_SHUTDOWN_ACK_TIMEOUT_SECONDS constant, Python never "
            "enforced the cooperative-shutdown timeout (the Rust host's "
            "SHUTDOWN_ACK_TIMEOUT_MS in src-tauri/src/util.rs is the "
            "single source of truth)."
        )


class TestRunbookCoverage:
    """Cross-check that the source actually implements what runbook §6.6"""

    def test_rust_log_string_matches_runbook_intent(self):
        """The Rust log string must contain `sidecar kill completed` so"""
        src = _read_sidecar_cmds_module()
        assert "[SHUTDOWN]" in src, (
            "Rust shutdown logs must use the '[SHUTDOWN]' prefix so the runbook "
            "§6.6 grep (Select-String 'SHUTDOWN|shutdown') matches"
        )

    def test_python_log_string_matches_runbook(self):
        """The Python log string must match the runbook §6.6 expected"""
        dispatcher = _REPO_ROOT / "voice_typer" / "server" / "ipc" / "dispatcher.py"
        src = dispatcher.read_text(encoding="utf-8")
        assert "[SIDECAR-WS] shutdown received: releasing mic and exiting" in src, (
            "Python shutdown handler must log the exact runbook §6.6 line: "
            "'[SIDECAR-WS] shutdown received, releasing mic and exiting'"
        )
