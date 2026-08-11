// POSIX implementation of the kill-on-parent-exit guarantee (reaper
// subprocess) plus the Unix kill-tree helpers (in-process syscall
// per-pid signals, child enumeration, and the process-group race-
// window catcher). VP-1 split: body of the former inline
// `mod posix_impl` + the Unix helper functions in `platform/process.rs`;
// declared from `process/mod.rs` via `#[path = "posix.rs"] mod posix_impl;`.

use std::os::unix::process::CommandExt;
use std::process::Command;

/// The reaper shell script. Uses ONLY POSIX shell features so it
/// works on both `dash` (Linux `/bin/sh`) and `bash` (macOS
/// `/bin/sh`).
///
/// Behavior:
/// 1. `target` = the sidecar pid to kill on parent death.
/// 2. `orig_parent` = the reaper's own PPID at startup (== the
///    Tauri host pid).
/// 3. Loop:
///    a. If `target` is already dead → exit 0 (nothing to do).
///    b. If `orig_parent` is dead → `kill -9 $target` → exit 0.
///    c. Sleep 1s, retry.
///
/// The `kill -9` is unconditional (SIGKILL can't be caught). The
/// 1s poll interval is a tradeoff between CPU cost (negligible —
/// one `kill -0` syscall per second) and orphan-detection latency
/// (≤ 1s after host death, which is well within the "user notices
/// the mic is still on" threshold of ~5s).
///
/// The script is passed to `/bin/sh -c "<script>"` with the
/// target pid substituted via `format!` (the pid is a `u32` so
/// there's no shell-injection risk — only digits can appear).
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

/// POSIX: spawn a reaper subprocess that kills the sidecar
/// pid when the Tauri host dies.
///
/// The reaper is spawned via `/bin/sh -c "<script>"` with
/// `pre_exec` calling `setsid()` to detach it into its own
/// session/process group. This ensures:
/// - The reaper doesn't receive signals sent to the host's
///   process group (e.g. terminal Ctrl-C).
/// - The reaper's stdout/stderr don't interfere with the host's
///   (we redirect them to /dev/null).
pub(crate) fn register_kill_on_parent_exit_posix(pid: u32) -> Result<(), String> {
    let script = REAPER_SCRIPT_TEMPLATE.replace("{pid}", &pid.to_string());

    let mut cmd = Command::new("/bin/sh");
    cmd.arg("-c").arg(&script);
    // Redirect stdio so the reaper is silent (it has nothing
    // useful to say in normal operation; errors are surfaced via
    // the host's log when the reaper fails to spawn).
    cmd.stdin(std::process::Stdio::null());
    cmd.stdout(std::process::Stdio::null());
    cmd.stderr(std::process::Stdio::null());

    // SAFETY: `pre_exec` runs after `fork()` but before `exec()`.
    // `setsid()` is async-signal-safe (it's a thin syscall wrapper)
    // and is the correct way to detach the child into a new
    // session. The closure must NOT do anything that isn't
    // async-signal-safe (no malloc, no logging, no std I/O).
    // `setsid()` only fails if the caller is already a process
    // group leader (which a freshly-forked child is NOT), so the
    // error path is effectively dead but defensive.
    //
    // Detaching the reaper into its own session is REQUIRED for
    // correct Ctrl-C behavior: a terminal SIGINT is delivered to
    // every process in the host's process group. Without
    // `setsid()`, the reaper would die at the same time as the
    // host — before it could kill the sidecar — defeating the
    // kill-on-parent-exit guarantee for terminal-initiated
    // shutdowns. The hard-crash case (SIGKILL, segfault) was
    // already handled because the reaper is a separate process
    // that survives the host's death; this `setsid()` call adds
    // the same protection for the soft-signal case (Ctrl-C).
    //
    // `io::Error::last_os_error()` is async-signal-safe in
    // practice — it stores the raw `errno` integer in the
    // `Error`'s `Repr::Os(i32)` variant without allocating.
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

    // Spawn the reaper. We do NOT wait for it — it runs in the
    // background for the lifetime of the host (and a few seconds
    // beyond, to detect parent death and kill the sidecar).
    cmd.spawn()
        .map_err(|e| format!("failed to spawn reaper subprocess: {}", e))?;

    log::info!(
        "[process] spawned POSIX reaper subprocess to kill sidecar pid {} on host exit",
        pid
    );
    Ok(())
}

