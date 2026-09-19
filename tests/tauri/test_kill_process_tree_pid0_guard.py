"""Source-grep gate: `kill_process_tree(0)` must stay a safe no-op."""

from __future__ import annotations

import re
from pathlib import Path

_SRC_TAURI = Path(__file__).resolve().parents[2] / "src-tauri"
_PROCESS_RS = _SRC_TAURI / "src" / "platform" / "process" / "mod.rs"
_POSIX_RS = _SRC_TAURI / "src" / "platform" / "process" / "posix.rs"


def _read(path: Path) -> str:
    assert path.exists(), f"missing source file: {path}"
    return path.read_text(encoding="utf-8")


def test_kill_process_tree_has_pid_zero_guard_before_platform_branches() -> None:
    """``kill_process_tree`` must no-op for pid 0 BEFORE any cfg branch."""
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
        "signal_pid must guard pid 0: POSIX kill(0, sig) signals the CALLER's own process group (self-kill)."
    )
    assert "libc::kill" in body, "signal_pid must keep its libc::kill call for real pids"


def test_enumerate_children_pgrep_guards_pid_zero() -> None:
    """``enumerate_children_pgrep(0)`` must NOT run ``pgrep -P 0``."""
    src = _read(_POSIX_RS)
    m = re.search(r"fn enumerate_children_pgrep\(pid: u32\) -> Vec<u32> \{(.*?)\n\}", src, re.DOTALL)
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
    m = re.search(r"fn kill_process_group_if_safe\(pid: u32, sig: libc::c_int\) -> bool \{(.*?)\n\}", src, re.DOTALL)
    assert m, "kill_process_group_if_safe body not found"
    body = m.group(1)
    assert re.search(r"if pid == 0 \{\s*\n\s*log::debug!", body), (
        "kill_process_group_if_safe must guard pid 0: getpgid(0) returns the "
        "caller's own pgid, routing the group signal at the caller's group."
    )
