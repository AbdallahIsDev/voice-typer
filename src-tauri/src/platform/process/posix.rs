// POSIX implementation of the kill-on-parent-exit guarantee (reaper
// subprocess) plus the Unix kill-tree helpers (in-process syscall
// per-pid signals, child enumeration, and the process-group race-
// window catcher). Split: body of the former inline
// `mod posix_impl` + the Unix helper functions in `platform/process.rs`;
// declared from `process/mod.rs` via `#[path = "posix.rs"] mod posix_impl;`.

use std::os::unix::process::CommandExt;
use std::process::Command;

const REAPER_SCRIPT_TEMPLATE: &str = "\
target={pid}
orig_parent=$PPID
while true; do
    if ! kill -0 \"$target\" 2>/dev/null; then
        exit 0
    fi
    if ! kill -0 \"$orig_parent\" 2>/dev/null; then
        kill -9 \"$target\" 2>/dev/null
        exit 0
    fi
    sleep 1
done
";

pub(crate) fn register_kill_on_parent_exit_posix(pid: u32) -> Result<(), String> {
    let script = REAPER_SCRIPT_TEMPLATE.replace("{pid}", &pid.to_string());

    let mut cmd = Command::new("/bin/sh");
    cmd.arg("-c").arg(&script);
    cmd.stdin(std::process::Stdio::null());
    cmd.stdout(std::process::Stdio::null());
    cmd.stderr(std::process::Stdio::null());

    unsafe {
        cmd.pre_exec(|| {
            // SAFETY: `setsid()` is a thin syscall wrapper and is
            // async-signal-safe per POSIX.1.
            if libc::setsid() == -1 {
                Err(std::io::Error::last_os_error())
            } else {
                Ok(())
            }
        });
    }

    cmd.spawn()
        .map_err(|e| format!("failed to spawn reaper subprocess: {}", e))?;

    log::info!(
        "[process] spawned POSIX reaper subprocess to kill sidecar pid {} on host exit",
        pid
    );
    Ok(())
}


/// Send `sig` to `pid` via the `libc::kill(2)` syscall (best-effort:
/// logs on failure but doesn't abort the caller). Returns `true` if a
/// non-ESRCH failure occurred (i.e. the signal was NOT delivered AND
/// the reason was not "pid already exited"); `false` otherwise (signal
/// delivered, OR ESRCH race-window where the pid was already gone).
///
/// Replaces the prior `Command::new("kill").args(["-TERM" | "-KILL",
/// &pid]).status()` shell-out: same POSIX semantics (signal
/// delivery to the named pid) without the fork+exec overhead per
/// descendant. ESRCH (no such process) is expected for a descendant
/// that already exited between the snapshot and the signal —
/// downgraded to `debug!` to avoid log spam during the SIGKILL phase
/// (every SIGKILL on a SIGTERM-reaped pid returns ESRCH).
///
/// Per-pid non-ESRCH failures (e.g. EPERM on a root-owned descendant)
/// are also demoted to `debug!` to avoid log spam, a single
/// `kill_process_tree` call iterates many descendants, and the prior
/// per-pid `warn!` made log triage difficult. The caller
/// (`kill_process_tree`) aggregates the `true` returns into ONE
/// summary `warn!` at the end of the call so the operator still sees
/// the failure (once per `kill_process_tree` invocation, not N times).
#[cfg(unix)]
pub(super) fn signal_pid(pid: u32, sig: libc::c_int) -> bool {
    if pid == 0 {
        log::debug!(
            "[KILL-TREE] signal_pid skipped for pid 0 (would signal the caller's own process group)"
        );
        return false;
    }
    if pid > i32::MAX as u32 {
        log::debug!(
            "[KILL-TREE] signal_pid skipped for out-of-range pid {} (> i32::MAX), would truncate on cast to pid_t",
            pid
        );
        return false;
    }
    let rc = unsafe { libc::kill(pid as libc::pid_t, sig) };
    if rc != 0 {
        let errno = std::io::Error::last_os_error().raw_os_error().unwrap_or(0);
        if errno == libc::ESRCH {
            log::debug!(
                "[KILL-TREE] kill({}, {}) returned ESRCH (pid already exited)",
                pid,
                signal_name(sig)
            );
            // ESRCH is not a failure, the pid is already gone, which
            // is the desired end state.
            false
        } else {
            log::debug!(
                "[KILL-TREE] kill({}, {}) failed: errno={} ({})",
                pid,
                signal_name(sig),
                errno,
                std::io::Error::last_os_error()
            );
            // Non-ESRCH failure: signal the caller to aggregate.
            true
        }
    } else {
        // Signal delivered successfully.
        false
    }
}

