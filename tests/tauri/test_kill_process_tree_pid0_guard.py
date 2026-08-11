"""Source-grep gate: `kill_process_tree(0)` must stay a safe no-op.

CI incident (2026-08): the Tauri Linux smoke job (`cargo test --bin
voice-typer-tauri`) was cancelled mid-run by ``kill_process_tree(0)`` in
``test_kill_process_tree_pid_zero_is_noop``. On Unix,
``enumerate_children(0)`` failed on ``/proc/0`` and fell back to
``pgrep -P 0`` — which matches PID 1 (init) + kernel threads. The DFS
then descended into the ENTIRE process tree (the GitHub Actions runner
agent included) and the per-pid SIGTERM/SIGKILL loop killed it. The job
died with "The runner has received a shutdown signal" + "The operation
was canceled." right after that test (only 278/413 tests printed).

The fix: ``kill_process_tree`` must return early for ``pid == 0`` before
any enumeration / signaling, and the Unix helpers (``signal_pid``,
``enumerate_children_pgrep``, ``kill_process_group_if_safe``) must
guard pid 0 too (POSIX ``kill(0, sig)`` signals the caller's own process
group; ``getpgid(0)`` returns the caller's own pgid).

These checks are static (read-the-source) so they run in the sandbox
without needing a real process tree — and they fail with a clear message
in the Electron test suite if the guard is ever removed, instead of the
CI job being silently killed by the Rust test itself.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC_TAURI = Path(__file__).resolve().parents[2] / "src-tauri"
# VP-1: platform/process was split into a directory — the facade
# (kill_process_tree) lives in mod.rs, the Unix helpers (signal_pid,
# enumerate_children_pgrep, kill_process_group_if_safe) in posix.rs.
_PROCESS_RS = _SRC_TAURI / "src" / "platform" / "process" / "mod.rs"
_POSIX_RS = _SRC_TAURI / "src" / "platform" / "process" / "posix.rs"


def _read(path: Path) -> str:
    assert path.exists(), f"missing source file: {path}"
    return path.read_text(encoding="utf-8")


def test_kill_process_tree_has_pid_zero_guard_before_platform_branches() -> None:
    """``kill_process_tree`` must no-op for pid 0 BEFORE any cfg branch.

    The guard must appear between the function signature and the first
    ``#[cfg(windows)]`` block so it short-circuits on EVERY platform
    (Windows included — ``taskkill /PID 0`` is also meaningless).
    """
    src = _read(_PROCESS_RS)

    # Capture the function body from the signature to the first cfg block.
    m = re.search(
        r"pub\(crate\) fn kill_process_tree\(pid: u32\) \{(.*?)#\[cfg\(windows\)\]",
        src,
        re.DOTALL,
    )
    assert m, "kill_process_tree body before #[cfg(windows)] not found"
    body = m.group(1)

    assert re.search(
        r"if pid == 0 \{\s*\n\s*log::debug!",
        body,
    ), (
        "kill_process_tree must guard `if pid == 0 { log::debug!(...) }` BEFORE "
        "the #[cfg(windows)] branch. Without it, Unix `pgrep -P 0` makes the DFS "
        "descend into the entire process tree and SIGKILL the CI runner agent "
        "(job cancelled with 'The runner has received a shutdown signal')."
    )


def test_signal_pid_guards_pid_zero() -> None:
    """``signal_pid(0, sig)`` must not call ``libc::kill`` (self-group kill)."""
    src = _read(_POSIX_RS)
    m = re.search(r"fn signal_pid\(pid: u32, sig: libc::c_int\) -> bool \{(.*?)\n\}", src, re.DOTALL)
    assert m, "signal_pid body not found"
    body = m.group(1)
    assert re.search(r"if pid == 0 \{\s*\n\s*log::debug!", body), (
        "signal_pid must guard pid 0: POSIX kill(0, sig) signals the CALLER's own "
        "process group (self-kill)."
    )
    assert "libc::kill" in body, "signal_pid must keep its libc::kill call for real pids"


def test_enumerate_children_pgrep_guards_pid_zero() -> None:
    """``enumerate_children_pgrep(0)`` must NOT run ``pgrep -P 0``."""
    src = _read(_POSIX_RS)
    m = re.search(
        r"fn enumerate_children_pgrep\(pid: u32\) -> Vec<u32> \{(.*?)\n\}", src, re.DOTALL
    )
    assert m, "enumerate_children_pgrep body not found"
    body = m.group(1)
    assert re.search(r"if pid == 0 \{\s*\n\s*log::debug!", body), (
        "enumerate_children_pgrep must guard pid 0: `pgrep -P 0` matches PID 1 "
        "(init) + kernel threads, which made kill_process_tree(0) walk the entire "
        "process tree and kill the CI runner."
    )


def test_kill_process_group_if_safe_guards_pid_zero() -> None:
    """``kill_process_group_if_safe(0, sig)`` must not resolve the caller's pgid."""
    src = _read(_POSIX_RS)
    m = re.search(
        r"fn kill_process_group_if_safe\(pid: u32, sig: libc::c_int\) \{(.*?)\n\}", src, re.DOTALL
    )
    assert m, "kill_process_group_if_safe body not found"
    body = m.group(1)
    assert re.search(r"if pid == 0 \{\s*\n\s*log::debug!", body), (
        "kill_process_group_if_safe must guard pid 0: getpgid(0) returns the "
        "caller's own pgid, routing the group signal at the caller's group."
    )
