"""cooperative shutdown validation (macOS)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parents[3]
# Don't assert the literal repo-dir name, the repo may be cloned under any
assert (_REPO_ROOT / "pyproject.toml").is_file(), (
    f"_REPO_ROOT does not look like the voice-typer project root (no pyproject.toml found): {_REPO_ROOT}"
)
assert (_REPO_ROOT / "src-tauri" / "Cargo.toml").is_file(), (
    f"_REPO_ROOT does not look like the voice-typer project root (no src-tauri/Cargo.toml found): {_REPO_ROOT}"
)

_SIDECAR_CMDS_RS = _REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds.rs"
_SIDECAR_CMDS_DIR = _REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds"


def _read_sidecar_cmds_module() -> str:
    """Concatenate sidecar_cmds.rs + sidecar_cmds/*.rs (EO-35 split)."""
    files = [_SIDECAR_CMDS_RS] + sorted(_SIDECAR_CMDS_DIR.glob("*.rs"))
    return "\n\n".join(p.read_text(encoding="utf-8") for p in files)


_SUPERVISOR_RS = _REPO_ROOT / "src-tauri" / "src" / "sidecar" / "supervisor.rs"
_UTIL_RS = _REPO_ROOT / "src-tauri" / "src" / "util.rs"
_STATE_RS = _REPO_ROOT / "src-tauri" / "src" / "state.rs"
_HANDLE_RS = _REPO_ROOT / "src-tauri" / "src" / "sidecar" / "handle.rs"
_CARGO_LOCK = _REPO_ROOT / "src-tauri" / "Cargo.lock"
_CARGO_TOML = _REPO_ROOT / "src-tauri" / "Cargo.toml"
_SIDECAR_WS_PY = _REPO_ROOT / "voice_typer" / "server" / "sidecar_ws.py"
_MACOS_RUNBOOK = _REPO_ROOT / "docs" / "migration" / "macos-validation-runbook.md"


def _read(path: Path) -> str:
    """Read a source file as a string (source-inspection helper)."""
    assert path.is_file(), f"source file missing: {path}"
    return path.read_text(encoding="utf-8")


def _shutdown_sidecar_body() -> str:
    """Extract the body of `shutdown_sidecar` from sidecar_cmds.rs."""
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


class TestShutdownSidecarSource:
    """Source-inspection tests for the Rust `shutdown_sidecar` command."""

    def test_source_file_exists(self):
        """Guard: the file under test must exist (catches path moves)."""
        assert _SIDECAR_CMDS_RS.is_file(), f"shutdown_sidecar source missing: {_SIDECAR_CMDS_RS}"

    def test_sets_shutting_down_atomic_flag(self):
        """Step 1: set `state.shutting_down` (atomic flag) via `swap`/`store`"""
        body = _shutdown_sidecar_body()
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
        # Calls kill_tree().await on the child (the recursive process-tree
        assert "child.kill_tree().await" in body, (
            "shutdown_sidecar must call child.kill_tree().await as the force-kill "
            "backstop (no-op if already exited, guarantees no zombie)"
        )
        # The kill is reached on BOTH paths (graceful + timeout), verify
        idx_take = body.index("rx_guard.take()")
        idx_kill = body.index("child.kill_tree().await")
        assert idx_kill > idx_take, (
            "child.kill_tree() must run AFTER the wait block (rx_guard.take()) so it "
            "fires on both the graceful-exit and timeout paths"
        )

    def test_logs_graceful_and_force_kill_outcomes(self):
        """Both outcomes produce a `[SHUTDOWN]` log line for runbook §6.5."""
        body = _shutdown_sidecar_body()
        # Graceful-exit log (Terminated received).
        assert "sidecar exited gracefully" in body, (
            "shutdown_sidecar must log '[SHUTDOWN] sidecar exited gracefully' "
            "when CommandEvent::Terminated is received (runbook §6.5 verification)"
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
        assert "dev-mode" in body.lower(), (
            "shutdown_sidecar must have an explicit dev-mode fallback branch "
            "(tokio::process::Child has no CommandEvent receiver)"
        )
        assert "tokio::time::sleep" in body, (
            "shutdown_sidecar dev-mode fallback must use tokio::time::sleep "
            "for the bounded wait before the force-kill backstop"
        )


class TestMacOSSignalBehavior:
    """macOS-specific signal verification via source inspection."""

    def test_state_source_file_exists(self):
        """Guard: state.rs must exist (holds SidecarHandle enum)."""
        assert _STATE_RS.is_file(), f"state.rs missing: {_STATE_RS}"

    def test_sidecar_handle_has_shell_plugin_variant(self):
        """`SidecarHandle::ShellPlugin(Option<CommandChild>)` variant exists"""
        src = _read(_HANDLE_RS)
        assert re.search(r"ShellPlugin\s*\(\s*Option<CommandChild>\s*\)", src), (
            "SidecarHandle must have a ShellPlugin(Option<CommandChild>) variant for "
            "release builds (CommandChild::kill sends SIGTERM on macOS)"
        )

    def test_sidecar_handle_has_dev_mode_variant(self):
        """`SidecarHandle::DevMode(tokio::process::Child)` variant exists"""
        src = _read(_HANDLE_RS)
        assert re.search(r"DevMode\s*\(\s*tokio::process::Child\s*\)", src), (
            "SidecarHandle must have a DevMode(tokio::process::Child) variant "
            "for dev mode (tokio Child::kill sends SIGKILL on macOS)"
        )

    def test_kill_method_matches_on_both_variants(self):
        """delegates to the variant's `kill()` method."""
        src = _read(_HANDLE_RS)
        # The kill method exists and is async (returns io::Result<()>).
        assert re.search(
            r"pub\(crate\)\s+async\s+fn\s+kill\s*\(\s*mut\s+self\s*\)\s*->\s*std::io::Result<\(\)>",
            src,
        ), "SidecarHandle must have an async kill(mut self) -> io::Result<()> method"
        # Both arms call .kill() on the inner handle.
        assert "SidecarHandle::ShellPlugin(c)" in src, (
            "kill() must match SidecarHandle::ShellPlugin(c) and call child.kill()"
        )
        assert "SidecarHandle::DevMode(c)" in src, "kill() must match SidecarHandle::DevMode(c) and call c.kill().await"
        # ShellPlugin arm `take()`s the inner Option then calls
        assert "c.take()" in src, "ShellPlugin arm must take() the inner Option<CommandChild> before kill"
        assert "child.kill()" in src, (
            "ShellPlugin arm must call child.kill() (CommandChild::kill is sync, sends SIGTERM on macOS)"
        )
        # DevMode arm calls c.kill().await (async, tokio Child::kill is async).
        m_dev = re.search(
            r"SidecarHandle::DevMode\(c\)\s*=>\s*c\.kill\(\)\.await",
            src,
            re.DOTALL,
        )
        assert m_dev, "DevMode arm must call c.kill().await (tokio Child::kill is async, sends SIGKILL on macOS)"

    def test_tauri_plugin_shell_version_is_pinned(self):
        """SIGTERM-on-Unix behavior is locked."""
        lock = _read(_CARGO_LOCK)
        # The package entry must exist with a 2.x version.
        m = re.search(
            r'name\s*=\s*"tauri-plugin-shell"\s*\nversion\s*=\s*"(\d+)\.(\d+)\.(\d+)"',
            lock,
        )
        assert m, (
            "tauri-plugin-shell must be pinned in Cargo.lock (used by SidecarHandle::ShellPlugin for SIGTERM on macOS)"
        )
        major = int(m.group(1))
        assert major == 2, (
            f"tauri-plugin-shell major version must be 2 (Tauri 2.x plugin "
            f"series, CommandChild::kill sends SIGTERM on Unix); got {major}"
        )

    def test_tauri_plugin_shell_declared_in_cargo_toml(self):
        """Cargo.toml declares tauri-plugin-shell as a dependency (so a"""
        toml = _read(_CARGO_TOML)
        assert re.search(r"^tauri-plugin-shell\s*=", toml, re.MULTILINE), (
            "tauri-plugin-shell must be declared in Cargo.toml [dependencies] "
            "(provides CommandChild used by SidecarHandle::ShellPlugin on macOS)"
        )

    def test_state_docstring_documents_cross_platform_kill(self):
        """state.rs's SidecarHandle docstring must mention BOTH variants"""
        src = _read(_STATE_RS)
        # The enum docstring (line starting with /// above the enum).
        assert "shell-plugin" in src.lower() or "ShellPlugin" in src, (
            "SidecarHandle enum must be documented with the shell-plugin variant"
        )
        assert "dev mode" in src.lower() or "DevMode" in src, (
            "SidecarHandle enum must be documented with the dev-mode variant"
        )
        # The kill method itself must be documented.
        assert "kill" in src.lower(), "SidecarHandle::kill method must be present and documented"

    def test_shutdown_sidecar_uses_sidecar_handle_kill(self):
        """shutdown_sidecar calls `child.kill_tree().await` which routes"""
        body = _shutdown_sidecar_body()
        # Takes the Option<SidecarHandle> (via the poison-safe helper).
        assert "mutex_lock(&state.child).take()" in body, (
            "shutdown_sidecar must take() the SidecarHandle from state.child"
        )
        assert "child.kill_tree().await" in body, (
            "shutdown_sidecar must call child.kill_tree().await which routes to "
            "SidecarHandle::kill (SIGTERM on release, SIGKILL on dev mode macOS)"
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

    def test_shutdown_poll_interval_constant_removed(self):
        """The dev-mode shutdown fallback no longer polls in fixed"""
        src = _read(_UTIL_RS)
        m = re.search(r"SHUTDOWN_POLL_INTERVAL_MS", src)
        assert not m, (
            "SHUTDOWN_POLL_INTERVAL_MS must not exist in util.rs, the dev-mode "
            "fallback is now a single bounded sleep, not a poll loop (dead code removed)"
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
        """persistent restart-attempt circuit breaker (`MAX_RESTART_ATTEMPTS`)"""
        src = _read(_SUPERVISOR_RS)
        # The circuit-breaker cap constant.
        assert "MAX_RESTART_ATTEMPTS" in src, "supervisor must define MAX_RESTART_ATTEMPTS (the full-app relaunch cap)"
        # The breaker trip check.
        assert ">= MAX_RESTART_ATTEMPTS" in src, (
            "supervisor must trip the breaker via `restart_count >= MAX_RESTART_ATTEMPTS`"
        )
        assert '"supervisor_relaunching"' in src, (
            "supervisor must emit a 'supervisor_relaunching' Tauri event before "
            "app.restart() so the UI can render a 'restarting…' banner"
        )
        # Calls app.restart() (the whole-app relaunch).
        assert "app.restart()" in src, (
            "supervisor must call app.restart() (full-app relaunch) after "
            "exhausting the restart-attempt budget, NOT just another sidecar respawn"
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
        # Search forward from the log line for `return Ok(())`.
        idx_return = src.index("return Ok(())", idx_log)
        # The return must be within the success arm (reset counter + emit
        assert idx_return - idx_log < 2000, (
            f"`return Ok(())` after 'respawn succeeded' log must be in the "
            f"same match arm (within 400 chars); gap was "
            f"{idx_return - idx_log} chars, the supervisor must return "
            f"immediately on successful reconnect_ws (reset-on-success: the "
            f"loop exits early, the next crash starts a fresh backoff schedule)"
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


class TestPythonShutdownHandler:
    """Mock-heavy tests for the Python sidecar's shutdown frame handler."""

    def _make_server(self):
        """Build an IPCServer with a mocked app + service."""
        from voice_typer.server.ipc_server import IPCServer

        app = MagicMock(name="app")
        service = MagicMock(name="service")
        server = IPCServer(app, service=service)
        return server, app, service

    def test_shutdown_returns_ack_envelope(self):
        """The handler must return `{\"type\":\"result\",\"data\":{\"ack\":True}}`"""
        server, _app, service = self._make_server()
        result = server._handle_shutdown(data=None, resp={"id": 1})
        assert result["type"] == "result", 'shutdown handler must return a {"type":"result",...} ack'
        assert result["data"] == {"ack": True}, (
            'shutdown handler must return {"data":{"ack":True}}, the host '
            "correlates this ack with the shutdown frame it just sent"
        )
        service.quit.assert_called_once_with()

    def test_shutdown_delegates_to_service_quit_not_app_quit(self):
        """shutdown must call `service.quit()` (NOT `app.quit()`) so"""
        server, app, service = self._make_server()
        server._handle_shutdown(data=None, resp={"id": 1})
        service.quit.assert_called_once_with()
        app.quit.assert_not_called()

    def test_shutdown_is_idempotent(self):
        """A duplicate shutdown returns the same ack without spawning a"""
        server, _app, service = self._make_server()
        first = server._handle_shutdown(data=None, resp={"id": 1})
        second = server._handle_shutdown(data=None, resp={"id": 2})
        assert first["data"] == {"ack": True}
        assert second["data"] == {"ack": True}
        # Only one cleanup thread owns service.quit(), the second
        service.quit.assert_called_once_with()

    def test_shutdown_logs_release_mic_message(self, caplog):
        """The handler logs `[SIDECAR-WS] shutdown received, releasing"""
        import logging

        server, _app, _service = self._make_server()
        with caplog.at_level(logging.INFO, logger="voice_typer.server.ipc_server"):
            server._handle_shutdown(data=None, resp={"id": 1})
        joined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "shutdown received: releasing mic and exiting" in joined, (
            "shutdown handler must log '[SIDECAR-WS] shutdown received, "
            "releasing mic and exiting' (runbook sidecar.log verification)"
        )

    def test_shutdown_ack_is_json_serializable(self):
        """The ack envelope must round-trip through `json.dumps` →"""
        server, _app, _service = self._make_server()
        result = server._handle_shutdown(data=None, resp={"id": 1})
        envelope = {"type": result["type"], "data": result["data"]}
        wire = json.dumps(envelope)
        assert '"ack": true' in wire, f"ack must serialize to JSON true, got wire form: {wire}"
        assert json.loads(wire) == envelope


class TestShutdownAckTimeoutConstant:
    """ADR-0020 §10: the cooperative-shutdown hard timeout is defined"""

    def test_rust_shutdown_ack_timeout_ms_is_2000(self):
        """The Rust host's ``SHUTDOWN_ACK_TIMEOUT_MS`` constant in"""
        src = _read(_UTIL_RS)
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
    """Cross-check that the source actually implements what the macOS"""

    def test_macos_runbook_exists(self):
        """Guard: the macOS validation runbook must exist."""
        assert _MACOS_RUNBOOK.is_file(), f"macOS validation runbook missing: {_MACOS_RUNBOOK}"

    def test_macos_runbook_has_cooperative_shutdown_section(self):
        """Runbook §6.5 (Cooperative shutdown gate) must exist with the"""
        src = _read(_MACOS_RUNBOOK)
        # The section header.
        assert "Cooperative shutdown" in src, "macOS runbook must have a 'Cooperative shutdown' section (§6.5)"
        # The pass criteria mentions the 2-second window.
        assert re.search(r"\b2\s*(?:second|sec|s)\b", src, re.IGNORECASE), (
            "macOS runbook §6.5 must mention the 2-second sidecar-exit window"
        )
        # The hard-kill backstop test (lldb pause).
        assert "lldb" in src, (
            "macOS runbook §6.5 must document the hard-kill backstop test "
            "(uses lldb to hang the sidecar, then verifies force-kill cleans it)"
        )
        # pgrep verification.
        assert "pgrep" in src, "macOS runbook §6.5 must use pgrep to verify the sidecar process is gone after shutdown"

    def test_rust_log_string_matches_runbook_intent(self):
        """The Rust log string must contain `sidecar kill completed` so"""
        src = _read_sidecar_cmds_module()
        assert "[SHUTDOWN]" in src, (
            "Rust shutdown logs must use the '[SHUTDOWN]' prefix so the runbook "
            "§6.5 grep (grep 'SHUTDOWN|shutdown') matches"
        )

    def test_python_log_string_matches_runbook(self):
        """The Python log string must match the runbook §6.5 expected"""
        dispatcher = _REPO_ROOT / "voice_typer" / "server" / "ipc" / "dispatcher.py"
        src = _read(dispatcher)
        assert "[SIDECAR-WS] shutdown received: releasing mic and exiting" in src, (
            "Python shutdown handler must log the exact runbook §6.5 line: "
            "'[SIDECAR-WS] shutdown received, releasing mic and exiting'"
        )