// ─── per-pid + child-enumeration helpers (Unix-only) ────────────────────
//
// In-process syscall replacements for the prior `kill -<sig> <pid>`
// shell-outs (and the `pgrep -P <pid>` shell-out on Linux). Each
// shell-out forked+exec'd a child process (~5-10ms each on Linux); for
// N descendants that was (1 + N) pgrep spawns + N TERM spawns + N KILL
// spawns = 3N+1 process spawns per `kill_process_tree` call. The
// syscall replacements below do the same work via `libc::kill(2)` and
// a single `/proc/<pid>/task/<pid>/children` read on Linux (or
// `pgrep -P <pid>` fallback on macOS/other Unix), eliminating the
// per-descendant fork/exec overhead.

/// Send `sig` to `pid` via the `libc::kill(2)` syscall (best-effort:
/// logs on failure but doesn't abort the caller). Returns `true` if a
/// non-ESRCH failure occurred (i.e. the signal was NOT delivered AND
/// the reason was not "pid already exited"); `false` otherwise (signal
/// delivered, OR ESRCH race-window where the pid was already gone).
///
/// Replaces the prior `Command::new("kill").args(["-TERM" | "-KILL",
/// &pid]).status()` shell-out — same POSIX semantics (signal
/// delivery to the named pid) without the fork+exec overhead per
/// descendant. ESRCH (no such process) is expected for a descendant
/// that already exited between the snapshot and the signal —
/// downgraded to `debug!` to avoid log spam during the SIGKILL phase
/// (every SIGKILL on a SIGTERM-reaped pid returns ESRCH).
///
/// Per-pid non-ESRCH failures (e.g. EPERM on a root-owned descendant)
/// are also demoted to `debug!` to avoid log spam — a single
/// `kill_process_tree` call iterates many descendants, and the prior
/// per-pid `warn!` made log triage difficult. The caller
/// (`kill_process_tree`) aggregates the `true` returns into ONE
/// summary `warn!` at the end of the call so the operator still sees
/// the failure (once per `kill_process_tree` invocation, not N times).
#[cfg(unix)]
pub(super) fn signal_pid(pid: u32, sig: libc::c_int) -> bool {
    // pid 0 guard: POSIX `kill(0, sig)` signals the CALLING process's
    // own process group — a self-kill. Real descendant pids are never
    // 0, but a malformed input must not be able to group-signal the
    // caller. Mirrors the `pid > i32::MAX` range guard below.
    if pid == 0 {
        log::debug!(
            "[KILL-TREE] signal_pid skipped for pid 0 (would signal the caller's own process group)"
        );
        return false;
    }
    // Range guard: `pid` is `u32` but `libc::pid_t` is `i32` on all
    // supported Unix platforms. A `pid > i32::MAX` would silently
    // wrap to a NEGATIVE `pid_t` (which POSIX `kill(2)` interprets as
    // a process-GROUP signal — `kill(-pgid, sig)` — and would signal
    // an UNRELATED process group). The kernel never assigns pids >
    // `i32::MAX` on any real system, but defensive guarding prevents
    // a malformed `u32::MAX` from triggering a process-group signal.
    // Returns `false` (no failure) because the pid is provably
    // nonexistent (the kernel doesn't have it), so the caller's
    // aggregation logic stays consistent.
    if pid > i32::MAX as u32 {
        log::debug!(
            "[KILL-TREE] signal_pid skipped for out-of-range pid {} (> i32::MAX) — would truncate on cast to pid_t",
            pid
        );
        return false;
    }
    let rc = unsafe { libc::kill(pid as libc::pid_t, sig) };
    if rc != 0 {
        let errno = std::io::Error::last_os_error().raw_os_error().unwrap_or(0);
        if errno == libc::ESRCH {
            // Expected: the pid already exited (race between snapshot
            // and signal). Not a warning — common during the SIGKILL
            // phase on pids that already reaped themselves after
            // SIGTERM.
            log::debug!(
                "[KILL-TREE] kill({}, {}) returned ESRCH (pid already exited)",
                pid,
                signal_name(sig)
            );
            // ESRCH is not a failure — the pid is already gone, which
            // is the desired end state.
            false
        } else {
            // Demoted from `warn!` to `debug!` per the aggregation
            // contract: the caller (`kill_process_tree`) counts the
            // `true` returns from this fn and emits ONE summary
            // `warn!` at the end. Per-pid `warn!` spam made shutdown
            // log triage difficult (a sidecar with N descendants
            // produced 2N+ warn lines per `kill_process_tree` call,
            // one per signal phase per pid).
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
/// / `"sig=<n>"` for unknown signals). Pure formatter — no syscall.
#[cfg(unix)]
pub(super) fn signal_name(sig: libc::c_int) -> &'static str {
    match sig {
        libc::SIGTERM => "SIGTERM",
        libc::SIGKILL => "SIGKILL",
        _ => "sig=<unknown>",
    }
}

/// Enumerate the direct child pids of `pid`. Platform-stratified:
///
/// - **Linux** (`target_os = "linux"`): reads
///   `/proc/<pid>/task/<pid>/children` directly — a single file read,
///   no fork/exec. The kernel maintains this file exactly for this
///   use-case (child-process enumeration for cleanup signals).
///
/// - **macOS / other Unix**: falls back to `pgrep -P <pid>` shell-out
///   (macOS doesn't have `/proc`). Returns an empty `Vec` on any
///   failure (best-effort — `kill_process_tree` is best-effort
///   overall).
///
/// Returns ONLY the direct children — `kill_process_tree` does the
/// recursive DFS itself by pushing each child back onto its own
/// stack. The returned `Vec` is point-in-time: between the snapshot
/// and the `signal_pid` calls, the parent may spawn NEW children that
/// the snapshot missed (the race window closed separately by the
/// `signal_process_group` call inside `kill_process_tree`).
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

/// Linux `/proc/<pid>/task/<pid>/children` reader. Returns the direct
/// child pids as parsed from the space-separated decimal list. Empty
/// Vec on any IO / parse error (the caller falls back to `pgrep`).
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

/// `pgrep -P <pid>` fallback for non-Linux Unix (macOS). Returns the
/// direct child pids parsed from pgrep's stdout. Empty Vec on any
/// failure (best-effort).
#[cfg(unix)]
pub(super) fn enumerate_children_pgrep(pid: u32) -> Vec<u32> {
    // pid 0 guard: `pgrep -P 0` matches PID 1 (init) + kernel threads,
    // and the DFS in `kill_process_tree` would then descend into the
    // ENTIRE process tree (the caller's host included). Never run
    // pgrep with a 0 parent — return empty (best-effort contract).
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
            // Exit 1 = no children (normal leaf) — skip logging.
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
// `abs(pid)`" — a POSIX-guaranteed behavior (see `man 2 kill`).
//
// The CRITICAL safety guard: we ONLY send the group signal when the
// sidecar's pgid differs from the host's own pgid (`getpgrp()`). The
// sidecar is spawned via `tauri-plugin-shell`'s `externalBin` API,
// which does NOT call `setsid()` / `setpgid()` — so the sidecar
// inherits the HOST's pgid. Sending `kill -<host_pgid>` would kill the
// HOST (and all its children, including unrelated Tauri threads). This
// guard makes the process-group kill a safe no-op until the spawn path
// is updated to put the sidecar in its own group.

#[cfg(unix)]
pub(super) fn signal_process_group(sidecar_pgid: libc::pid_t, sig: libc::c_int) {
    // Safety guard: refuse to signal a group that includes the host.
    // `sidecar_pgid <= 0` means `getpgid` failed (the sidecar already
    // exited, or the pid is invalid) — skip the group kill entirely.
    // `sidecar_pgid == getpgrp()` means the sidecar shares the host's
    // pgid — sending the group signal would kill the host.
    if sidecar_pgid <= 0 {
        return;
    }
    let host_pgid = unsafe { libc::getpgrp() };
    if sidecar_pgid == host_pgid {
        // The sidecar is in the host's process group. Sending a signal
        // to `-<host_pgid>` would kill the host. Skip — we rely on
        // the per-pid kills in `kill_process_tree` instead. This is
        // the current production state (the spawn path doesn't put the
        // sidecar in its own group). Logged at debug level to avoid
        // spamming the log on every shutdown (this is expected, not an
        // error).
        log::debug!(
            "[KILL-TREE] skipping process-group signal (sidecar pgid {} == host pgid {} — \
             would kill the host; rely on per-pid kills instead)",
            sidecar_pgid,
            host_pgid
        );
        return;
    }
    // Send the signal to the entire process group. `kill(-pgid, sig)`
    // is the POSIX way to signal a process group. Returns 0 on
    // success, -1 on error (errno set).
    let rc = unsafe { libc::kill(-sidecar_pgid, sig) };
    if rc != 0 {
        let errno = std::io::Error::last_os_error().raw_os_error().unwrap_or(0);
        // ESRCH (No such process) is expected if the group has already
        // exited — not a warning-worthy condition.
        if errno != libc::ESRCH {
            // Log format: `kill(-<pgid>, <signal-name>) failed: errno=<n> (<msg>)`.
            // The prior format `kill(-TERM -12345, 15)` was nonsensical
            // (mixed the literal "-TERM" with the pgid), making log
            // triage harder. The new format mirrors `man 2 kill`
            // invocation: `kill(-<pgid>, <SIGNAME>)`.
            log::warn!(
                "[KILL-TREE] kill(-{}, {}) failed: errno={} ({})",
                sidecar_pgid,
                signal_name(sig),
                errno,
                std::io::Error::last_os_error()
            );
        }
    } else {
        log::info!(
            "[KILL-TREE] sent signal {} to process group -{} (race-window catcher)",
            sig,
            sidecar_pgid
        );
    }
}

/// Convenience wrapper: resolve the sidecar's pgid from its pid, then
/// call `signal_process_group`. Used by the early-return path in
/// `kill_process_tree` (when `all_descendants` is empty, we still want
/// to attempt the process-group kill to catch race-window children).
#[cfg(unix)]
pub(super) fn kill_process_group_if_safe(pid: u32, sig: libc::c_int) {
    // pid 0 guard: `getpgid(0)` returns the CALLING process's own
    // pgid, which would route the group signal to the caller's own
    // group (the `signal_process_group` host-pgid check happens to
    // catch it, but never get there). Real sidecar pids are never 0.
    if pid == 0 {
        log::debug!(
            "[KILL-TREE] kill_process_group_if_safe skipped for pid 0 (getpgid(0) would return the caller's own pgid)"
        );
        return;
    }
    // Range guard: same as `signal_pid` — `pid_t` is `i32`, so a
    // `u32` value > `i32::MAX` would wrap to a negative `pid_t` and
    // `getpgid` would interpret it as a process-group lookup of an
    // unrelated group. Skip the call entirely in that case.
    if pid > i32::MAX as u32 {
        log::debug!(
            "[KILL-TREE] kill_process_group_if_safe skipped for out-of-range pid {} (> i32::MAX)",
            pid
        );
        return;
    }
    let sidecar_pgid = unsafe { libc::getpgid(pid as libc::pid_t) };
    signal_process_group(sidecar_pgid, sig);
}