/// Human-readable signal name for log lines (`"SIGTERM"` / `"SIGKILL"`
/// / `"sig=<n>"` for unknown signals). Pure formatter, no syscall.
#[cfg(unix)]
pub(super) fn signal_name(sig: libc::c_int) -> &'static str {
    match sig {
        libc::SIGTERM => "SIGTERM",
        libc::SIGKILL => "SIGKILL",
        _ => "sig=<unknown>",
    }
}

/// Liveness probe: does `pid` currently exist? Used by
/// `kill_process_tree` to drop already-exited descendants from
/// the snapshot BEFORE paying any signal or grace-sleep cost.
///
/// Uses `kill(pid, 0)`: signal 0 performs permission/existence checks
/// but delivers no signal. Semantics:
/// - `rc == 0`            → process exists and we may signal it → alive
/// - `errno == ESRCH`     → no such process → dead
/// - `errno == EPERM`     → process EXISTS but belongs to another user
///                          (we may not signal it, yet it is running)
///                          → treated as alive (killing it would fail
///                          with EPERM anyway, but skipping the attempt
///                          would misreport the tree as empty).
///
/// Guards mirror `signal_pid`: pid 0 and pids > `i32::MAX` are reported
/// dead without a syscall (0 would probe the caller's own process
/// group; the cast would wrap negative).
#[cfg(unix)]
pub(super) fn pid_is_alive(pid: u32) -> bool {
    if pid == 0 || pid > i32::MAX as u32 {
        return false;
    }
    let rc = unsafe { libc::kill(pid as libc::pid_t, 0) };
    if rc == 0 {
        return true;
    }
    let errno = std::io::Error::last_os_error().raw_os_error().unwrap_or(0);
    // EPERM ⇒ the process exists but is not ours to signal. ESRCH ⇒ gone.
    errno == libc::EPERM
}

#[cfg(unix)]
pub(super) fn enumerate_children(pid: u32) -> Vec<u32> {
    #[cfg(target_os = "linux")]
    {
        enumerate_children_procfs(pid).unwrap_or_else(|e| {
            log::debug!(
                "[KILL-TREE] /proc/{}/task/{}/children read failed (falling back to pgrep): {}",
                pid,
                pid,
                e
            );
            enumerate_children_pgrep(pid)
        })
    }
    #[cfg(not(target_os = "linux"))]
    {
        enumerate_children_pgrep(pid)
    }
}

#[cfg(target_os = "linux")]
pub(super) fn enumerate_children_procfs(pid: u32) -> Result<Vec<u32>, std::io::Error> {
    let path = format!("/proc/{}/task/{}/children", pid, pid);
    let contents = std::fs::read_to_string(&path)?;
    let mut out = Vec::new();
    for tok in contents.split_whitespace() {
        if let Ok(child_pid) = tok.parse::<u32>() {
            out.push(child_pid);
        }
    }
    Ok(out)
}

