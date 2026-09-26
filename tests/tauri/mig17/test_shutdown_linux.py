"""cooperative shutdown validation (Linux)."""

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


_SUPERVISOR_RS = _REPO_ROOT / "src-tauri" / "src" / "sidecar" / "supervisor.rs"
_UTIL_RS = _REPO_ROOT / "src-tauri" / "src" / "util.rs"
_STATE_RS = _REPO_ROOT / "src-tauri" / "src" / "state.rs"
_HANDLE_RS = _REPO_ROOT / "src-tauri" / "src" / "sidecar" / "handle.rs"
_CARGO_LOCK = _REPO_ROOT / "src-tauri" / "Cargo.lock"
_CARGO_TOML = _REPO_ROOT / "src-tauri" / "Cargo.toml"
_SIDECAR_WS_PY = _REPO_ROOT / "voice_typer" / "server" / "sidecar_ws.py"
_LINUX_RUNBOOK = _REPO_ROOT / "docs" / "migration" / "linux-validation-runbook.md"


def _read_state_combined() -> str:
    """Read state.rs + sidecar/handle.rs concatenated (post EO-35 split)."""
    parts = []
    if _STATE_RS.is_file():
        parts.append(_STATE_RS.read_text(encoding="utf-8"))
    if _HANDLE_RS.is_file():
        parts.append(_HANDLE_RS.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def _read(path: Path) -> str:
    """Read a source file as a string (source-inspection helper)."""
    assert path.is_file(), f"source file missing: {path}"
    return path.read_text(encoding="utf-8")


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


class TestShutdownSidecarSource:
    """Source-inspection tests for the Rust ``shutdown_sidecar`` command."""

    def test_source_file_exists(self):
        """Guard: the file under test must exist (catches path moves)."""
        assert _SIDECAR_CMDS_RS.is_file(), f"shutdown_sidecar source missing: {_SIDECAR_CMDS_RS}"

    def test_sets_shutting_down_atomic_flag(self):
        """Step 1: set ``state.shutting_down`` (atomic flag)."""
        body = _shutdown_sidecar_body()
        flag_match = re.search(
            r"shutting_down\s*\.\s*(?:swap|store)\(true,\s*(?:std::sync::atomic::)?Ordering::SeqCst\)",
            body,
        )
        begin_shutdown_match = re.search(r"state\.begin_shutdown\(\)", body)
        assert flag_match is not None or begin_shutdown_match is not None, (
            "shutdown_sidecar must set state.shutting_down = true (atomic flag) "
            "via `shutting_down.swap(true, Ordering::SeqCst)` (PVT-17) or "
            "`shutting_down.store(true, Ordering::SeqCst)` (pre-PVT-17) or the "
            "canonical `state.begin_shutdown()` routing (swap + notify_one, "
            "pinned by state_tests.rs) so supervisor doesn't respawn during "
            "shutdown"
        )
        # The flag set must come BEFORE the WS frame send.
        idx_flag = flag_match.start() if flag_match is not None else begin_shutdown_match.start()
        idx_frame = body.index('json!({"type": "shutdown"})')
        assert idx_flag < idx_frame, (
            "shutting_down flag must be set BEFORE the shutdown frame is sent "
            "(otherwise supervisor could respawn between flag-set and frame-send)"
        )

    def test_sends_shutdown_ws_frame(self):
        """Step 2: sends ``{\"type\":\"shutdown\"}`` via the WS writer channel."""
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
        """``SHUTDOWN_ACK_TIMEOUT_MS`` deadline via ``tokio::time::timeout``."""
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
        """Step 4/5: force-kills the child via ``child.kill_tree().await`` as backstop."""
        body = _shutdown_sidecar_body()
        # Takes the child out of the Option (single-use after kill).
        take_match = re.search(
            r"(?:mutex_lock\(&state\.child\)|state\.child\.lock\(\)\.unwrap\(\))\.take\(\)",
            body,
        )
        assert take_match is not None, (
            "shutdown_sidecar must take() the child handle (single-use after kill) "
            "via `mutex_lock(&state.child).take()` (CR-29) or "
            "`state.child.lock().unwrap().take()` (pre-CR-29)"
        )
        # Calls .kill().await or .kill_tree().await on the child.
        kill_match = re.search(r"child\.(?:kill_tree|kill)\(\)\.await", body)
        assert kill_match is not None, (
            "shutdown_sidecar must call child.kill().await or child.kill_tree().await "
            "as the force-kill backstop (no-op if already exited, guarantees no zombie)"
        )
        # The kill is reached on BOTH paths (graceful + timeout), verify
        idx_take = take_match.start()
        idx_kill = kill_match.start()
        assert idx_kill > idx_take, (
            "child.kill() must run AFTER the wait block (marked by the "
            "state.child take()) so it fires on both the graceful-exit "
            "and timeout paths"
        )

    def test_logs_graceful_and_force_kill_outcomes(self):
        """Both outcomes produce a ``[SHUTDOWN]`` log line for runbook Step 10."""
        body = _shutdown_sidecar_body()
        # Graceful-exit log (Terminated received).
        assert "sidecar exited gracefully" in body, (
            "shutdown_sidecar must log '[SHUTDOWN] sidecar exited gracefully' "
            "when CommandEvent::Terminated is received (runbook Step 10 verification)"
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
        assert "SHUTDOWN_ACK_TIMEOUT_MS" in body, (
            "shutdown_sidecar must reference SHUTDOWN_ACK_TIMEOUT_MS, the "
            "dev-mode fallback sleeps for the full cooperative-shutdown "
            "deadline window before force-killing the sidecar"
        )


class TestLinuxSignalBehavior:
    """Linux-specific signal verification via source inspection."""

    def test_state_source_file_exists(self):
        """Guard: state.rs must exist (holds SidecarHandle enum)."""
        assert _STATE_RS.is_file(), f"state.rs missing: {_STATE_RS}"

    def test_sidecar_handle_has_shell_plugin_variant(self):
        """release builds (externalBin / Nuitka-frozen python-sidecar on Linux)."""
        src = _read_state_combined()
        assert re.search(
            r"ShellPlugin\s*\(\s*(?:Option\s*<\s*)?CommandChild(?:\s*>\s*)?\s*\)",
            src,
        ), (
            "SidecarHandle must have a ShellPlugin(CommandChild) [or "
            "ShellPlugin(Option<CommandChild>)] variant for release builds "
            "(CommandChild::kill sends SIGTERM on Linux)"
        )

    def test_sidecar_handle_has_dev_mode_variant(self):
        """``SidecarHandle::DevMode(tokio::process::Child)`` variant"""
        src = _read_state_combined()
        assert re.search(r"DevMode\s*\(\s*tokio::process::Child\s*\)", src), (
            "SidecarHandle must have a DevMode(tokio::process::Child) variant "
            "for dev mode (tokio Child::kill sends SIGKILL on Linux)"
        )

    def test_kill_method_matches_on_both_variants(self):
        """delegates to the variant's ``kill()`` method."""
        src = _read_state_combined()
        # The kill method exists and is async (returns io::Result<()>).
        assert re.search(
            r"pub\(crate\)\s+async\s+fn\s+kill\s*\(\s*(?:mut\s+)?self\s*\)\s*->\s*std::io::Result<\(\)>",
            src,
        ), "SidecarHandle must have an async kill(self) -> io::Result<()> method"
        # Both arms call .kill() on the inner handle. The DevMode arm
        assert "SidecarHandle::ShellPlugin(c)" in src, (
            "kill() must match SidecarHandle::ShellPlugin(c) and call c.kill()"
        )
        assert "SidecarHandle::DevMode(mut c)" in src or "SidecarHandle::DevMode(c)" in src, (
            "kill() must match SidecarHandle::DevMode(mut c) (or DevMode(c) "
            "post-&mut-self refactor) and call c.kill().await"
        )
        m_shell = re.search(
            r"SidecarHandle::ShellPlugin\(c\)\s*=>\s*(?:\{[^}]*?c\.kill\(\)|c\.kill\(\)|match\s+c\.take\(\))",
            src,
            re.DOTALL,
        )
        assert m_shell, (
            "ShellPlugin arm must call c.kill() (CommandChild::kill is sync, "
            "sends SIGTERM on Linux) or take()+kill() on the inner Option"
        )
        m_dev = re.search(
            r"SidecarHandle::DevMode\s*\(\s*(?:mut\s+)?c\)\s*=>\s*(?:\{[^}]*?c\.kill\(\)\.await|c\.kill\(\)\.await)",
            src,
            re.DOTALL,
        )
        assert m_dev, "DevMode arm must call c.kill().await (tokio Child::kill is async, sends SIGKILL on Linux)"

    def test_tauri_plugin_shell_version_is_pinned(self):
        """SIGTERM-on-Unix behavior is locked."""
        lock = _read(_CARGO_LOCK)
        # The package entry must exist with a 2.x version.
        m = re.search(
            r'name\s*=\s*"tauri-plugin-shell"\s*\nversion\s*=\s*"(\d+)\.(\d+)\.(\d+)"',
            lock,
        )
        assert m, (
            "tauri-plugin-shell must be pinned in Cargo.lock (used by SidecarHandle::ShellPlugin for SIGTERM on Linux)"
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
            "(provides CommandChild used by SidecarHandle::ShellPlugin on Linux)"
        )

    def test_state_docstring_documents_cross_platform_kill(self):
        """state.rs's SidecarHandle docstring must mention BOTH variants"""
        src = _read_state_combined()
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
        """shutdown_sidecar calls ``child.kill_tree().await`` which routes"""
        body = _shutdown_sidecar_body()
        take_match = re.search(
            r"(?:mutex_lock\(&state\.child\)|state\.child\.lock\(\)\.unwrap\(\))\.take\(\)",
            body,
        )
        assert take_match is not None, (
            "shutdown_sidecar must take() the SidecarHandle from state.child "
            "via `mutex_lock(&state.child).take()` (CR-29) or "
            "`state.child.lock().unwrap().take()` (pre-CR-29)"
        )
        kill_match = re.search(r"child\.(?:kill_tree|kill)\(\)\.await", body)
        assert kill_match is not None, (
            "shutdown_sidecar must call child.kill().await or child.kill_tree().await "
            "which routes to SidecarHandle::kill (SIGTERM on release, SIGKILL on "
            "dev mode Linux)"
        )

    def test_linux_uses_sigterm_for_graceful_and_sigkill_for_backstop(self):
        """Linux dual-signal contract: SIGTERM for graceful kill (release"""
        src = _read_state_combined()
        # ShellPlugin variant exists (release → SIGTERM on Linux).
        assert re.search(
            r"ShellPlugin\s*\(\s*(?:Option\s*<\s*)?CommandChild(?:\s*>\s*)?\s*\)",
            src,
        ), (
            "Linux release builds must use SidecarHandle::ShellPlugin "
            "(CommandChild::kill → SIGTERM via nix::sys::signal::kill)"
        )
        # DevMode variant exists (dev → SIGKILL on Linux).
        assert re.search(r"DevMode\s*\(\s*tokio::process::Child\s*\)", src), (
            "Linux dev mode must use SidecarHandle::DevMode (tokio Child::kill → SIGKILL via libc::kill)"
        )
        # The kill method dispatches on the variant, the ShellPlugin
        assert re.search(
            r"SidecarHandle::ShellPlugin\(c\)\s*=>\s*(?:\{[^}]*?c\.kill\(\)|c\.kill\(\)|match\s+c\.take\(\))",
            src,
            re.DOTALL,
        ), "ShellPlugin arm must call c.kill() (sync → SIGTERM on Linux)"
        assert re.search(
            r"SidecarHandle::DevMode\s*\(\s*(?:mut\s+)?c\)\s*=>\s*(?:\{[^}]*?c\.kill\(\)\.await|c\.kill\(\)\.await)",
            src,
            re.DOTALL,
        ), "DevMode arm must call c.kill().await (async → SIGKILL on Linux)"

    def test_linux_runbook_documents_sigterm_backstop(self):
        """The Linux runbook Step 10 must document the cooperative"""
        src = _read(_LINUX_RUNBOOK)
        assert "Cooperative shutdown" in src, "Linux runbook must have a 'Cooperative shutdown' section (Step 10)"
        # The runbook calls out the kill_children backstop by name.
        assert "kill_children" in src, (
            "Linux runbook Step 10 must mention the 'kill_children' backstop "
            "(the host's force-kill when the cooperative shutdown handshake "
            "times out)"
        )
        # The runbook's 2-second sidecar-exit window.
        assert re.search(r"\b2\s*(?:second|sec|s)\b", src, re.IGNORECASE), (
            "Linux runbook Step 10 must mention the 2-second sidecar-exit window"
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

    def test_shutdown_wakeup_is_notify_based_not_polling(self):
        """Shutdown wakeup is Notify-based, not a 100ms poll loop."""
        supervisor_src = _read(_SUPERVISOR_RS)
        state_src = _read(_STATE_RS)

        assert "shutdown_notify.notified()" in supervisor_src, (
            "supervisor.rs must await shutdown_notify.notified() in the "
            "backoff loop, the Notify-based wakeup replaced the 100ms "
            "poll loop."
        )
        assert "shutdown_notify.notify_one()" in state_src, (
            "state.rs must call shutdown_notify.notify_one() right after "
            "the shutting_down swap, without it a supervisor mid-backoff "
            "waits out the full sleep before noticing shutdown."
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
        """PRE_RESTART_DELAY_MS = 500, delay between ``supervisor_relaunching``"""
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
        """``respawn_in_progress`` AtomicBool serializes concurrent"""
        src = _read(_SUPERVISOR_RS)
        assert "respawn_in_progress" in src, (
            "supervisor must use state.respawn_in_progress to serialize concurrent respawn attempts"
        )
        assert "compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)" in src, (
            "supervisor must acquire respawn_in_progress via compare_exchange(false → true) (atomic test-and-set)"
        )

    def test_iterates_backoff_schedule(self):
        """The supervisor iterates ``SUPERVISOR_BACKOFF_MS`` with ``enumerate()``."""
        src = _read(_SUPERVISOR_RS)
        assert "SUPERVISOR_BACKOFF_MS.iter().enumerate()" in src, (
            "supervisor must iterate SUPERVISOR_BACKOFF_MS with enumerate() so "
            "each retry sleeps for the corresponding backoff delay"
        )

    # ``test_caps_restarts_at_supervisor_max_retries`` was

    def test_respects_shutting_down_flag(self):
        """If ``state.shutting_down`` is true, the supervisor bails early"""
        src = _read(_SUPERVISOR_RS)
        assert "state.shutting_down.load(Ordering::SeqCst)" in src, (
            "supervisor must check state.shutting_down and bail early if a "
            "cooperative shutdown is in flight (don't respawn during quit)"
        )

    def test_returns_ok_on_successful_respawn(self):
        """On a successful ``reconnect_ws``, the supervisor returns"""
        src = _read(_SUPERVISOR_RS)
        # The success branch returns Ok(()).
        assert "respawn succeeded" in src, "supervisor must log 'respawn succeeded' when reconnect_ws succeeds"
        # Find the success branch and verify it returns Ok(()) inside
        idx_log = src.index("respawn succeeded")
        # Search forward from the log line for `return Ok(())`.
        idx_return = src.index("return Ok(())", idx_log)
        assert idx_return - idx_log < 3600, (
            f"`return Ok(())` after 'respawn succeeded' log must be in the "
            f"same match arm (within 3600 chars, widened for CR-29's "
            f"write_restart_counter + supervisor_reconnected emit + "
            f"respawn_in_progress clear, CR-13's flag-clear rationale "
            f"comment block, AND the spawn_blocking counter-write "
            f"routing); gap was "
            f"{idx_return - idx_log} chars, the supervisor must return "
            f"immediately on successful reconnect_ws (reset-on-success: the "
            f"loop exits early, the next crash starts a fresh backoff schedule)"
        )

    def test_emits_reconnected_event_on_success(self):
        """On successful respawn, emit ``supervisor_reconnected`` so the UI"""
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
        """each respawn rotates ``state.child_exit_rx`` so the next"""
        src = _read(_SUPERVISOR_RS)
        assert "child_exit_rx" in src, (
            "supervisor must rotate state.child_exit_rx on respawn so the "
            "next shutdown_sidecar call polls the new sidecar's exit events"
        )

    def test_has_exhaustion_relaunch_after_loop(self):
        """Defensive: if the loop exits without returning"""
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
    """Import sidecar_ws lazily so a missing ``websockets`` dep doesn't"""
    from voice_typer.server import sidecar_ws

    return sidecar_ws


class TestPythonShutdownHandler:
    """Mock-heavy tests for the Python sidecar's shutdown frame handler."""

    def _make_dispatch(self):
        """Build the dispatch closure with a real IPCServer instance."""
        sw = _import_sidecar_ws()
        from voice_typer.server.ipc_server import IPCServer

        server = IPCServer.__new__(IPCServer)
        # ``_dispatch`` acquires ``self._dispatch_lock``
        server._dispatch_lock = threading.RLock()
        # ``_handle_shutdown`` accesses ``self._shutdown_started``
        server._shutdown_started = threading.Event()
        server.app = MagicMock(name="LausuApp")
        # ``_dispatch`` checks ``app._shutting_down is True``
        server.app._shutting_down = False
        server.service = MagicMock(name="LausuService")
        server.service.quit = MagicMock(name="service.quit")
        dispatch = sw._make_dispatch(server)
        return dispatch, server

    @pytest.mark.asyncio
    async def test_shutdown_returns_ack_envelope(self):
        """The handler must return ``{\"type\":\"result\",\"data\":{\"ack\":True}}``"""
        dispatch, _server = self._make_dispatch()
        result = await dispatch({"type": "shutdown"}, websocket=MagicMock())
        assert result == {"type": "result", "data": {"ack": True}}, (
            'shutdown handler must return {"type":"result","data":'
            '{"ack":True}}, the host correlates this ack with the '
            "shutdown frame it just sent"
        )

    @pytest.mark.asyncio
    async def test_shutdown_schedules_quit_on_background_thread(self):
        """The handler schedules ``server.service.quit()`` on a daemon"""
        dispatch, server = self._make_dispatch()
        quit_called = threading.Event()

        def _fake_quit():
            quit_called.set()

        server.service.quit = _fake_quit

        result = await dispatch({"type": "shutdown"}, websocket=MagicMock())

        # Ack returned immediately (before quit runs).
        assert result == {"type": "result", "data": {"ack": True}}
        # The background thread should call service.quit() shortly.
        assert quit_called.wait(timeout=2.0), (
            "server.service.quit() must be called on the background thread within 2s of the ack being returned"
        )

    @pytest.mark.asyncio
    async def test_shutdown_ack_returns_before_quit_completes(self):
        """quit thread completes, even if quit() takes 500ms."""
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
    async def test_shutdown_does_not_swallow_quit_exceptions(self):
        """exception (not swallow it silently) so the operator can"""
        from voice_typer.server import ipc_server

        def boom():
            raise RuntimeError("quit blew up")

        with patch.object(ipc_server.log, "error") as mock_err:
            dispatch, server = self._make_dispatch()
            server.service.quit = boom
            await dispatch({"type": "shutdown"}, websocket=MagicMock())
            # Give the background daemon thread a moment to run _bg_cleanup
            time.sleep(0.3)

        # The handler's inner try/except must have logged the exception.
        assert mock_err.call_count >= 1, (
            "shutdown handler must log (via log.error) any exception from "
            "server.service.quit() so a stuck shutdown is diagnosable"
        )

    @pytest.mark.asyncio
    async def test_shutdown_envelope_is_json_serializable(self):
        """The ack envelope must round-trip through ``json.dumps`` →"""
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
        # A random non-shutdown frame, the dispatch closure will fall
        server._dispatch = MagicMock(return_value={"type": "result", "data": {}})
        # Wire a permissive rate limiter so the frame reaches _dispatch.
        limiter = MagicMock()
        limiter.allow.return_value = True
        limiter.reject = MagicMock()
        with patch("voice_typer.server.ipc_server._get_rate_limiter", return_value=limiter):
            result = await dispatch({"type": "get_state"}, websocket=MagicMock())
        # (NOT ``self.app.quit()``); a non-shutdown frame must NOT trigger it.
        assert server.service.quit.call_count == 0, "non-shutdown frames must NOT schedule server.service.quit()"
        # And the frame dispatched normally.
        assert result == {"type": "result", "data": {}}


class TestPythonShutdownReleasesMic:
    """Verify the shutdown handler delegates to ``server.app.quit()``,"""

    def test_app_quit_delegates_to_shutdown_controller(self):
        """``LausuApp.quit`` delegates to ``ShutdownController.quit``"""
        from voice_typer.server import app as app_mod

        # Source-inspection: app.quit delegates to self.shutdown.quit().
        src = Path(app_mod.__file__).read_text(encoding="utf-8")
        # The quit method exists.
        assert re.search(r"def quit\(self\):", src), (
            "LausuApp must have a quit() method (called by the WS shutdown handler via server.app.quit())"
        )
        # It delegates to self.shutdown.quit(), the ShutdownController
        assert re.search(
            r"def quit\(self\):.*?return\s+self\.shutdown\.quit\(\)",
            src,
            re.DOTALL,
        ), (
            "LausuApp.quit() must delegate to self.shutdown.quit() "
            "(ShutdownController.quit, releases mic, closes sockets, exits)"
        )

    @pytest.mark.asyncio
    async def test_shutdown_handler_calls_app_quit(self):
        """The WS shutdown handler must call ``server.service.quit()`` —"""
        dispatch, server = self._make_dispatch_with_captured_thread()
        await dispatch({"type": "shutdown"}, websocket=MagicMock())
        # The captured thread target (server.service.quit) was invoked by
        assert server.service.quit.call_count == 1, (
            "shutdown handler must call server.service.quit() exactly once, "
            "this is the path that releases the mic, closes sockets, and "
            "exits with code 0"
        )

    def _make_dispatch_with_captured_thread(self):
        """Build a dispatch closure whose background thread runs"""
        sw = _import_sidecar_ws()
        from voice_typer.server.ipc_server import IPCServer

        server = IPCServer.__new__(IPCServer)
        server._dispatch_lock = threading.RLock()
        # ``_handle_shutdown`` accesses ``self._shutdown_started``
        server._shutdown_started = threading.Event()
        server.app = MagicMock(name="LausuApp")
        server.app._shutting_down = False
        server.service = MagicMock(name="LausuService")
        server.service.quit = MagicMock(name="service.quit")

        class _SyncThread:
            def __init__(self, target=None, **kw):
                self._target = target

            def start(self):
                # Run the target synchronously so the test can assert
                if self._target is not None:
                    self._target()

            def join(self, timeout=None):  # noqa: ARG002, interpreter shutdown
                pass

        with patch("threading.Thread", _SyncThread):
            dispatch = sw._make_dispatch(server)
        return dispatch, server


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

    def test_rust_constant_documented_as_cooperative_shutdown(self):
        """The Rust ``SHUTDOWN_ACK_TIMEOUT_MS`` constant must be"""
        src = _read(_UTIL_RS)
        assert "cooperative shutdown" in src.lower() or "ADR-0020 §10" in src, (
            "SHUTDOWN_ACK_TIMEOUT_MS in src-tauri/src/util.rs must be "
            "documented as the cooperative shutdown hard timeout "
            "(ADR-0020 §10), it is the single source of truth for the "
            "cooperative-shutdown deadline."
        )


class TestRunbookCoverage:
    """Cross-check that the source actually implements what the Linux"""

    def test_linux_runbook_exists(self):
        """Guard: the Linux validation runbook must exist."""
        assert _LINUX_RUNBOOK.is_file(), f"Linux validation runbook missing: {_LINUX_RUNBOOK}"

    def test_linux_runbook_has_cooperative_shutdown_section(self):
        """Runbook Step 10 (Cooperative shutdown gate point 6) must"""
        src = _read(_LINUX_RUNBOOK)
        # The section header, the Linux runbook uses "Step 10" +
        assert "Cooperative shutdown" in src, (
            "Linux runbook must have a 'Cooperative shutdown' section (Step 10, gate point 6)"
        )
        # The pass criteria mentions the 2-second window.
        assert re.search(r"\b2\s*(?:second|sec|s)\b", src, re.IGNORECASE), (
            "Linux runbook Step 10 must mention the 2-second sidecar-exit window"
        )
        assert "ps aux" in src, (
            "Linux runbook Step 10 must use 'ps aux | grep python-sidecar' to "
            "verify the sidecar process is gone after shutdown (Linux convention)"
        )
        # The kill_children backstop is mentioned by name.
        assert "kill_children" in src, (
            "Linux runbook Step 10 must mention the 'kill_children' backstop "
            "(the host's force-kill when the cooperative shutdown handshake "
            "times out, SIGTERM on release builds, SIGKILL on dev mode)"
        )

    def test_linux_runbook_has_step_10_header(self):
        """The Linux runbook must use 'Step 10' as the cooperative"""
        src = _read(_LINUX_RUNBOOK)
        # Step 10 heading, the Linux runbook's cooperative shutdown
        assert re.search(r"##\s*Step\s*10\b", src), (
            "Linux runbook must have a '## Step 10' heading for the cooperative shutdown section (gate point 6)"
        )
        m = re.search(r"##\s*Step\s*10\b[^\n]*\n", src)
        assert m, "Step 10 heading not found"
        # Look at the heading line itself.
        heading_line = m.group(0)
        assert (
            "cooperative shutdown" in heading_line.lower()
            or "kill_children" in heading_line.lower()
            or "shutdown" in heading_line.lower()
        ), f"Step 10 heading must mention shutdown/kill_children; got: {heading_line!r}"

    def test_linux_runbook_documents_zombie_check(self):
        """Runbook Step 10 must verify no zombie ``linux-key-listener``"""
        src = _read(_LINUX_RUNBOOK)
        assert "linux-key-listener" in src, (
            "Linux runbook Step 10 must verify no zombie 'linux-key-listener' "
            "process remains after shutdown (the sidecar's child must be "
            "reaped, not orphaned)"
        )

    def test_rust_log_string_matches_runbook_intent(self):
        """The Rust log string must contain ``sidecar kill completed`` so"""
        src = _read_sidecar_cmds_module()
        assert "[SHUTDOWN]" in src, (
            "Rust shutdown logs must use the '[SHUTDOWN]' prefix so the runbook "
            "Step 10 grep (grep 'SHUTDOWN|shutdown') matches"
        )
