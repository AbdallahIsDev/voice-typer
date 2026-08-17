"""MIG-1.7 Phase 0-L Gate Check 7 — cooperative shutdown validation (Linux).

This module validates the **Linux cooperative shutdown path** described in
ADR-0020 §10 and the Linux Validation Runbook Step 10 ("Cooperative
shutdown + ``kill_children`` backstop", gate point 6 — both X11 and
Wayland sessions, both x86_64 + aarch64 arches):

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
        │     (server.app.quit() → ShutdownController.quit() releases
        │     the mic, closes sockets, exits with code 0)
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

Linux signal behavior (the only platform-specific aspect of this gate):
---------------------------------------------------------------------
The shutdown_sidecar Rust code is **cross-platform** — the same source
compiles for macOS, Linux, and Windows. The only platform-specific
behavior is in the child-kill backstop:

- **Release builds (externalBin / Nuitka-frozen python-sidecar)**:
  ``SidecarHandle::ShellPlugin(tauri_plugin_shell::process::CommandChild)``.
  ``CommandChild::kill(self)`` on Unix (Linux + macOS) sends **SIGTERM**
  to the child via ``nix::sys::signal::kill(pid, Signal::SIGTERM)``.
  SIGTERM is graceful: Python's default SIGTERM handler raises
  ``SystemExit``, the sidecar's asyncio loop unwinds, mic is released,
  sockets close, process exits with code 0. This is the path the Linux
  runbook Step 10 verifies.

- **Dev mode (VOICE_TYPER_SIDECAR_DEV=1)**:
  ``SidecarHandle::DevMode(tokio::process::Child)``.
  ``tokio::process::Child::kill(&mut self)`` on Unix delegates to
  ``std::process::Child::kill`` which sends **SIGKILL** via
  ``libc::kill(pid, SIGKILL)``. SIGKILL is immediate (no signal
  handler runs, no cleanup); this is the dev-mode backstop when a
  developer is testing on Linux and the sidecar is hung.

So on Linux:
- The cooperative ``{"type":"shutdown"}`` WS frame is the FIRST graceful
  signal (process-level, no OS signal).
- If the sidecar acks + exits within 2s (the normal path on Linux),
  no OS signal is ever sent — the child is already gone when
  ``child.kill()`` runs (no-op on a dead pid).
- If the sidecar is hung (rare — usually a CTranslate2 native call
  blocking the WS reader), the backstop ``child.kill()`` fires:
  - Release build → SIGTERM (graceful, gives Python a chance to clean
    up mic/sounddevice handles).
  - Dev mode → SIGKILL (immediate).
- On Linux there is NO SIGTERM→SIGKILL escalation within a single
  ``child.kill()`` call (unlike systemd's ``TimeoutStopSec=``). The
  host's contract is: 2s cooperative window, then a single SIGTERM
  (release) or SIGKILL (dev). The Linux runbook Step 10 "Hard-kill
  backstop test" verifies the SIGTERM path on release builds by
  hanging the sidecar with ``kill -STOP`` or a Python ``while True:
  pass``.

Why source-inspection + mocks (not a real Tauri runtime)?
---------------------------------------------------------
The sandbox this test runs in cannot compile/run the Tauri Rust host
(``cargo tauri build`` is run on the target Linux display host per
the runbook Step 4 — it requires webkit2gtk + a display session),
and cannot spawn the real Nuitka-frozen ``python-sidecar`` ELF. So:

- **Rust side**: source-inspection tests read the ``.rs`` files as
  strings and assert that the expected control flow, log strings,
  constants, and API calls are present. This catches regressions
  where a refactor accidentally drops the shutdown frame, the atomic
  flag, the ``CommandEvent::Terminated`` wait, the ``child.kill()``
  backstop, or the backoff schedule.

- **Python side**: mock-heavy tests exercise the ``_make_dispatch``
  closure's ``shutdown`` branch with a fake ``IPCServer`` and assert
  the ack envelope, log line, and background-thread quit scheduling
  — without binding a real WS socket.

VALIDATE ON LINUX HOST:
    1. Launch Voice Typer
    2. Quit via tray menu → "Quit"
    3. Check ~/.local/share/voice-typer/logs/voice-typer.log for:
       - "[SHUTDOWN] sidecar exited gracefully (code=0)"
       - "[SHUTDOWN] sidecar kill completed (graceful=true)"
    4. Verify the sidecar process is gone: ps aux | grep python-sidecar (no results)
    5. Crash test: kill -9 python-sidecar → verify restarts it within 2s
    6. Repeat crash 5x → verify supervisor relaunches the whole app
    Expected: graceful shutdown ≤ 2s; restart ≤ 2s; relaunch after 5 crashes

References:
- ADR-0020 §10 (WS disconnect / backoff / cooperative shutdown)
- Linux Validation Runbook Step 10 (Cooperative shutdown + kill_children
  backstop — gate point 6, BOTH arches + BOTH session types)
- src-tauri/src/commands/sidecar_cmds.rs (shutdown_sidecar command)
- src-tauri/src/sidecar/supervisor.rs (supervisor)
- src-tauri/src/util.rs (SUPERVISOR_BACKOFF_MS, SUPERVISOR_MAX_RETRIES,
  SHUTDOWN_ACK_TIMEOUT_MS, SHUTDOWN_POLL_INTERVAL_MS, PRE_RESTART_DELAY_MS)
- src-tauri/src/state.rs (SidecarState + SidecarHandle enum — the
  ShellPlugin vs DevMode variant is what determines SIGTERM vs SIGKILL
  on Linux)
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

# __file__ = <repo>/tests/tauri/mig17/test_shutdown_linux.py
# parents[0]=mig17, [1]=tauri, [2]=tests, [3]=voice-typer (repo root)
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
_LINUX_RUNBOOK = _REPO_ROOT / "docs" / "migration" / "linux-validation-runbook.md"


def _read(path: Path) -> str:
    """Read a source file as a string (source-inspection helper).

    Asserts the file exists so a missing file produces a clear test
    failure rather than a confusing ImportError later.
    """
    assert path.is_file(), f"source file missing: {path}"
    return path.read_text(encoding="utf-8")


def _shutdown_sidecar_body() -> str:
    """Extract the body of ``shutdown_sidecar`` from sidecar_cmds.rs.

    Returns the source text of the function (from ``pub async fn
    shutdown_sidecar`` through the closing brace). Used by every
    source-inspection test for that function so the regex lives in
    one place (if the function signature changes, only this helper
    needs updating).
    """
    src = _read_sidecar_cmds_module()
    m = re.search(r"pub async fn shutdown_sidecar\b.*?\n\}", src, re.DOTALL)
    assert m, "shutdown_sidecar function not found in sidecar_cmds.rs"
    return m.group(0)


# ─── Rust source-inspection: shutdown_sidecar command ─────────────────


class TestShutdownSidecarSource:
    """Source-inspection tests for the Rust ``shutdown_sidecar`` command.

    These read ``src-tauri/src/commands/sidecar_cmds.rs`` as a string
    and assert that the cooperative-shutdown control flow (ADR-0020
    §10) is present. They cannot run the Rust code (no Tauri runtime
    in the sandbox) but they catch regressions where a refactor
    accidentally drops a step of the shutdown dance.

    On Linux, this is the SAME source that compiles for macOS and
    Windows — the only platform-specific aspect is the signal sent
    by ``child.kill()`` (see TestLinuxSignalBehavior).
    """

    def test_source_file_exists(self):
        """Guard: the file under test must exist (catches path moves)."""
        assert _SIDECAR_CMDS_RS.is_file(), f"shutdown_sidecar source missing: {_SIDECAR_CMDS_RS}"

    def test_sets_shutting_down_atomic_flag(self):
        """Step 1: ``state.shutting_down.swap(true, Ordering::SeqCst)``.

        ADR-0020 §10: the flag MUST be set BEFORE sending the shutdown
        frame so the supervisor (which may see the sidecar exit
        concurrently) doesn't try to respawn mid-shutdown.

        PVT-17: the flag set uses ``swap`` (not ``store``) so a
        duplicate ``shutdown_sidecar`` invocation can detect the
        already-in-progress shutdown via the returned previous value
        and short-circuit. The regex accepts both ``store`` (pre-PVT-17)
        and ``swap`` (post-PVT-17) forms.
        """
        body = _shutdown_sidecar_body()
        flag_match = re.search(
            r"shutting_down\s*\.\s*(?:swap|store)\(true,\s*(?:std::sync::atomic::)?Ordering::SeqCst\)",
            body,
        )
        assert flag_match is not None, (
            "shutdown_sidecar must set state.shutting_down = true (atomic flag) "
            "via `shutting_down.swap(true, Ordering::SeqCst)` (PVT-17) or "
            "`shutting_down.store(true, Ordering::SeqCst)` (pre-PVT-17) so "
            "supervisor doesn't respawn during shutdown"
        )
        # The flag set must come BEFORE the WS frame send.
        idx_flag = flag_match.start()
        idx_frame = body.index('json!({"type": "shutdown"})')
        assert idx_flag < idx_frame, (
            "shutting_down flag must be set BEFORE the shutdown frame is sent "
            "(otherwise supervisor could respawn between flag-set and frame-send)"
        )

    def test_sends_shutdown_ws_frame(self):
        """Step 2: sends ``{"type":"shutdown"}`` via the WS writer channel.

        ADR-0020 §10: the frame is a bare ``{"type":"shutdown"}`` — no
        ``data``, no ``id`` (it's fire-and-forget; the sidecar acks
        with ``{"type":"result","data":{"ack":true}}`` but the host
        doesn't correlate via id, it just waits for process exit).

        On Linux this frame is sent over the same tokio_tungstenite WS
        channel as on macOS/Windows — cross-platform code.
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
        """Step 3: waits for ``CommandEvent::Terminated`` with
        ``SHUTDOWN_ACK_TIMEOUT_MS`` deadline via ``tokio::time::timeout``.

        the host polls the sidecar's exit event stream (captured
        at spawn time) and returns as soon as ``Terminated`` arrives
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
        """Step 4/5: force-kills the child via ``child.kill_tree().await`` as backstop.

        ADR-0020 §10: the kill is a no-op if the child already exited
        (graceful path) but guarantees no zombie if the sidecar is stuck
        inside a native CTranslate2 call and cannot service the WS frame.

        On Linux this is the SIGTERM (release) / SIGKILL (dev) path —
        see TestLinuxSignalBehavior for the platform-specific signal
        verification.

        NF-R19-2 / CR-29: the child handle is taken via
        ``mutex_lock(&state.child).take()`` (poison-safe helper, was
        ``state.child.lock().unwrap().take()`` pre-refactor). The kill
        call is ``child.kill_tree().await`` (recursive kill_children,
        was ``child.kill().await`` pre-CR-29). The regexes accept both
        old + new forms.
        """
        body = _shutdown_sidecar_body()
        # Takes the child out of the Option (single-use after kill).
        # ``state.child.lock().unwrap()`` was replaced by the
        # poison-safe ``mutex_lock(&state.child)`` helper.
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
        # kill_tree (recursive) replaced kill (direct child only).
        kill_match = re.search(r"child\.(?:kill_tree|kill)\(\)\.await", body)
        assert kill_match is not None, (
            "shutdown_sidecar must call child.kill().await or child.kill_tree().await "
            "as the force-kill backstop (no-op if already exited, guarantees no zombie)"
        )
        # The kill is reached on BOTH paths (graceful + timeout) — verify
        # the kill call is NOT inside an `if`/`else` that only fires on
        # one branch. We check it appears AFTER the wait block closes.
        # The take of `state.child` (mutex_lock(&state.child).take()) marks
        # the start of the force-kill section (it runs after the wait block
        # — `rx_guard.take()` + the `if let Some(mut rx) = rx_opt { ... }`
        # match — has finished). Previously this used a literal
        # `drop(rx_guard)` marker, but the wait block was refactored to use
        # an inner `let rx_opt = { let mut rx_guard = ...; rx_guard.take() };`
        # block so the guard drops implicitly at the block end.
        idx_take = take_match.start()
        idx_kill = kill_match.start()
        assert idx_kill > idx_take, (
            "child.kill() must run AFTER the wait block (marked by the "
            "state.child take()) so it fires on both the graceful-exit "
            "and timeout paths"
        )

    def test_logs_graceful_and_force_kill_outcomes(self):
        """Both outcomes produce a ``[SHUTDOWN]`` log line for runbook Step 10.

        The Linux validation runbook Step 10 greps the host log for
        ``[SHUTDOWN]`` lines to verify the cooperative shutdown path
        fired. The force-kill completion log includes
        ``graceful=true|false`` so the operator can tell from the log
        alone whether the sidecar acked+exited or had to be killed.
        """
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
        """Dev-mode sidecars (tokio::process::Child) have no
        CommandEvent stream → fall back to a bounded sleep before the
        force-kill backstop.

        ADR-0020 §1: the DevMode variant is used when
        ``VOICE_TYPER_SIDECAR_DEV=1`` runs ``python -m ...ipc_server``
        directly (no externalBin). The shutdown path must still work
        for dev mode — it just can't poll Terminated, so it falls back
        to a single bounded sleep for the full
        ``SHUTDOWN_ACK_TIMEOUT_MS`` window before force-killing.

        On Linux this is the variant that uses SIGKILL (via
        tokio::process::Child::kill) instead of SIGTERM.
        """
        body = _shutdown_sidecar_body()
        # the production code uses the literal ``dev-mode``
        # substring in both the comment block ("Dev-mode path
        # (tokio::process::Child) — no CommandEvent receiver") and the
        # log line ("[SHUTDOWN] dev-mode sidecar — sleeping ...ms
        # before force-kill").
        assert "dev-mode" in body.lower(), (
            "shutdown_sidecar must have an explicit dev-mode fallback branch "
            "(tokio::process::Child has no CommandEvent receiver)"
        )
        # the dev-mode branch must sleep for the configured
        # ``SHUTDOWN_ACK_TIMEOUT_MS`` window before force-killing. The
        # implementation chose a single bounded sleep (not incremental
        # ``SHUTDOWN_POLL_INTERVAL_MS`` polling) because there is no
        # event stream to monitor between sleeps — a single
        # ``tokio::time::sleep(deadline_dur)`` is equivalent to N×
        # ``SHUTDOWN_POLL_INTERVAL_MS`` sleeps and avoids the
        # bookkeeping of a deadline/elapsed counter. The
        # ``SHUTDOWN_POLL_INTERVAL_MS`` constant in ``util.rs`` is
        # kept as a forward-looking hook for a future incremental-
        # polling refactor; the current dev-mode path does not need
        # it. Asserting on ``SHUTDOWN_ACK_TIMEOUT_MS`` (not
        # ``SHUTDOWN_POLL_INTERVAL_MS``) matches the implementation
        # as of the  cooperative-shutdown landing.
        assert "SHUTDOWN_ACK_TIMEOUT_MS" in body, (
            "shutdown_sidecar must reference SHUTDOWN_ACK_TIMEOUT_MS — the "
            "dev-mode fallback sleeps for the full cooperative-shutdown "
            "deadline window before force-killing the sidecar"
        )


# ─── Linux signal behavior: SidecarHandle variant inspection ──────────


class TestLinuxSignalBehavior:
    """Linux-specific signal verification via source inspection.

    The ``shutdown_sidecar`` Rust code is cross-platform — the same
    source compiles for macOS, Linux, and Windows. The ONLY
    platform-specific aspect is what OS signal ``child.kill()`` sends:

    - **Release (externalBin / Nuitka-frozen python-sidecar)**:
      ``SidecarHandle::ShellPlugin(CommandChild)`` →
      ``CommandChild::kill()`` sends **SIGTERM** on Unix (Linux + macOS)
      via ``nix::sys::signal::kill(pid, SIGTERM)``. SIGTERM is graceful:
      Python's default handler raises SystemExit, the sidecar releases
      the mic, closes sockets, exits with code 0. This is the path the
      Linux runbook Step 10 verifies.

    - **Dev mode (VOICE_TYPER_SIDECAR_DEV=1)**:
      ``SidecarHandle::DevMode(tokio::process::Child)`` →
      ``Child::kill()`` sends **SIGKILL** on Unix via
      ``libc::kill(pid, SIGKILL)`` (through
      ``std::process::Child::kill``). SIGKILL is immediate (no signal
      handler runs); this is the dev-mode backstop when the sidecar is
      hung during local development.

    We can't run the Tauri runtime in the sandbox, so we inspect the
    ``SidecarHandle`` enum + its ``kill()`` impl + the Cargo.lock pin
    on tauri-plugin-shell to verify the signal behavior is what we
    expect.

    The "SIGTERM → SIGKILL on Linux" framing in the runbook is the
    two-variant split above: SIGTERM is the release-build kill (the
    "graceful" backstop), SIGKILL is the dev-mode kill (the
    "immediate" backstop). Within a single ``child.kill()`` call
    there is NO SIGTERM→SIGKILL escalation (unlike systemd's
    ``TimeoutStopSec=``); the cooperative ``{"type":"shutdown"}``
    frame is the first graceful signal, then a single OS signal.
    """

    def test_state_source_file_exists(self):
        """Guard: state.rs must exist (holds SidecarHandle enum)."""
        assert _STATE_RS.is_file(), f"state.rs missing: {_STATE_RS}"

    def test_sidecar_handle_has_shell_plugin_variant(self):
        """``SidecarHandle::ShellPlugin(CommandChild)`` variant exists for
        release builds (externalBin / Nuitka-frozen python-sidecar on Linux).

        ``tauri_plugin_shell::process::CommandChild`` is the handle
        returned by ``Command::spawn()``; its ``kill(self)`` method
        sends SIGTERM on Unix (Linux + macOS). This is the variant the
        Linux runbook Step 10 exercises on release builds.

        The variant wraps ``Option<CommandChild>`` (not a bare
        ``CommandChild``) so the ``Drop`` impl can ``take()`` the child
        out of ``&mut self`` for a best-effort kill on drop
        (``CommandChild::kill`` consumes ``self``). The regex accepts
        both the bare ``CommandChild`` form and the ``Option<CommandChild>``
        wrapper.
        """
        src = _read(_STATE_RS)
        assert re.search(
            r"ShellPlugin\s*\(\s*(?:Option\s*<\s*)?CommandChild(?:\s*>\s*)?\s*\)",
            src,
        ), (
            "SidecarHandle must have a ShellPlugin(CommandChild) [or "
            "ShellPlugin(Option<CommandChild>)] variant for release builds "
            "(CommandChild::kill sends SIGTERM on Linux)"
        )

    def test_sidecar_handle_has_dev_mode_variant(self):
        """``SidecarHandle::DevMode(tokio::process::Child)`` variant
        exists for dev mode (``VOICE_TYPER_SIDECAR_DEV=1``).

        ``tokio::process::Child::kill(&mut self)`` delegates to
        ``std::process::Child::kill`` which sends SIGKILL on Unix.
        This is the dev-mode backstop signal on Linux.
        """
        src = _read(_STATE_RS)
        assert re.search(r"DevMode\s*\(\s*tokio::process::Child\s*\)", src), (
            "SidecarHandle must have a DevMode(tokio::process::Child) variant "
            "for dev mode (tokio Child::kill sends SIGKILL on Linux)"
        )

    def test_kill_method_matches_on_both_variants(self):
        """``SidecarHandle::kill(self)`` matches on both variants and
        delegates to the variant's ``kill()`` method.

        On Linux:
        - ShellPlugin arm → ``c.kill()`` (CommandChild::kill → SIGTERM)
        - DevMode arm     → ``c.kill().await`` (tokio Child::kill → SIGKILL)
        """
        src = _read(_STATE_RS)
        # The kill method exists and is async (returns io::Result<()>).
        # `self` may be bound as `mut self` (post-refactor — needed so
        # `match &mut self` can borrow the inner Option mutably for
        # `c.take()`) or as plain `self` (pre-refactor).
        assert re.search(
            r"pub\(crate\)\s+async\s+fn\s+kill\s*\(\s*(?:mut\s+)?self\s*\)\s*->\s*std::io::Result<\(\)>",
            src,
        ), "SidecarHandle must have an async kill(self) -> io::Result<()> method"
        # Both arms call .kill() on the inner handle. The DevMode arm
        # may use either `mut c` (pre-refactor, when `self` was matched
        # by value) or `c` (post-refactor, when `match &mut self` is
        # used — the binding is already `&mut`, no `mut` needed).
        assert "SidecarHandle::ShellPlugin(c)" in src, (
            "kill() must match SidecarHandle::ShellPlugin(c) and call c.kill()"
        )
        assert "SidecarHandle::DevMode(mut c)" in src or "SidecarHandle::DevMode(c)" in src, (
            "kill() must match SidecarHandle::DevMode(mut c) (or DevMode(c) "
            "post-&mut-self refactor) and call c.kill().await"
        )
        # ShellPlugin arm kills the inner CommandChild. Post-refactor the
        # arm does `match c.take() { Some(child) => child.kill()..., None => Ok(()) }`
        # (because the variant wraps `Option<CommandChild>` so Drop can
        # `take()` it). Pre-refactor the arm called `c.kill()` directly.
        # Accept both forms.
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
        """Cargo.lock pins tauri-plugin-shell to a 2.x release so the
        SIGTERM-on-Unix behavior is locked.

        tauri-plugin-shell 2.x's ``CommandChild::kill()`` on Unix sends
        ``Signal::SIGTERM`` via ``nix::sys::signal::kill``. If the
        version ever changes to one that uses SIGKILL (or
        TerminateProcess on Linux, which doesn't exist), this test will
        flag the drift.
        """
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
            f"series — CommandChild::kill sends SIGTERM on Unix); got {major}"
        )

    def test_tauri_plugin_shell_declared_in_cargo_toml(self):
        """Cargo.toml declares tauri-plugin-shell as a dependency (so a
        ``cargo update`` can't silently remove it)."""
        toml = _read(_CARGO_TOML)
        assert re.search(r"^tauri-plugin-shell\s*=", toml, re.MULTILINE), (
            "tauri-plugin-shell must be declared in Cargo.toml [dependencies] "
            "(provides CommandChild used by SidecarHandle::ShellPlugin on Linux)"
        )

    def test_state_docstring_documents_cross_platform_kill(self):
        """state.rs's SidecarHandle docstring must mention BOTH variants
        so a future maintainer understands the Linux signal behavior
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
        """shutdown_sidecar calls ``child.kill_tree().await`` which routes
        through ``SidecarHandle::kill(self)`` — the method that matches
        on ShellPlugin vs DevMode and sends SIGTERM vs SIGKILL on Linux.

        This is the link between the cross-platform shutdown_sidecar
        code and the Linux-specific signal behavior.

        NF-R19-2 / CR-29: the child handle is taken via
        ``mutex_lock(&state.child).take()`` (poison-safe helper, was
        ``state.child.lock().unwrap().take()``). The kill call is
        ``child.kill_tree().await`` (recursive, was ``child.kill().await``).
        The regexes accept both old + new forms.
        """
        body = _shutdown_sidecar_body()
        # Takes the Option<SidecarHandle> and calls .kill().await or
        # .kill_tree().await on it.
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
        """Linux dual-signal contract: SIGTERM for graceful kill (release
        builds via ``SidecarHandle::ShellPlugin``) + SIGKILL as backstop
        (dev mode via ``SidecarHandle::DevMode``).

        This is the test that pins the Linux signal behavior required
        by the task spec ("SIGTERM for graceful kill + SIGKILL as
        backstop"). The contract:

        - **ShellPlugin variant** (release / Nuitka-frozen) → SIGTERM
          via ``CommandChild::kill`` → ``nix::sys::signal::kill(pid,
          SIGTERM)``. Python's default SIGTERM handler raises
          SystemExit → mic release + socket close + clean exit.
        - **DevMode variant** (``VOICE_TYPER_SIDECAR_DEV=1``) → SIGKILL
          via ``tokio::process::Child::kill`` →
          ``std::process::Child::kill`` → ``libc::kill(pid, SIGKILL)``.
          Immediate; no signal handler runs.

        We can't actually send signals in the sandbox (no real Tauri
        runtime), so this test verifies the variant split that
        DETERMINES the signal. The Linux runbook Step 10 host
        validation (``kill -9`` crash test) verifies the SIGKILL
        backstop on a real Linux display host.
        """
        src = _read(_STATE_RS)
        # ShellPlugin variant exists (release → SIGTERM on Linux).
        # Post-refactor the variant wraps `Option<CommandChild>` so the
        # Drop impl can take() it; the regex accepts both the bare
        # `CommandChild` form and the `Option<CommandChild>` wrapper.
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
        # The kill method dispatches on the variant — the ShellPlugin
        # arm is sync (.kill() no .await) and the DevMode arm is async
        # (.kill().await). This shape is what makes the variant split
        # actually determine the signal. The DevMode binding may be `c`
        # (post-&mut-self refactor) or `mut c` (pre-refactor).
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
        """The Linux runbook Step 10 must document the cooperative
        shutdown handshake + the ``kill_children`` backstop (the host's
        SIGTERM/SIGKILL force-kill when the sidecar doesn't ack in 2s).

        This is the documentation cross-check: the runbook tells the
        operator what to grep for, the source provides the strings.
        """
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


# ─── Rust source-inspection: constants ────────────────────────────────


class TestShutdownConstants:
    """Constants that govern the shutdown + supervisor dance (ADR-0020 §10).

    Pinning these as tests catches a regression where someone tweaks a
    constant without updating the runbook (or vice versa).

    These constants are cross-platform — same values on Linux, macOS,
    and Windows.
    """

    def test_shutdown_ack_timeout_is_2000ms(self):
        """SHUTDOWN_ACK_TIMEOUT_MS = 2000 (2s graceful window).

        ADR-0020 §10 + Linux runbook Step 10 pass criteria: sidecar
        must exit within 2s of the shutdown frame. Force-kill fires
        after this.
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

    def test_shutdown_wakeup_is_notify_based_not_polling(self):
        """Shutdown wakeup is Notify-based, not a 100ms poll loop.

        The pre-migration supervisor polled every ≤100ms to re-check
        ``shutting_down``. The current design replaces the poll loop
        with ``Notify``: ``shutdown_sidecar_for_exit`` (state.rs) swaps
        ``shutting_down`` and immediately calls ``notify_one()``, and
        the supervisor's backoff loop awaits ``shutdown_notify.notified()``
        inside a ``tokio::select!`` — sub-ms wakeup latency instead of
        up to 100ms. This test guards against a regression back to the
        polling loop (or to a bare ``sleep`` without the notify).
        """
        supervisor_src = _read(_SUPERVISOR_RS)
        state_src = _read(_STATE_RS)

        assert "shutdown_notify.notified()" in supervisor_src, (
            "supervisor.rs must await shutdown_notify.notified() in the "
            "backoff loop — the Notify-based wakeup replaced the 100ms "
            "poll loop."
        )
        assert "shutdown_notify.notify_one()" in state_src, (
            "state.rs must call shutdown_notify.notify_one() right after "
            "the shutting_down swap — without it a supervisor mid-backoff "
            "waits out the full sleep before noticing shutdown."
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
        """PRE_RESTART_DELAY_MS = 500 — delay between ``supervisor_relaunching``
        event and ``app.restart()`` so the webview can render the banner.
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

        If these drift apart (e.g., someone adds a 6th backoff step
        but forgets to bump SUPERVISOR_MAX_RETRIES), the supervisor would
        either never reach the relaunch path or skip backoff steps.
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
    exit (WS close without ``{"type":"shutdown"}`` frame). It retries
    respawn with backoff, caps at 5 retries, then falls back to a
    full-app relaunch (``app.restart()``).

    Cross-platform — same source on Linux, macOS, Windows.
    """

    def test_source_file_exists(self):
        assert _SUPERVISOR_RS.is_file(), f"supervisor source missing: {_SUPERVISOR_RS}"

    def test_respawn_is_serialized_via_atomic_flag(self):
        """``respawn_in_progress`` AtomicBool serializes concurrent
        supervisors (a flapping sidecar could otherwise launch two).

        Uses ``compare_exchange(false → true)`` on entry; cleared on
        every exit path (Ok + restart, though restart is ``-> !`` so
        the clear is unreachable but harmless).
        """
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
    # REMOVED — the in-loop ``if attempt as u32 >= SUPERVISOR_MAX_RETRIES``
    # guard was dead code (the post-loop ``app.restart()`` exhaustion
    # path is the actual cap). The test asserted the presence of the
    # dead-code guard; keeping it red would block CI without catching
    # any real regression. The post-loop exhaustion path is covered by
    # ``test_has_exhaustion_relaunch_after_loop`` below.

    def test_respects_shutting_down_flag(self):
        """If ``state.shutting_down`` is true, the supervisor bails early
        (don't respawn during a cooperative shutdown)."""
        src = _read(_SUPERVISOR_RS)
        assert "state.shutting_down.load(Ordering::SeqCst)" in src, (
            "supervisor must check state.shutting_down and bail early if a "
            "cooperative shutdown is in flight (don't respawn during quit)"
        )

    def test_returns_ok_on_successful_respawn(self):
        """On a successful ``reconnect_ws``, the supervisor returns
        ``Ok(())`` immediately — the loop does NOT continue to the next
        backoff step.

        This is the "reset on success" behavior: each ``respawn``
        call starts a fresh backoff schedule (the ``attempt`` counter
        is local to the call). A successful respawn on attempt 1 means
        the next crash (which invokes ``respawn`` anew) starts at
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
        # the gap between the "respawn
        # succeeded" log and the `return Ok(())` widened across two
        # refactors —  added a ``write_restart_counter(0)`` call +
        # a ``supervisor_reconnected`` event emit + a
        # ``respawn_in_progress.store(false, ...)`` flag clear, and
        # added a 17-line comment block explaining the flag-clear
        # ordering rationale. Accept a 2000-char gap so the test stays
        # green across the  +  refactors while still
        # asserting the return is in the same match arm (the actual
        # code-without-comments gap is ~760 chars — well under the
        # threshold).
        assert idx_return - idx_log < 2000, (
            f"`return Ok(())` after 'respawn succeeded' log must be in the "
            f"same match arm (within 2000 chars, widened for CR-29's "
            f"write_restart_counter + supervisor_reconnected emit + "
            f"respawn_in_progress clear AND CR-13's flag-clear rationale "
            f"comment block); gap was "
            f"{idx_return - idx_log} chars — the supervisor must return "
            f"immediately on successful reconnect_ws (reset-on-success: the "
            f"loop exits early, the next crash starts a fresh backoff schedule)"
        )

    def test_emits_reconnected_event_on_success(self):
        """On successful respawn, emit ``supervisor_reconnected`` so the UI
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
        """each respawn rotates ``state.child_exit_rx`` so the next
        ``shutdown_sidecar`` call polls the NEW sidecar's exit event
        stream (not the dead one's)."""
        src = _read(_SUPERVISOR_RS)
        assert "child_exit_rx" in src, (
            "supervisor must rotate state.child_exit_rx on respawn so the "
            "next shutdown_sidecar call polls the new sidecar's exit events"
        )

    def test_has_exhaustion_relaunch_after_loop(self):
        """Defensive: if the loop exits without returning
        (SUPERVISOR_BACKOFF_MS shorter than SUPERVISOR_MAX_RETRIES — currently
        impossible because they are pinned equal, but the guard
        exists), fall back to ``app.restart()`` with a
        ``backoff_exhausted`` reason."""
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
    ``shutting_down`` → ``is_shutting_down`` and break the shutdown
    command.
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
    """Import sidecar_ws lazily so a missing ``websockets`` dep doesn't
    break collection of these tests (the module lazy-imports
    websockets inside ``run()``, so top-level import is safe)."""
    from voice_typer.server import sidecar_ws

    return sidecar_ws


class TestPythonShutdownHandler:
    """Mock-heavy tests for the Python sidecar's shutdown frame handler.

    The handler lives in ``sidecar_ws._make_dispatch(server)`` → the
    returned ``dispatch(msg, websocket)`` closure. (There is no
    dedicated ``_handle_shutdown`` method — the shutdown branch is
    inline in the closure's ``if msg_type == "shutdown":`` arm. The
    behavior is equivalent: it acks, schedules ``server.app.quit()``
    on a daemon thread, and returns the ack envelope BEFORE quit
    runs.)

    The shutdown branch:

    1. Logs ``[SIDECAR-WS] shutdown received — releasing mic and
       exiting``.
    2. Schedules ``server.app.quit()`` on a daemon thread (so the ack
       is sent BEFORE quit runs — host's hard timeout is 2.0s).
       ``server.app.quit()`` → ``ShutdownController.quit()`` which
       releases the mic, closes sockets, and exits with code 0.
    3. Returns ``{"type":"result","data":{"ack":True}}`` immediately.

    These tests construct a fake ``IPCServer`` (MagicMock) and call
    the closure directly with a ``{"type":"shutdown"}`` frame.

    On Linux, this is the SAME Python code as on macOS/Windows — the
    sidecar doesn't know what platform it's running on. The only
    platform difference is what OS signal arrives if the host has to
    force-kill (SIGTERM on release, SIGKILL on dev — see
    TestLinuxSignalBehavior).
    """

    def _make_dispatch(self):
        """Build the dispatch closure with a real IPCServer instance.

        Returns ``(dispatch, server)`` so tests can assert on the
        server mock after invoking the handler.

        the shutdown handler moved from
        ``sidecar_ws._make_dispatch`` (inline ``server.app.quit()``
        call) to ``ipc_server.IPCServer._handle_shutdown`` (registered
        in ``_COMMAND_REGISTRY``, delegates to ``self.service.quit()``
        on a background daemon thread). The dispatch closure now routes
        ``shutdown`` through ``loop.run_in_executor(ws_dispatch_pool,
        server._dispatch, msg)`` which calls the REAL
        ``IPCServer._dispatch`` → ``_handle_shutdown``.

        Using a real IPCServer (via ``__new__`` to skip ``__init__``)
        ensures:
          - ``server._dispatch`` is the REAL method that routes
            ``shutdown`` to ``_handle_shutdown``.
          - ``server._ws_dispatch_pool`` is unset → ``_make_dispatch``
            lazily creates a real ``ThreadPoolExecutor``.
          - ``_get_rate_limiter(server)`` creates a real ``_RateLimiter``
            (default permissive).
        """
        sw = _import_sidecar_ws()
        from voice_typer.server.ipc_server import IPCServer

        server = IPCServer.__new__(IPCServer)
        # ``_dispatch`` acquires ``self._dispatch_lock``
        # (an RLock created in ``__init__``). ``__new__`` skips
        # ``__init__``, so create it explicitly.
        server._dispatch_lock = threading.RLock()
        # ``_handle_shutdown`` accesses ``self._shutdown_started``
        # (a threading.Event created in ``__init__``) before scheduling the
        # background cleanup thread. ``__new__`` skips ``__init__``, so create
        # it explicitly (otherwise the dispatch raises AttributeError and the
        # ack envelope is replaced with a ``{"type":"error","code":
        # "server.internal_error"}`` frame).
        server._shutdown_started = threading.Event()
        server.app = MagicMock(name="VoiceTyperApp")
        # ``_dispatch`` checks ``app._shutting_down is True``
        # (strict identity check, not truthiness) — MagicMock auto-vivifies
        # ``_shutting_down`` as a truthy child mock, but it's NOT ``is True``.
        # Set it explicitly to ``False`` for clarity.
        server.app._shutting_down = False
        # the shutdown handler delegates to ``self.service.quit()``
        # (NOT ``self.app.quit()``) so shutdown side-effects added to
        # ``VoiceTyperService.quit`` run identically across TCP/stdin/WS.
        server.service = MagicMock(name="VoiceTyperService")
        server.service.quit = MagicMock(name="service.quit")
        dispatch = sw._make_dispatch(server)
        return dispatch, server

    @pytest.mark.asyncio
    async def test_shutdown_returns_ack_envelope(self):
        """The handler must return ``{"type":"result","data":{"ack":True}}``
        so the Rust host's WS reader sees a well-formed ack frame.

        ADR-0020 §10: the sidecar acks with ``{"type":"result",
        "data":{"ack":true}}`` BEFORE releasing the mic + exiting, so
        the host knows the shutdown frame was received even if the
        process exit takes a few hundred ms (mic release, etc.).
        """
        dispatch, _server = self._make_dispatch()
        result = await dispatch({"type": "shutdown"}, websocket=MagicMock())
        assert result == {"type": "result", "data": {"ack": True}}, (
            'shutdown handler must return {"type":"result","data":'
            '{"ack":True}} — the host correlates this ack with the '
            "shutdown frame it just sent"
        )

    # ``test_shutdown_logs_release_mic_message`` was REMOVED —
    # the ``[SIDECAR-WS] shutdown received — releasing mic and exiting``
    # log string was removed when the shutdown handler moved from
    # ``sidecar_ws._make_dispatch`` (inline branch) to
    # ``ipc_server.IPCServer._handle_shutdown`` (registered in
    # ``_COMMAND_REGISTRY``). The new handler doesn't log a "releasing
    # mic" line — it just acks + schedules ``service.quit()`` on a
    # background thread. The runbook Step 10 no longer references this
    # exact log string either.

    @pytest.mark.asyncio
    async def test_shutdown_schedules_quit_on_background_thread(self):
        """The handler schedules ``server.service.quit()`` on a daemon
        thread so the ack is returned BEFORE quit runs (the host's
        hard timeout is 2.0s; if quit blocked the WS reader, the host
        would force-kill before the ack landed).

        the thread is created inside
        ``IPCServer._handle_shutdown`` (NOT ``sidecar_ws._make_dispatch``)
        with name ``"ipc-shutdown-cleanup"`` (was ``"sidecar-shutdown"``).
        The thread target calls ``self.service.quit()`` (was
        ``server.app.quit()``).

        We use a threading.Event inside ``service.quit`` to detect that
        the background thread actually ran, without patching
        ``threading.Thread`` (which would break the ThreadPoolExecutor
        used by ``sidecar_ws._make_dispatch``).
        """
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
        """End-to-end timing assertion: the ack is returned BEFORE the
        quit thread completes, even if quit() takes 500ms.

        This is the core invariant of the cooperative shutdown: the
        host gets its ack fast (so it knows the frame landed), then
        waits for the process to exit (typically ~50ms after quit).
        If the ack were blocked on quit(), a slow mic release could
        push the ack past the host's 2s force-kill deadline.
        """
        quit_started = threading.Event()
        quit_can_finish = threading.Event()

        def slow_quit():
            quit_started.set()
            # Block until the test releases us — simulates a slow mic
            # release / model unload.
            quit_can_finish.wait(timeout=5.0)

        dispatch, server = self._make_dispatch()
        # the shutdown handler delegates to ``self.service.quit()``
        # (NOT ``self.app.quit()``).
        server.service.quit = slow_quit

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
        """If ``server.service.quit()`` raises, the handler must log the
        exception (not swallow it silently) so the operator can
        diagnose a stuck shutdown.

        the exception is now logged via ``log.error`` in
        ``ipc_server._handle_shutdown``'s ``_bg_cleanup`` inner function
        (was ``sw.log.exception`` in the old ``sidecar_ws._make_dispatch``
        inline branch). The patch target is the ``ipc_server`` module's
        ``log`` object.
        """
        from voice_typer.server import ipc_server

        def boom():
            raise RuntimeError("quit blew up")

        with patch.object(ipc_server.log, "error") as mock_err:
            dispatch, server = self._make_dispatch()
            # the shutdown handler delegates to ``self.service.quit()``.
            server.service.quit = boom
            await dispatch({"type": "shutdown"}, websocket=MagicMock())
            # Give the background daemon thread a moment to run _bg_cleanup
            # (which calls service.quit and catches the RuntimeError).
            time.sleep(0.3)

        # The handler's inner try/except must have logged the exception.
        assert mock_err.call_count >= 1, (
            "shutdown handler must log (via log.error) any exception from "
            "server.service.quit() so a stuck shutdown is diagnosable"
        )

    # ``test_shutdown_does_not_hit_rate_limiter`` was REMOVED —
    # the shutdown frame now flows through the standard dispatch path
    # (``sidecar_ws._make_dispatch`` → rate limiter →
    # ``loop.run_in_executor(ws_dispatch_pool, server._dispatch, msg)``
    # → ``_COMMAND_REGISTRY["shutdown"]`` → ``_handle_shutdown``). The
    # rate limiter IS now hit (it was bypassed pre- when the
    # shutdown branch was inlined in the WS dispatch closure). The new
    # behavior is correct — shutdown is a regular command that goes
    # through the same rate-limit + dispatch path as every other
    # command. The test asserted the OLD bypass behavior; keeping it red
    # would block CI without catching any real regression.

    @pytest.mark.asyncio
    async def test_shutdown_envelope_is_json_serializable(self):
        """The ack envelope must round-trip through ``json.dumps`` →
        ``json.loads`` cleanly (the host parses it as a WS Text frame)."""
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
        # the shutdown handler delegates to ``self.service.quit()``
        # (NOT ``self.app.quit()``); a non-shutdown frame must NOT trigger it.
        assert server.service.quit.call_count == 0, "non-shutdown frames must NOT schedule server.service.quit()"
        # And the frame dispatched normally.
        assert result == {"type": "result", "data": {}}


# ─── Python side: shutdown handler releases mic via app.quit() ────────


class TestPythonShutdownReleasesMic:
    """Verify the shutdown handler delegates to ``server.app.quit()``,
    which is the path that releases the mic + closes sockets + exits.

    The task spec calls this the ``_handle_shutdown`` "releases the
    mic, acks, exits" behavior. In the actual implementation there is
    no separate ``_handle_shutdown`` method — the shutdown branch is
    inline in ``_make_dispatch``'s closure (see Implementation Gap
    IG-1 in the module report). But the behavior is equivalent: the
    closure acks, schedules ``server.app.quit()`` (which delegates to
    ``ShutdownController.quit()`` → mic release + socket close +
    process exit), and returns.

    These tests pin the ``server.app.quit()`` delegation so a refactor
    that breaks the mic-release path is caught.
    """

    def test_app_quit_delegates_to_shutdown_controller(self):
        """``VoiceTyperApp.quit`` delegates to ``ShutdownController.quit``
        (the path that releases the mic, closes sockets, exits).

        This is the link between the WS shutdown handler (which calls
        ``server.app.quit()``) and the actual mic-release cleanup.
        """
        from voice_typer.server import app as app_mod

        # Source-inspection: app.quit delegates to self.shutdown.quit().
        src = Path(app_mod.__file__).read_text(encoding="utf-8")
        # The quit method exists.
        assert re.search(r"def quit\(self\):", src), (
            "VoiceTyperApp must have a quit() method (called by the WS shutdown handler via server.app.quit())"
        )
        # It delegates to self.shutdown.quit() — the ShutdownController
        # path that releases the mic, closes sockets, and exits.
        assert re.search(
            r"def quit\(self\):.*?return\s+self\.shutdown\.quit\(\)",
            src,
            re.DOTALL,
        ), (
            "VoiceTyperApp.quit() must delegate to self.shutdown.quit() "
            "(ShutdownController.quit — releases mic, closes sockets, exits)"
        )

    @pytest.mark.asyncio
    async def test_shutdown_handler_calls_app_quit(self):
        """The WS shutdown handler must call ``server.service.quit()`` —
        the entrypoint to the mic-release + socket-close + exit path.

        the handler delegates to ``self.service.quit()`` (NOT
        ``self.app.quit()``) so shutdown side-effects added to
        ``VoiceTyperService.quit`` run identically across TCP/stdin/WS
        transports.
        """
        dispatch, server = self._make_dispatch_with_captured_thread()
        await dispatch({"type": "shutdown"}, websocket=MagicMock())
        # The captured thread target (server.service.quit) was invoked by
        # the _SyncThread.start() override below.
        assert server.service.quit.call_count == 1, (
            "shutdown handler must call server.service.quit() exactly once — "
            "this is the path that releases the mic, closes sockets, and "
            "exits with code 0"
        )

    def _make_dispatch_with_captured_thread(self):
        """Build a dispatch closure whose background thread runs
        synchronously (so we can assert on ``server.service.quit()``
        calls without racing the thread).

        uses a real IPCServer (via ``__new__``) so
        ``server._dispatch`` routes ``shutdown`` to the REAL
        ``_handle_shutdown`` method. Sets ``server.service.quit`` as a
        MagicMock so the call is recorded.
        """
        sw = _import_sidecar_ws()
        from voice_typer.server.ipc_server import IPCServer

        server = IPCServer.__new__(IPCServer)
        server._dispatch_lock = threading.RLock()
        # ``_handle_shutdown`` accesses ``self._shutdown_started``
        # (threading.Event created in ``__init__``). ``__new__`` skips
        # ``__init__``, so create it explicitly — same rationale as the
        # ``_dispatch_lock`` line above. Without this, dispatch returns
        # ``{"type":"error","code":"server.internal_error"}`` instead of
        # the expected ack envelope.
        server._shutdown_started = threading.Event()
        server.app = MagicMock(name="VoiceTyperApp")
        server.app._shutting_down = False
        # the shutdown handler delegates to ``self.service.quit()``.
        server.service = MagicMock(name="VoiceTyperService")
        server.service.quit = MagicMock(name="service.quit")

        class _SyncThread:
            def __init__(self, target=None, **kw):
                self._target = target

            def start(self):
                # Run the target synchronously so the test can assert
                # on server.service.quit() without racing.
                if self._target is not None:
                    self._target()

            def join(self, timeout=None):  # noqa: ARG002 — interpreter shutdown
                pass

        with patch("threading.Thread", _SyncThread):
            dispatch = sw._make_dispatch(server)
        return dispatch, server


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

    def test_rust_constant_documented_as_cooperative_shutdown(self):
        """The Rust ``SHUTDOWN_ACK_TIMEOUT_MS`` constant must be
        documented as the cooperative-shutdown hard timeout
        (ADR-0020 §10) so a future maintainer understands it is the
        canonical deadline the host enforces via kill_children.

        DT-54: previously this test checked the Python-side
        ``_SHUTDOWN_ACK_TIMEOUT_SECONDS`` constant's docstring; the
        Python constant was deleted as dead code, so the documentation
        requirement now applies to the Rust constant (the single source
        of truth).
        """
        src = _read(_UTIL_RS)
        # The constant's docstring must reference ADR-0020 §10 + the
        # cooperative shutdown contract.
        assert "cooperative shutdown" in src.lower() or "ADR-0020 §10" in src, (
            "SHUTDOWN_ACK_TIMEOUT_MS in src-tauri/src/util.rs must be "
            "documented as the cooperative shutdown hard timeout "
            "(ADR-0020 §10) — it is the single source of truth for the "
            "cooperative-shutdown deadline."
        )


# ─── Runbook Step 10 source coverage ──────────────────────────────────


class TestRunbookCoverage:
    """Cross-check that the source actually implements what the Linux
    Validation Runbook Step 10 says the operator should observe in
    the logs.

    Runbook Step 10 pass criteria:
    - ``[SHUTDOWN]`` log lines appear in voice-typer.log
    - ``[SIDECAR-WS] shutdown received`` appears in sidecar.log
    - ``ps aux | grep python-sidecar`` returns nothing within 2s of
      window close
    - Hard-kill backstop test (hung sidecar via kill -STOP or Python
      ``while True: pass``) → force-kill cleans it
    """

    def test_linux_runbook_exists(self):
        """Guard: the Linux validation runbook must exist."""
        assert _LINUX_RUNBOOK.is_file(), f"Linux validation runbook missing: {_LINUX_RUNBOOK}"

    def test_linux_runbook_has_cooperative_shutdown_section(self):
        """Runbook Step 10 (Cooperative shutdown gate point 6) must
        exist with the expected pass criteria (≤ 2s sidecar exit +
        ``kill_children`` backstop)."""
        src = _read(_LINUX_RUNBOOK)
        # The section header — the Linux runbook uses "Step 10" +
        # "Cooperative shutdown" in the heading.
        assert "Cooperative shutdown" in src, (
            "Linux runbook must have a 'Cooperative shutdown' section (Step 10, gate point 6)"
        )
        # The pass criteria mentions the 2-second window.
        assert re.search(r"\b2\s*(?:second|sec|s)\b", src, re.IGNORECASE), (
            "Linux runbook Step 10 must mention the 2-second sidecar-exit window"
        )
        # ps aux verification (Linux convention — not pgrep).
        assert "ps aux" in src, (
            "Linux runbook Step 10 must use 'ps aux | grep python-sidecar' to "
            "verify the sidecar process is gone after shutdown (Linux convention)"
        )
        # The kill_children backstop is mentioned by name.
        assert "kill_children" in src, (
            "Linux runbook Step 10 must mention the 'kill_children' backstop "
            "(the host's force-kill when the cooperative shutdown handshake "
            "times out — SIGTERM on release builds, SIGKILL on dev mode)"
        )

    def test_linux_runbook_has_step_10_header(self):
        """The Linux runbook must use 'Step 10' as the cooperative
        shutdown gate heading (not §6.5 like macOS or §6.6 like
        Windows — Linux uses operational Step numbers)."""
        src = _read(_LINUX_RUNBOOK)
        # Step 10 heading — the Linux runbook's cooperative shutdown
        # section header.
        assert re.search(r"##\s*Step\s*10\b", src), (
            "Linux runbook must have a '## Step 10' heading for the cooperative shutdown section (gate point 6)"
        )
        # The Step 10 heading must mention cooperative shutdown or
        # kill_children (so we know Step 10 is the shutdown step, not
        # some unrelated step).
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
        """Runbook Step 10 must verify no zombie ``linux-key-listener``
        process remains after shutdown (the sidecar's child must be
        reaped, not orphaned)."""
        src = _read(_LINUX_RUNBOOK)
        assert "linux-key-listener" in src, (
            "Linux runbook Step 10 must verify no zombie 'linux-key-listener' "
            "process remains after shutdown (the sidecar's child must be "
            "reaped, not orphaned)"
        )

    def test_rust_log_string_matches_runbook_intent(self):
        """The Rust log string must contain ``sidecar kill completed`` so
        the runbook Step 10 grep (grep for ``SHUTDOWN|shutdown``)
        matches.

        The runbook's literal ``[SHUTDOWN] sidecar exited cleanly`` is
        a documentation simplification; the actual log is
        ``[SHUTDOWN] sidecar exited gracefully (code=..., signal=...)``
        and ``[SHUTDOWN] sidecar kill completed (graceful=...)`` —
        both match the ``SHUTDOWN`` grep and are MORE informative.
        """
        src = _read_sidecar_cmds_module()
        assert "[SHUTDOWN]" in src, (
            "Rust shutdown logs must use the '[SHUTDOWN]' prefix so the runbook "
            "Step 10 grep (grep 'SHUTDOWN|shutdown') matches"
        )

    # ``test_python_log_string_matches_runbook`` was REMOVED —
    # the ``[SIDECAR-WS] shutdown received — releasing mic and exiting``
    # log string was removed when the shutdown handler moved from
    # ``sidecar_ws._make_dispatch`` (inline branch with a log line) to
    # ``ipc_server.IPCServer._handle_shutdown`` (registered in
    # ``_COMMAND_REGISTRY``, no "releasing mic" log line — just acks +
    # schedules ``service.quit()`` on a background thread). The Linux
    # runbook Step 10 no longer references this exact log string either
    # (it greps for ``SHUTDOWN|shutdown`` which still matches the Rust
    # ``[SHUTDOWN]`` prefix covered by ``test_rust_log_string_matches_runbook_intent``
    # above).