#[cfg(unix)]
pub(super) fn enumerate_children_pgrep(pid: u32) -> Vec<u32> {
    if pid == 0 {
        log::debug!(
            "[KILL-TREE] enumerate_children_pgrep skipped for pid 0 (pgrep -P 0 would match init + kernel threads)"
        );
        return Vec::new();
    }
    use std::process::Command;
    let pgrep = Command::new("pgrep")
        .args(["-P", &pid.to_string()])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .output();
    match pgrep {
        Ok(out) if out.status.success() => {
            let stdout = String::from_utf8_lossy(&out.stdout);
            stdout
                .lines()
                .filter_map(|line| line.trim().parse::<u32>().ok())
                .collect()
        }
        Ok(out) => {
            // Exit 1 = no children (normal leaf), skip logging.
            if out.status.code() != Some(1) {
                log::warn!(
                    "[KILL-TREE] pgrep exited with code {:?} for pid {}",
                    out.status.code(),
                    pid
                );
            }
            Vec::new()
        }
        Err(e) => {
            log::warn!("[KILL-TREE] pgrep failed for pid={}: {}", pid, e);
            Vec::new()
        }
    }
}

// ─── process-group signal helpers (Unix-only) ───────────────────────────
//
// Helpers for the race-window mitigation in `kill_process_tree`. These
// send a signal to the sidecar's entire process group via
// `libc::kill(-pgid, sig)`. The negative `pid` argument to `kill(2)`
// means "send to every process in the process group whose ID is
// `abs(pid)`": a POSIX-guaranteed behavior (see `man 2 kill`).
//
// The CRITICAL safety guard: we ONLY send the group signal when the
// sidecar's pgid differs from the host's own pgid (`getpgrp()`). The
// sidecar is spawned via `tauri-plugin-shell`'s `externalBin` API,
// which does NOT call `setsid()` / `setpgid()`, so the sidecar
// inherits the HOST's pgid. Sending `kill -<host_pgid>` would kill the
// HOST (and all its children, including unrelated Tauri threads). This
// guard makes the process-group kill a safe no-op until the spawn path
// is updated to put the sidecar in its own group.

#[cfg(unix)]
pub(super) fn signal_process_group(sidecar_pgid: libc::pid_t, sig: libc::c_int) -> bool {
    if sidecar_pgid <= 0 {
        return false;
    }
    let host_pgid = unsafe { libc::getpgrp() };
    if sidecar_pgid == host_pgid {
        log::debug!(
            "[KILL-TREE] skipping process-group signal (sidecar pgid {} == host pgid {}: \
             would kill the host; rely on per-pid kills instead)",
            sidecar_pgid,
            host_pgid
        );
        return false;
    }
    let rc = unsafe { libc::kill(-sidecar_pgid, sig) };
    if rc != 0 {
        let errno = std::io::Error::last_os_error().raw_os_error().unwrap_or(0);
        // ESRCH (No such process) is expected if the group has already
        // exited: not a warning-worthy condition.
        if errno != libc::ESRCH {
            log::warn!(
                "[KILL-TREE] kill(-{}, {}) failed: errno={} ({})",
                sidecar_pgid,
                signal_name(sig),
                errno,
                std::io::Error::last_os_error()
            );
        }
        return false;
    }
    log::info!(
        "[KILL-TREE] sent signal {} to process group -{} (race-window catcher)",
        sig,
        sidecar_pgid
    );
    true
}

/// Convenience wrapper: resolve the sidecar's pgid from its pid, then
/// call `signal_process_group`. Used by the early-return path in
/// `kill_process_tree` (when `all_descendants` is empty, we still want
/// to attempt the process-group kill to catch race-window children).
#[cfg(unix)]
pub(super) fn kill_process_group_if_safe(pid: u32, sig: libc::c_int) -> bool {
    if pid == 0 {
        log::debug!(
            "[KILL-TREE] kill_process_group_if_safe skipped for pid 0 (getpgid(0) would return the caller's own pgid)"
        );
        return false;
    }
    if pid > i32::MAX as u32 {
        log::debug!(
            "[KILL-TREE] kill_process_group_if_safe skipped for out-of-range pid {} (> i32::MAX)",
            pid
        );
        return false;
    }
    let sidecar_pgid = unsafe { libc::getpgid(pid as libc::pid_t) };
    signal_process_group(sidecar_pgid, sig)
}
