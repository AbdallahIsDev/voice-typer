"""MIG-1.6 Phase 0-M Gate Check 7 — cooperative shutdown validation (macOS).

This module validates the **macOS cooperative shutdown path** described in
ADR-0020 §10 and the macOS Validation Runbook §6.5 (Cooperative shutdown
gate point 6, BOTH arches — Apple Silicon + Intel):

    Rust host (shutdown_sidecar Tauri command)  [cross-platform code]
        │  1. sets state.shutting_down = true (atomic flag)
        │  2. sends {"type":"shutdown"} over the WS writer channel
        │  3. waits up to SHUTDOWN_ACK_TIMEOUT_MS (2_000ms) for
        │     CommandEvent::Terminated from the sidecar's
        │     Command::spawn() event stream
        │  4. on Terminated → graceful=true → child.kill() (no-op
        │     if already exited) → log "[SHUTDOWN] sidecar kill
        │     completed (graceful=true)"
        │  5. on timeout → child.kill() force-kill backstop →
        │     log "[SHUTDOWN] sidecar kill completed (graceful=false)"
        ▼
    Python sidecar (_make_dispatch → dispatch closure in sidecar_ws.py)
        │  1. logs "[SIDECAR-WS] shutdown received — releasing mic and exiting"
        │  2. schedules server.app.quit() on a daemon thread (so the
        │     ack is sent BEFORE quit runs — host's hard timeout is 2.0s)
        │  3. returns {"type":"result","data":{"ack":True}} immediately
        ▼
    supervisor (respawn) — crash backstop  [cross-platform code]
        │  • backoff schedule: [500, 1000, 2000, 4000, 8000] ms
        │    (ADR-0020 §10: doubling, 5 steps)
        │  • cap: SUPERVISOR_MAX_RETRIES = 5 → after 5 failed respawns,
        │    emit "supervisor_relaunching" event + app.restart() (whole-app
        │    relaunch, NOT just sidecar respawn)
        │  • respects state.shutting_down: bails early if a shutdown
        │    is in flight (don't respawn during quit)
        │  • serialized via state.respawn_in_progress (AtomicBool,
        │    compare_exchange) so a flapping sidecar can't launch
        │    two concurrent supervisors

macOS signal behavior (the only platform-specific aspect of this gate):
---------------------------------------------------------------------
The shutdown_sidecar Rust code is **cross-platform** — the same source
compiles for macOS, Linux, and Windows. The only platform-specific
behavior is in the child-kill backstop:

- **Release builds (externalBin / Nuitka-frozen .app)**: `SidecarHandle::
  ShellPlugin(tauri_plugin_shell::process::CommandChild)`. `CommandChild::
  kill(self)` on Unix (macOS + Linux) sends **SIGTERM** to the child via
  `nix::sys::signal::kill(pid, Signal::SIGTERM)`. SIGTERM is graceful:
  Python's default SIGTERM handler raises `SystemExit`, the sidecar's
  asyncio loop unwinds, mic is released, sockets close, process exits
  with code 0. This is what the runbook §6.5 pass criteria expects.

- **Dev mode (VOICE_TYPER_SIDECAR_DEV=1)**: `SidecarHandle::DevMode(
  tokio::process::Child)`. `tokio::process::Child::kill(&mut self)` on
  Unix delegates to `std::process::Child::kill` which sends **SIGKILL**
  via `libc::kill(pid, SIGKILL)`. SIGKILL is immediate (no signal
  handler runs, no cleanup); this is the dev-mode backstop when a
  developer is testing on macOS and the sidecar is hung.

So on macOS:
- The cooperative `{"type":"shutdown"}` WS frame is the FIRST graceful
  signal (process-level, no OS signal).
- If the sidecar acks + exits within 2s (the normal path on macOS),
  no OS signal is ever sent — the child is already gone when
  `child.kill()` runs (no-op on a dead pid).
- If the sidecar is hung (rare — usually a CTranslate2 native call
  blocking the WS reader), the backstop `child.kill()` fires:
  - Release build → SIGTERM (graceful, gives Python a chance to clean
    up mic/sounddevice handles).
  - Dev mode → SIGKILL (immediate).
- On macOS there is NO SIGTERM→SIGKILL escalation within a single
  `child.kill()` call (unlike systemd's `TimeoutStopSec=`). The host's
  contract is: 2s cooperative window, then a single SIGTERM (release)
  or SIGKILL (dev). The runbook §6.5 "Hard-kill backstop test" verifies
  the SIGTERM path on release builds by hanging the sidecar with lldb.

Why source-inspection + mocks (not a real Tauri runtime)?
---------------------------------------------------------
The Linux sandbox this test runs in cannot compile/run the Tauri Rust
host (`cargo tauri build` is macOS-only per the runbook Step 4), and
cannot spawn the real Nuitka-frozen `python-sidecar` `.app` binary. So:

- **Rust side**: source-inspection tests read the `.rs` files as strings
  and assert that the expected control flow, log strings, constants, and
  API calls are present. This catches regressions where a refactor
  accidentally drops the shutdown frame, the atomic flag, the
  `CommandEvent::Terminated` wait, the `child.kill()` backstop, or the
  backoff schedule.

- **Python side**: mock-heavy tests exercise the `_make_dispatch`
  closure's `shutdown` branch with a fake `IPCServer` and assert the
  ack envelope, log line, and background-thread quit scheduling —
  without binding a real WS socket.

VALIDATE ON MACOS HOST:
    1. Launch Voice Typer
    2. Quit via dock menu → "Quit"
    3. Check ~/Library/Logs/voice-typer/voice-typer.log for:
       - "[SHUTDOWN] sidecar exited gracefully (code=0)"
       - "[SHUTDOWN] sidecar kill completed (graceful=true)"
    4. Verify the sidecar process is gone: ps aux | grep python-sidecar (no results)
    5. Crash test: kill -9 python-sidecar → verify restarts it within 2s
    6. Repeat crash 5x → verify supervisor relaunches the whole app
    Expected: graceful shutdown ≤ 2s; restart ≤ 2s; relaunch after 5 crashes

References:
- ADR-0020 §10 (WS disconnect / backoff / cooperative shutdown)
- macOS Validation Runbook §6.5 (Cooperative shutdown gate, BOTH arches)
- src-tauri/src/commands/sidecar_cmds.rs (shutdown_sidecar command)
- src-tauri/src/sidecar/supervisor.rs (supervisor)
- src-tauri/src/util.rs (SUPERVISOR_BACKOFF_MS, SUPERVISOR_MAX_RETRIES,
  SHUTDOWN_ACK_TIMEOUT_MS, SHUTDOWN_POLL_INTERVAL_MS, PRE_RESTART_DELAY_MS)
- src-tauri/src/state.rs (SidecarState + SidecarHandle enum — the
  ShellPlugin vs DevMode variant is what determines SIGTERM vs SIGKILL
  on macOS)
- voice_typer/server/sidecar_ws.py (_make_dispatch shutdown branch)
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ─── Repo paths ────────────────────────────────────────────────────────

# __file__ = <repo>/tests/tauri/mig16/test_shutdown_macos.py
# parents[0]=mig16, [1]=tauri, [2]=tests, [3]=voice-typer (repo root)
_REPO_ROOT = Path(__file__).resolve().parents[3]
# Don't assert the literal repo-dir name — the repo may be cloned under any
# name (e.g. "voice-typer", "persistent-voice-typing", a fork name). Instead
# verify _REPO_ROOT actually points at the project root by checking for a
# known file. This makes the test portable across CI runners + forks.
assert (_REPO_ROOT / "pyproject.toml").is_file(), (
    f"_REPO_ROOT does not look like the voice-typer project root (no pyproject.toml found): {_REPO_ROOT}"
)
assert (_REPO_ROOT / "src-tauri" / "Cargo.toml").is_file(), (
    f"_REPO_ROOT does not look like the voice-typer project root (no src-tauri/Cargo.toml found): {_REPO_ROOT}"
)

_SIDECAR_CMDS_RS = _REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds.rs"
_SIDECAR_CMDS_DIR = _REPO_ROOT / "src-tauri" / "src" / "commands" / "sidecar_cmds"


def _read_sidecar_cmds_module() -> str:
    """Concatenate sidecar_cmds.rs + sidecar_cmds/*.rs (EO-35 split).

    EO-35 split the former single-file ``commands/sidecar_cmds.rs``
    into an orchestrator (``sidecar_cmds.rs``) + four concern
    submodules (``sidecar_cmds/allowlist.rs``, ``dispatch.rs``,
    ``shutdown.rs``, ``window_close.rs``). The shutdown-gate
    assertions target the module as a whole, so we read every file
    and join them.
    """
    files = [_SIDECAR_CMDS_RS] + sorted(_SIDECAR_CMDS_DIR.glob("*.rs"))
    return "\n\n".join(p.read_text(encoding="utf-8") for p in files)
_SUPERVISOR_RS = _REPO_ROOT / "src-tauri" / "src" / "sidecar" / "supervisor.rs"
_UTIL_RS = _REPO_ROOT / "src-tauri" / "src" / "util.rs"
_STATE_RS = _REPO_ROOT / "src-tauri" / "src" / "state.rs"
_CARGO_LOCK = _REPO_ROOT / "src-tauri" / "Cargo.lock"
_CARGO_TOML = _REPO_ROOT / "src-tauri" / "Cargo.toml"
_SIDECAR_WS_PY = _REPO_ROOT / "voice_typer" / "server" / "sidecar_ws.py"
_MACOS_RUNBOOK = _REPO_ROOT / "docs" / "migration" / "macos-validation-runbook.md"


def _read(path: Path) -> str:
    """Read a source file as a string (source-inspection helper).

    Asserts the file exists so a missing file produces a clear test
    failure rather than a confusing ImportError later.
    """
    assert path.is_file(), f"source file missing: {path}"
    return path.read_text(encoding="utf-8")


def _shutdown_sidecar_body() -> str:
    """Extract the body of `shutdown_sidecar` from sidecar_cmds.rs.

    Returns the source text of the function (from `pub async fn
    shutdown_sidecar` through the closing brace). Used by every
    source-inspection test for that function so the regex lives in one
    place (if the function signature changes, only this helper needs
    updating).
    """
    src = _read_sidecar_cmds_module()
    m = re.search(r"pub async fn shutdown_sidecar\b.*?\n\}", src, re.DOTALL)
    assert m, "shutdown_sidecar function not found in sidecar_cmds.rs"
    return m.group(0)


# ─── Rust source-inspection: shutdown_sidecar command ─────────────────


class TestShutdownSidecarSource:
    """Source-inspection tests for the Rust `shutdown_sidecar` command.

    These read `src-tauri/src/commands/sidecar_cmds.rs` as a string and
    assert that the cooperative-shutdown control flow (ADR-0020 §10) is
    present. They cannot run the Rust code (no Tauri runtime in the
    sandbox) but they catch regressions where a refactor accidentally
    drops a step of the shutdown dance.

    On macOS, this is the SAME source that compiles for Linux and
    Windows — the only platform-specific aspect is the signal sent by
    `child.kill()` (see TestMacOSSignalBehavior).
    """

    def test_source_file_exists(self):
        """Guard: the file under test must exist (catches path moves)."""
        assert _SIDECAR_CMDS_RS.is_file(), f"shutdown_sidecar source missing: {_SIDECAR_CMDS_RS}"

    def test_sets_shutting_down_atomic_flag(self):
        """Step 1: `state.shutting_down.store(true, Ordering::SeqCst)`.

        ADR-0020 §10: the flag MUST be set BEFORE sending the shutdown
        frame so the supervisor (which may see the sidecar exit
        concurrently) doesn't try to respawn mid-shutdown.
        """
        body = _shutdown_sidecar_body()
        assert "shutting_down.store(true, Ordering::SeqCst)" in body, (
            "shutdown_sidecar must set state.shutting_down = true (atomic flag) so supervisor doesn't respawn during shutdown"  # noqa: E501
        )
        # The flag set must come BEFORE the WS frame send.
        idx_flag = body.index("shutting_down.store(true")
        idx_frame = body.index('json!({"type": "shutdown"})')
        assert idx_flag < idx_frame, (
            "shutting_down flag must be set BEFORE the shutdown frame is sent "
            "(otherwise supervisor could respawn between flag-set and frame-send)"
        )

    def test_sends_shutdown_ws_frame(self):
        """Step 2: sends `{"type":"shutdown"}` via the WS writer channel.

        ADR-0020 §10: the frame is a bare `{"type":"shutdown"}` — no
        `data`, no `id` (it's fire-and-forget; the sidecar acks with
        `{"type":"result","data":{"ack":true}}` but the host doesn't
        correlate via id, it just waits for process exit).

        On macOS this frame is sent over the same tokio_tungstenite WS
        channel as on Linux/Windows — cross-platform code.
        """
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
        """Step 3: waits for `CommandEvent::Terminated` with
        `SHUTDOWN_ACK_TIMEOUT_MS` deadline via `tokio::time::timeout`.

        the host polls the sidecar's exit event stream (captured
        at spawn time) and returns as soon as `Terminated` arrives
        (~50ms typical), instead of sleeping the full 2s unconditionally.
        """
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
        """Step 4/5: force-kills the child via `child.kill()` as backstop.

        ADR-0020 §10: the kill is a no-op if the child already exited
        (graceful path) but guarantees no zombie if the sidecar is stuck
        inside a native CTranslate2 call and cannot service the WS frame.

        On macOS this is the SIGTERM (release) / SIGKILL (dev) path —
        see TestMacOSSignalBehavior for the platform-specific signal
        verification.
        """
        body = _shutdown_sidecar_body()
        # Takes the child out of the Option (single-use after kill).
        assert "state.child.lock().unwrap().take()" in body, (
            "shutdown_sidecar must take() the child handle (single-use after kill)"
        )
        # Calls .kill().await on the child.
        assert "child.kill().await" in body, (
            "shutdown_sidecar must call child.kill().await as the force-kill "
            "backstop (no-op if already exited, guarantees no zombie)"
        )
        # The kill is reached on BOTH paths (graceful + timeout) — verify
        # the kill call is NOT inside an `if`/`else` that only fires on
        # one branch. We check it appears after the wait block closes.
        # The `drop(rx_guard)` line marks the end of the wait block.
        idx_drop = body.index("drop(rx_guard)")
        idx_kill = body.index("child.kill().await")
        assert idx_kill > idx_drop, (
            "child.kill() must run AFTER the wait block (drop(rx_guard)) so it "
            "fires on both the graceful-exit and timeout paths"
        )

    def test_logs_graceful_and_force_kill_outcomes(self):
        """Both outcomes produce a `[SHUTDOWN]` log line for runbook §6.5.

        The macOS validation runbook §6.5 greps the host log for
        `[SHUTDOWN]` lines to verify the cooperative shutdown path fired.
        The force-kill completion log includes `graceful=true|false` so
        the operator can tell from the log alone whether the sidecar
        acked+exited or had to be killed.
        """
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
        """Dev-mode sidecars (tokio::process::Child) have no
        CommandEvent stream → fall back to bounded sleep polling.

        ADR-0020 §1: the DevMode variant is used when
        `VOICE_TYPER_SIDECAR_DEV=1` runs `python -m ...ipc_server`
        directly (no externalBin). The shutdown path must still work
        for dev mode — it just can't poll Terminated, so it sleeps in
        SHUTDOWN_POLL_INTERVAL_MS increments up to the deadline.

        On macOS this is the variant that uses SIGKILL (via
        tokio::process::Child::kill) instead of SIGTERM.
        """
        body = _shutdown_sidecar_body()
        assert "SHUTDOWN_POLL_INTERVAL_MS" in body, (
            "shutdown_sidecar must use SHUTDOWN_POLL_INTERVAL_MS for the "
            "dev-mode bounded-sleep fallback (no CommandEvent stream available)"
        )
        assert "dev-mode" in body.lower(), (
            "shutdown_sidecar must have an explicit dev-mode fallback branch "
            "(tokio::process::Child has no CommandEvent receiver)"
        )


# ─── macOS signal behavior: SidecarHandle variant inspection ──────────


class TestMacOSSignalBehavior:
    """macOS-specific signal verification via source inspection.

    The `shutdown_sidecar` Rust code is cross-platform — the same source
    compiles for macOS, Linux, and Windows. The ONLY platform-specific
    aspect is what OS signal `child.kill()` sends:

    - **Release (externalBin / Nuitka .app)**: `SidecarHandle::ShellPlugin(
      CommandChild)` → `CommandChild::kill()` sends **SIGTERM** on Unix
      (macOS + Linux) via `nix::sys::signal::kill(pid, SIGTERM)`.
      SIGTERM is graceful: Python's default handler raises SystemExit,
      the sidecar releases the mic, closes sockets, exits with code 0.
      This is the path the macOS runbook §6.5 verifies.

    - **Dev mode (VOICE_TYPER_SIDECAR_DEV=1)**: `SidecarHandle::DevMode(
      tokio::process::Child)` → `Child::kill()` sends **SIGKILL** on Unix
      via `libc::kill(pid, SIGKILL)` (through `std::process::Child::kill`).
      SIGKILL is immediate (no signal handler runs); this is the dev-mode
      backstop when the sidecar is hung during local development.

    We can't run the Tauri runtime in the sandbox, so we inspect the
    `SidecarHandle` enum + its `kill()` impl + the Cargo.lock pin on
    tauri-plugin-shell to verify the signal behavior is what we expect.
    """

    def test_state_source_file_exists(self):
        """Guard: state.rs must exist (holds SidecarHandle enum)."""
        assert _STATE_RS.is_file(), f"state.rs missing: {_STATE_RS}"

    def test_sidecar_handle_has_shell_plugin_variant(self):
        """`SidecarHandle::ShellPlugin(CommandChild)` variant exists for
        release builds (externalBin / Nuitka .app on macOS).

        `tauri_plugin_shell::process::CommandChild` is the handle
        returned by `Command::spawn()`; its `kill(self)` method sends
        SIGTERM on Unix (macOS + Linux). This is the variant the macOS
        runbook §6.5 exercises.
        """
        src = _read(_STATE_RS)
        assert re.search(r"ShellPlugin\s*\(\s*CommandChild\s*\)", src), (
            "SidecarHandle must have a ShellPlugin(CommandChild) variant for "
            "release builds (CommandChild::kill sends SIGTERM on macOS)"
        )

    def test_sidecar_handle_has_dev_mode_variant(self):
        """`SidecarHandle::DevMode(tokio::process::Child)` variant exists
        for dev mode (VOICE_TYPER_SIDECAR_DEV=1).

        `tokio::process::Child::kill(&mut self)` delegates to
        `std::process::Child::kill` which sends SIGKILL on Unix. This is
        the dev-mode backstop signal on macOS.
        """
        src = _read(_STATE_RS)
        assert re.search(r"DevMode\s*\(\s*tokio::process::Child\s*\)", src), (
            "SidecarHandle must have a DevMode(tokio::process::Child) variant "
            "for dev mode (tokio Child::kill sends SIGKILL on macOS)"
        )

    def test_kill_method_matches_on_both_variants(self):
        """`SidecarHandle::kill(self)` matches on both variants and
        delegates to the variant's `kill()` method.

        On macOS:
        - ShellPlugin arm → `c.kill()` (CommandChild::kill → SIGTERM)
        - DevMode arm     → `c.kill().await` (tokio Child::kill → SIGKILL)
        """
        src = _read(_STATE_RS)
        # The kill method exists and is async (returns io::Result<()>).
        assert re.search(
            r"pub\(crate\)\s+async\s+fn\s+kill\s*\(\s*self\s*\)\s*->\s*std::io::Result<\(\)>",
            src,
        ), "SidecarHandle must have an async kill(self) -> io::Result<()> method"
        # Both arms call .kill() on the inner handle.
        assert "SidecarHandle::ShellPlugin(c)" in src, (
            "kill() must match SidecarHandle::ShellPlugin(c) and call c.kill()"
        )
        assert "SidecarHandle::DevMode(mut c)" in src, (
            "kill() must match SidecarHandle::DevMode(mut c) and call c.kill().await"
        )
        # ShellPlugin arm calls c.kill() (sync — CommandChild::kill is sync).
        # DevMode arm calls c.kill().await (async — tokio Child::kill is async).
        # Both arms may use either an inline expression (`=> c.kill()...`)
        # or a block (`=> { c.kill()... }`); accept both forms.
        m_shell = re.search(
            r"SidecarHandle::ShellPlugin\(c\)\s*=>\s*(?:\{[^}]*?c\.kill\(\)|c\.kill\(\))",
            src,
            re.DOTALL,
        )
        assert m_shell, "ShellPlugin arm must call c.kill() (CommandChild::kill is sync, sends SIGTERM on macOS)"
        m_dev = re.search(
            r"SidecarHandle::DevMode\(mut c\)\s*=>\s*(?:\{[^}]*?c\.kill\(\)\.await|c\.kill\(\)\.await)",
            src,
            re.DOTALL,
        )
        assert m_dev, "DevMode arm must call c.kill().await (tokio Child::kill is async, sends SIGKILL on macOS)"

    def test_tauri_plugin_shell_version_is_pinned(self):
        """Cargo.lock pins tauri-plugin-shell to a 2.x release so the
        SIGTERM-on-Unix behavior is locked.

        tauri-plugin-shell 2.x's `CommandChild::kill()` on Unix sends
        `Signal::SIGTERM` via `nix::sys::signal::kill`. If the version
        ever changes to one that uses SIGKILL (or TerminateProcess on
        macOS, which doesn't exist), this test will flag the drift.
        """
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
            f"series — CommandChild::kill sends SIGTERM on Unix); got {major}"
        )

    def test_tauri_plugin_shell_declared_in_cargo_toml(self):
        """Cargo.toml declares tauri-plugin-shell as a dependency (so a
        `cargo update` can't silently remove it)."""
        toml = _read(_CARGO_TOML)
        assert re.search(r"^tauri-plugin-shell\s*=", toml, re.MULTILINE), (
            "tauri-plugin-shell must be declared in Cargo.toml [dependencies] "
            "(provides CommandChild used by SidecarHandle::ShellPlugin on macOS)"
        )

    def test_state_docstring_documents_cross_platform_kill(self):
        """state.rs's SidecarHandle docstring must mention BOTH variants
        so a future maintainer understands the macOS signal behavior
        (SIGTERM release vs SIGKILL dev) without spelunking through
        tauri-plugin-shell source."""
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
        """shutdown_sidecar calls `child.kill().await` which routes
        through `SidecarHandle::kill(self)` — the method that matches
        on ShellPlugin vs DevMode and sends SIGTERM vs SIGKILL on macOS.

        This is the link between the cross-platform shutdown_sidecar
        code and the macOS-specific signal behavior.
        """
        body = _shutdown_sidecar_body()
        # Takes the Option<SidecarHandle> and calls .kill().await on it.
        assert "state.child.lock().unwrap().take()" in body, (
            "shutdown_sidecar must take() the SidecarHandle from state.child"
        )
        assert "child.kill().await" in body, (
            "shutdown_sidecar must call child.kill().await which routes to "
            "SidecarHandle::kill (SIGTERM on release, SIGKILL on dev mode macOS)"
        )


# ─── Rust source-inspection: constants ────────────────────────────────


class TestShutdownConstants:
    """Constants that govern the shutdown + supervisor dance (ADR-0020 §10).

    Pinning these as tests catches a regression where someone tweaks a
    constant without updating the runbook (or vice versa).

    These constants are cross-platform — same values on macOS, Linux,
    and Windows.
    """

    def test_shutdown_ack_timeout_is_2000ms(self):
        """SHUTDOWN_ACK_TIMEOUT_MS = 2000 (2s graceful window).

        ADR-0020 §10 + macOS runbook §6.5 pass criteria: sidecar must
        exit within 2s of the shutdown frame. Force-kill fires after
        this.
        """
        src = _read(_UTIL_RS)
        m = re.search(
            r"pub\(crate\)\s+const\s+SHUTDOWN_ACK_TIMEOUT_MS\s*:\s*u64\s*=\s*(\d+)",
            src,
        )
        assert m, "SHUTDOWN_ACK_TIMEOUT_MS constant not found in util.rs"
        assert int(m.group(1)) == 2000, (
            f"SHUTDOWN_ACK_TIMEOUT_MS must be 2000 (2s graceful window per ADR-0020 §10), got {m.group(1)}"
        )

    def test_shutdown_poll_interval_is_100ms(self):
        """SHUTDOWN_POLL_INTERVAL_MS = 100 (dev-mode fallback step)."""
        src = _read(_UTIL_RS)
        m = re.search(
            r"pub\(crate\)\s+const\s+SHUTDOWN_POLL_INTERVAL_MS\s*:\s*u64\s*=\s*(\d+)",
            src,
        )
        assert m, "SHUTDOWN_POLL_INTERVAL_MS constant not found in util.rs"
        assert int(m.group(1)) == 100, (
            f"SHUTDOWN_POLL_INTERVAL_MS must be 100 (dev-mode fallback step), got {m.group(1)}"
        )

    def test_supervisor_backoff_schedule_is_doubling_5_steps(self):
        """SUPERVISOR_BACKOFF_MS = [500, 1000, 2000, 4000, 8000].

        ADR-0020 §10: backoff 500ms → 1s → 2s → 4s → 8s, 5 steps
        total. (The ADR's prose summary "500ms → 1s → 2s (cap 5
        retries)" lists the first three steps + the cap; the full
        doubling schedule is implemented in util.rs.)
        """
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
        """SUPERVISOR_MAX_RETRIES = 5 → after 5 failed respawns, full-app relaunch."""
        src = _read(_UTIL_RS)
        m = re.search(
            r"pub\(crate\)\s+const\s+SUPERVISOR_MAX_RETRIES\s*:\s*u32\s*=\s*(\d+)",
            src,
        )
        assert m, "SUPERVISOR_MAX_RETRIES constant not found in util.rs"
        assert int(m.group(1)) == 5, (
            f"SUPERVISOR_MAX_RETRIES must be 5 (then full-app relaunch per ADR-0020 §10), got {m.group(1)}"
        )

    def test_pre_restart_delay_is_500ms(self):
        """PRE_RESTART_DELAY_MS = 500 — delay between `supervisor_relaunching`
        event and `app.restart()` so the webview can render the banner.
        """
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
        """SUPERVISOR_BACKOFF_MS.len() == SUPERVISOR_MAX_RETRIES so the loop iterates
        exactly N times before falling back to app.restart().

        If these drift apart (e.g., someone adds a 6th backoff step but
        forgets to bump SUPERVISOR_MAX_RETRIES), the supervisor would either
        never reach the relaunch path or skip backoff steps.
        """
        src = _read(_UTIL_RS)
        sched_m = re.search(
            r"pub\(crate\)\s+const\s+SUPERVISOR_BACKOFF_MS\s*:\s*&\[u64\]\s*=\s*&\[(.*?)\]",
            src,
        )
        cap_m = re.search(
            r"pub\(crate\)\s+const\s+SUPERVISOR_MAX_RETRIES\s*:\s*u32\s*=\s*(\d+)",
            src,
        )
        assert sched_m and cap_m
        steps = [int(x.strip()) for x in sched_m.group(1).split(",")]
        cap = int(cap_m.group(1))
        assert len(steps) == cap, (
            f"SUPERVISOR_BACKOFF_MS.len() ({len(steps)}) must equal SUPERVISOR_MAX_RETRIES "
            f"({cap}) so the supervisor loop iterates exactly N times before "
            f"falling back to app.restart()"
        )


# ─── Rust source-inspection: supervisor ──────────────────────────


class TestSupervisorSource:
    """Source-inspection tests for the supervisor (supervisor.rs).

    ADR-0020 §10: the supervisor is called on every unexpected sidecar
    exit (WS close without `{"type":"shutdown"}` frame). It retries
    respawn with backoff, caps at 5 retries, then falls back to a
    full-app relaunch (`app.restart()`).

    Cross-platform — same source on macOS, Linux, Windows.
    """

    def test_source_file_exists(self):
        assert _SUPERVISOR_RS.is_file(), f"supervisor source missing: {_SUPERVISOR_RS}"

    def test_respawn_is_serialized_via_atomic_flag(self):
        """`respawn_in_progress` AtomicBool serializes concurrent
        supervisors (a flapping sidecar could otherwise launch two).

        Uses `compare_exchange(false → true)` on entry; cleared on every
        exit path (Ok + restart, though restart is `-> !` so the clear
        is unreachable but harmless).
        """
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
        """After SUPERVISOR_MAX_RETRIES (5) attempts, emit `supervisor_relaunching`
        and call `app.restart()` (full-app relaunch, NOT just sidecar
        respawn).

        ADR-0020 §10: "backoff 500ms → 1s → 2s (cap 5 retries) then
        full-app relaunch". The cap check is `attempt as u32 >=
        SUPERVISOR_MAX_RETRIES` at the TOP of the loop body, so the 6th
        iteration (attempt index 5) triggers the relaunch instead of
        a 6th respawn.
        """
        src = _read(_SUPERVISOR_RS)
        # The cap check.
        assert "attempt as u32 >= SUPERVISOR_MAX_RETRIES" in src, (
            "supervisor must cap retries at SUPERVISOR_MAX_RETRIES via `attempt as u32 >= SUPERVISOR_MAX_RETRIES`"
        )
        # Emits the relaunch event so the UI can show a banner.
        assert 'app.emit("supervisor_relaunching"' in src, (
            "supervisor must emit a 'supervisor_relaunching' Tauri event before "
            "app.restart() so the UI can render a 'restarting…' banner"
        )
        # Calls app.restart() (the whole-app relaunch).
        assert "app.restart()" in src, (
            "supervisor must call app.restart() (full-app relaunch) after "
            "exhausting SUPERVISOR_MAX_RETRIES — NOT just another sidecar respawn"
        )
        # The relaunch path includes the banner-render delay.
        assert "PRE_RESTART_DELAY_MS" in src, (
            "supervisor must sleep PRE_RESTART_DELAY_MS before app.restart() "
            "so the webview has time to render the 'restarting…' banner"
        )

    def test_respects_shutting_down_flag(self):
        """If `state.shutting_down` is true, the supervisor bails early
        (don't respawn during a cooperative shutdown)."""
        src = _read(_SUPERVISOR_RS)
        assert "state.shutting_down.load(Ordering::SeqCst)" in src, (
            "supervisor must check state.shutting_down and bail early if a "
            "cooperative shutdown is in flight (don't respawn during quit)"
        )

    def test_returns_ok_on_successful_respawn(self):
        """On a successful `reconnect_ws`, the supervisor returns
        `Ok(())` immediately — the loop does NOT continue to the next
        backoff step.

        This is the "reset on success" behavior: each `respawn`
        call starts a fresh backoff schedule (the `attempt` counter is
        local to the call). A successful respawn on attempt 1 means
        the next crash (which invokes `respawn` anew) starts at
        500ms again — the previous crashes don't accumulate.
        """
        src = _read(_SUPERVISOR_RS)
        # The success branch returns Ok(()).
        assert "respawn succeeded" in src, "supervisor must log 'respawn succeeded' when reconnect_ws succeeds"
        # Find the success branch and verify it returns Ok(()) inside
        # the loop (not after it). We locate the "respawn succeeded"
        # log line, then assert a `return Ok(())` appears shortly
        # after it (within the same Ok(()) match arm).
        idx_log = src.index("respawn succeeded")
        # Search forward from the log line for `return Ok(())`.
        idx_return = src.index("return Ok(())", idx_log)
        # The return must be within ~400 chars of the log line (same
        # match arm, not a distant return elsewhere in the file).
        assert idx_return - idx_log < 400, (
            f"`return Ok(())` after 'respawn succeeded' log must be in the "
            f"same match arm (within 400 chars); gap was "
            f"{idx_return - idx_log} chars — the supervisor must return "
            f"immediately on successful reconnect_ws (reset-on-success: the "
            f"loop exits early, the next crash starts a fresh backoff schedule)"
        )

    def test_emits_reconnected_event_on_success(self):
        """On successful respawn, emit `supervisor_reconnected` so the UI
        clears its 'reconnecting…' banner."""
        src = _read(_SUPERVISOR_RS)
        assert 'app.emit("supervisor_reconnected"' in src, (
            "supervisor must emit a 'supervisor_reconnected' Tauri event on "
            "successful respawn so the UI can clear its 'reconnecting…' banner"
        )

    def test_rotates_token_on_respawn(self):
        """Each respawn generates a fresh token (per ADR-0020 §3) so a
        compromised sidecar can't replay the old token."""
        src = _read(_SUPERVISOR_RS)
        assert "generate_token()" in src, (
            "supervisor must call generate_token() on each respawn to "
            "rotate the bearer token (ADR-0020 §3: per-respawn token rotation)"
        )

    def test_rotates_child_exit_rx_on_respawn(self):
        """each respawn rotates `state.child_exit_rx` so the next
        `shutdown_sidecar` call polls the NEW sidecar's exit event
        stream (not the dead one's)."""
        src = _read(_SUPERVISOR_RS)
        assert "child_exit_rx" in src, (
            "supervisor must rotate state.child_exit_rx on respawn so the "
            "next shutdown_sidecar call polls the new sidecar's exit events"
        )

    def test_has_exhaustion_relaunch_after_loop(self):
        """Defensive: if the loop exits without returning (SUPERVISOR_BACKOFF_MS
        shorter than SUPERVISOR_MAX_RETRIES — currently impossible because they
        are pinned equal, but the guard exists), fall back to
        `app.restart()` with a `backoff_exhausted` reason."""
        src = _read(_SUPERVISOR_RS)
        assert "backoff_exhausted" in src, (
            "supervisor must have a post-loop exhaustion path that emits "
            "'supervisor_relaunching' with reason 'backoff_exhausted' and calls "
            "app.restart() (defensive guard if the schedule shrinks below "
            "SUPERVISOR_MAX_RETRIES)"
        )


# ─── Rust source-inspection: SidecarState fields ──────────────────────


class TestSidecarStateFields:
    """SidecarState must expose the fields the shutdown path reads/writes.

    Pins the field names so a refactor can't silently rename
    `shutting_down` → `is_shutting_down` and break the shutdown command.
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


# ─── Python side: _make_dispatch shutdown branch (mock-heavy) ─────────


def _import_sidecar_ws():
    """Import sidecar_ws lazily so a missing `websockets` dep doesn't
    break collection of these tests (the module lazy-imports websockets
    inside `run()`, so top-level import is safe)."""
    from voice_typer.server import sidecar_ws

    return sidecar_ws


class TestPythonShutdownHandler:
    """Mock-heavy tests for the Python sidecar's shutdown frame handler.

    The handler lives in `sidecar_ws._make_dispatch(server)` → the
    returned `dispatch(msg, websocket)` closure. The `shutdown` branch:

    1. Logs `[SIDECAR-WS] shutdown received — releasing mic and exiting`.
    2. Schedules `server.app.quit()` on a daemon thread (so the ack is
       sent BEFORE quit runs — host's hard timeout is 2.0s).
    3. Returns `{"type":"result","data":{"ack":True}}` immediately.

    These tests construct a fake `IPCServer` (MagicMock) and call the
    closure directly with a `{"type":"shutdown"}` frame.

    On macOS, this is the SAME Python code as on Linux/Windows — the
    sidecar doesn't know what platform it's running on. The only
    platform difference is what OS signal arrives if the host has to
    force-kill (SIGTERM on release, SIGKILL on dev — see
    TestMacOSSignalBehavior).
    """

    def _make_dispatch(self):
        """Build the dispatch closure with a fake server.

        Returns `(dispatch, server)` so tests can assert on the server
        mock after invoking the handler.
        """
        sw = _import_sidecar_ws()
        server = MagicMock(name="IPCServer")
        # The shutdown branch calls server.app.quit() — give it a
        # MagicMock so the call is recorded.
        server.app.quit = MagicMock(name="server.app.quit")
        dispatch = sw._make_dispatch(server)
        return dispatch, server

    @pytest.mark.asyncio
    async def test_shutdown_returns_ack_envelope(self):
        """The handler must return `{"type":"result","data":{"ack":True}}`
        so the Rust host's WS reader sees a well-formed ack frame.

        ADR-0020 §10: the sidecar acks with `{"type":"result",
        "data":{"ack":true}}` BEFORE releasing the mic + exiting, so the
        host knows the shutdown frame was received even if the process
        exit takes a few hundred ms (mic release, etc.).
        """
        dispatch, _server = self._make_dispatch()
        result = await dispatch({"type": "shutdown"}, websocket=MagicMock())
        assert result == {"type": "result", "data": {"ack": True}}, (
            'shutdown handler must return {"type":"result","data":'
            '{"ack":True}} — the host correlates this ack with the '
            "shutdown frame it just sent"
        )

    @pytest.mark.asyncio
    async def test_shutdown_logs_release_mic_message(self, caplog):
        """The handler logs `[SIDECAR-WS] shutdown received — releasing
        mic and exiting` so runbook §6.5 can grep the sidecar log."""
        _import_sidecar_ws()
        with caplog.at_level("INFO", logger="voice_typer.server.sidecar_ws"):
            dispatch, _server = self._make_dispatch()
            await dispatch({"type": "shutdown"}, websocket=MagicMock())
        joined = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "shutdown received — releasing mic and exiting" in joined, (
            "shutdown handler must log '[SIDECAR-WS] shutdown received — "
            "releasing mic and exiting' (runbook §6.5 sidecar.log verification)"
        )

    @pytest.mark.asyncio
    async def test_shutdown_schedules_quit_on_background_thread(self):
        """The handler schedules `server.app.quit()` on a daemon thread
        so the ack is returned BEFORE quit runs (the host's hard timeout
        is 2.0s; if quit blocked the WS reader, the host would force-kill
        before the ack landed).

        We patch `threading.Thread` (the module-level import the closure
        does `import threading` against) to capture the target + kwargs
        without actually spawning a thread, then invoke the captured
        target and verify `server.app.quit()` was called.
        """
        dispatch, server = self._make_dispatch()
        captured: dict = {}

        class FakeThread:
            def __init__(self, target=None, name=None, daemon=None, **kw):
                captured["target"] = target
                captured["name"] = name
                captured["daemon"] = daemon

            def start(self):
                # Don't actually start — the test invokes target()
                # synchronously to verify quit() is called.
                captured["started"] = True

        with patch("threading.Thread", FakeThread):
            result = await dispatch({"type": "shutdown"}, websocket=MagicMock())

        # Ack returned immediately (before quit runs).
        assert result == {"type": "result", "data": {"ack": True}}
        # Thread was configured as a daemon (so it doesn't block process
        # exit if quit hangs).
        assert captured.get("daemon") is True, (
            "shutdown thread must be a daemon so a hung quit() doesn't block "
            "process exit (the host's force-kill backstop will reap it)"
        )
        # Thread name is stable for log grepping.
        assert captured.get("name") == "sidecar-shutdown", (
            "shutdown thread must be named 'sidecar-shutdown' for log/metric attribution"
        )
        # Target was captured + started.
        assert captured.get("target") is not None
        assert captured.get("started") is True
        # quit() NOT called yet (it's scheduled on the thread, which we
        # didn't actually start).
        assert server.app.quit.call_count == 0, (
            "server.app.quit() must NOT be called inline — it must be "
            "scheduled on the background thread so the ack returns first"
        )
        # Now invoke the captured target and verify quit() runs.
        captured["target"]()
        assert server.app.quit.call_count == 1, "the background-thread target must call server.app.quit() exactly once"

    @pytest.mark.asyncio
    async def test_shutdown_ack_returns_before_quit_completes(self):
        """End-to-end timing assertion: the ack is returned BEFORE the
        quit thread completes, even if quit() takes 500ms.

        This is the core invariant of the cooperative shutdown: the host
        gets its ack fast (so it knows the frame landed), then waits for
        the process to exit (typically ~50ms after quit). If the ack
        were blocked on quit(), a slow mic release could push the ack
        past the host's 2s force-kill deadline.
        """
        quit_started = threading.Event()
        quit_can_finish = threading.Event()

        def slow_quit():
            quit_started.set()
            # Block until the test releases us — simulates a slow mic
            # release / model unload.
            quit_can_finish.wait(timeout=5.0)

        dispatch, server = self._make_dispatch()
        server.app.quit = slow_quit

        # Use the REAL threading.Thread (not patched) so the timing
        # assertion is meaningful.
        t0 = time.monotonic()
        result = await dispatch({"type": "shutdown"}, websocket=MagicMock())
        ack_latency = time.monotonic() - t0

        # Ack must return in well under the host's 2s force-kill window,
        # even though quit() is still running.
        assert result == {"type": "result", "data": {"ack": True}}
        assert ack_latency < 1.0, (
            f"ack must return before quit() completes (got ack in "
            f"{ack_latency * 1000:.1f}ms; expected <1000ms even with a slow quit)"
        )
        # quit() must have started (on the background thread).
        assert quit_started.wait(timeout=2.0), "background quit thread must have started after the ack returned"
        # Release the slow quit so the test doesn't hang.
        quit_can_finish.set()

    @pytest.mark.asyncio
    async def test_shutdown_does_not_swallow_quit_exceptions(self):
        """If `server.app.quit()` raises, the handler must log the
        exception (not swallow it silently) so the operator can diagnose
        a stuck shutdown."""
        sw = _import_sidecar_ws()
        captured: dict = {}

        class FakeThread:
            def __init__(self, target=None, **kw):
                captured["target"] = target

            def start(self):
                pass

        def boom():
            raise RuntimeError("quit blew up")

        with patch("threading.Thread", FakeThread), patch.object(sw.log, "exception") as mock_exc:
            dispatch, server = self._make_dispatch()
            server.app.quit = boom
            await dispatch({"type": "shutdown"}, websocket=MagicMock())
            # Invoke the captured target — this is what the real thread
            # would do.
            captured["target"]()

        # The handler's inner try/except must have logged the exception.
        assert mock_exc.call_count >= 1, (
            "shutdown handler must log (via log.exception) any exception from "
            "server.app.quit() so a stuck shutdown is diagnosable"
        )

    @pytest.mark.asyncio
    async def test_shutdown_does_not_hit_rate_limiter(self):
        """The shutdown branch returns BEFORE the rate-limiter check,
        so a sidecar that's being spammed with frames (and is over the
        200-burst budget) can still shut down cleanly.

        ADR-0020 §10 + §9: shutdown is a control frame, not a dispatch
        frame — it must bypass the ADR-0019 rate limiter.
        """
        dispatch, server = self._make_dispatch()
        # Wire a rate limiter that always rejects — if the shutdown
        # branch hits it, the test will see an error envelope instead
        # of an ack.
        limiter = MagicMock()
        limiter.allow.return_value = False
        limiter.reject = MagicMock()
        # The handler looks up the limiter via _get_rate_limiter(server).
        with patch("voice_typer.server.ipc_server._get_rate_limiter", return_value=limiter) as mock_get:
            result = await dispatch({"type": "shutdown"}, websocket=MagicMock())
        assert result == {"type": "result", "data": {"ack": True}}, (
            "shutdown must bypass the rate limiter (it's a control frame, not "
            "a dispatch frame) — got an error envelope instead of an ack"
        )
        assert mock_get.call_count == 0, (
            "shutdown handler must NOT call _get_rate_limiter at all — it returns before the rate-limit check"
        )
        assert limiter.allow.call_count == 0
        assert limiter.reject.call_count == 0

    @pytest.mark.asyncio
    async def test_shutdown_envelope_is_json_serializable(self):
        """The ack envelope must round-trip through `json.dumps` →
        `json.loads` cleanly (the host parses it as a WS Text frame)."""
        dispatch, _server = self._make_dispatch()
        result = await dispatch({"type": "shutdown"}, websocket=MagicMock())
        # Round-trip.
        wire = json.dumps(result)
        parsed = json.loads(wire)
        assert parsed == result
        # The `ack` value must serialize to JSON `true` (Python True →
        # JSON true), not the string "True".
        assert '"ack": true' in wire, f"ack must serialize to JSON true, got wire form: {wire}"

    @pytest.mark.asyncio
    async def test_non_shutdown_frame_does_not_trigger_quit(self):
        """Sanity: a non-shutdown frame must NOT schedule quit()."""
        dispatch, server = self._make_dispatch()
        # A random non-shutdown frame — the dispatch closure will fall
        # through to the rate-limit + server._dispatch path. We mock
        # _dispatch to return a benign envelope so the closure returns
        # without raising.
        server._dispatch = MagicMock(return_value={"type": "result", "data": {}})
        # Wire a permissive rate limiter so the frame reaches _dispatch.
        limiter = MagicMock()
        limiter.allow.return_value = True
        limiter.reject = MagicMock()
        with patch("voice_typer.server.ipc_server._get_rate_limiter", return_value=limiter):
            result = await dispatch({"type": "get_state"}, websocket=MagicMock())
        # quit() never scheduled.
        assert server.app.quit.call_count == 0, "non-shutdown frames must NOT schedule server.app.quit()"
        # And the frame dispatched normally.
        assert result == {"type": "result", "data": {}}


# ─── Cooperative shutdown hard timeout (ADR-0020 §10) ────────────────


class TestShutdownAckTimeoutConstant:
    """ADR-0020 §10: the cooperative-shutdown hard timeout is defined
    in the Rust host as ``SHUTDOWN_ACK_TIMEOUT_MS = 2000``
    (``src-tauri/src/util.rs``) — the single source of truth.

    DT-54: the previous Python-side ``_SHUTDOWN_ACK_TIMEOUT_SECONDS = 2.0``
    constant in ``sidecar_ws.py`` was dead code — Python never enforced
    the timeout (it just acked ``{"type":"shutdown"}`` and exited; the
    Rust host's kill-children backstop is what enforces the 2s window).
    The constant was deleted to avoid misleading readers into thinking
    Python enforces it. These tests now pin the Rust constant as the
    canonical source of truth.
    """

    def test_rust_shutdown_ack_timeout_ms_is_2000(self):
        """The Rust host's ``SHUTDOWN_ACK_TIMEOUT_MS`` constant in
        ``src-tauri/src/util.rs`` must equal 2000 (2s graceful window)."""
        src = _read(_UTIL_RS)
        const_re = re.compile(
            r"SHUTDOWN_ACK_TIMEOUT_MS\s*:\s*u64\s*=\s*2000\s*;",
            re.MULTILINE,
        )
        assert const_re.search(src), (
            "src-tauri/src/util.rs must define "
            "SHUTDOWN_ACK_TIMEOUT_MS: u64 = 2000 (ADR-0020 §10: 2s "
            "cooperative-shutdown hard timeout — the Rust host's "
            "kill-children backstop fires after this window)."
        )

    def test_python_sidecar_does_not_define_dead_timeout_constant(self):
        """DT-54: ``sidecar_ws.py`` must NOT define the dead
        ``_SHUTDOWN_ACK_TIMEOUT_SECONDS`` constant — Python never
        enforced the timeout; the Rust host's
        ``SHUTDOWN_ACK_TIMEOUT_MS`` is the single source of truth.
        """
        src = _read(_SIDECAR_WS_PY)
        const_re = re.compile(
            r"^\s*_SHUTDOWN_ACK_TIMEOUT_SECONDS\s*=\s*",
            re.MULTILINE,
        )
        assert not const_re.search(src), (
            "DT-54: sidecar_ws.py must NOT define the dead "
            "_SHUTDOWN_ACK_TIMEOUT_SECONDS constant — Python never "
            "enforced the cooperative-shutdown timeout (the Rust host's "
            "SHUTDOWN_ACK_TIMEOUT_MS in src-tauri/src/util.rs is the "
            "single source of truth)."
        )


# ─── Runbook §6.5 source coverage ─────────────────────────────────────


class TestRunbookCoverage:
    """Cross-check that the source actually implements what the macOS
    Validation Runbook §6.5 says the operator should observe in the logs.

    Runbook §6.5 pass criteria:
    - `[SHUTDOWN]` log lines appear in voice-typer.log
    - `[SIDECAR-WS] shutdown received` appears in sidecar.log
    - `pgrep -lf python-sidecar` returns nothing within 2s of window close
    - Hard-kill backstop test (lldb-hung sidecar) → force-kill cleans it
    """

    def test_macos_runbook_exists(self):
        """Guard: the macOS validation runbook must exist."""
        assert _MACOS_RUNBOOK.is_file(), f"macOS validation runbook missing: {_MACOS_RUNBOOK}"

    def test_macos_runbook_has_cooperative_shutdown_section(self):
        """Runbook §6.5 (Cooperative shutdown gate) must exist with the
        expected pass criteria (≤ 2s sidecar exit + force-kill backstop)."""
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
        """The Rust log string must contain `sidecar kill completed` so
        the runbook §6.5 grep (grep for `SHUTDOWN|shutdown`) matches.

        The runbook's literal `[SHUTDOWN] sidecar exited cleanly` is a
        documentation simplification; the actual log is
        `[SHUTDOWN] sidecar exited gracefully (code=..., signal=...)`
        and `[SHUTDOWN] sidecar kill completed (graceful=...)` — both
        match the `SHUTDOWN` grep and are MORE informative.
        """
        src = _read_sidecar_cmds_module()
        assert "[SHUTDOWN]" in src, (
            "Rust shutdown logs must use the '[SHUTDOWN]' prefix so the runbook "
            "§6.5 grep (grep 'SHUTDOWN|shutdown') matches"
        )

    def test_python_log_string_matches_runbook(self):
        """The Python log string must match the runbook §6.5 expected
        sidecar.log line exactly: `[SIDECAR-WS] shutdown received —
        releasing mic and exiting`."""
        src = _read(_SIDECAR_WS_PY)
        assert "[SIDECAR-WS] shutdown received — releasing mic and exiting" in src, (
            "Python shutdown handler must log the exact runbook §6.5 line: "
            "'[SIDECAR-WS] shutdown received — releasing mic and exiting'"
        )
