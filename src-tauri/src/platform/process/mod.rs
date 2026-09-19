//! Platform-specific process-lifecycle helpers.
//!
//! Release-mode `CommandChild::Drop` does NOT kill the OS process; if the
//! host crashes the sidecar is orphaned (mic/port leak). This module
//! attaches kill-on-parent-exit to a freshly-spawned pid.
//!
//! Windows: Job Object + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
//! POSIX: detached `/bin/sh` reaper polling `kill -0 <parent_pid>` (1s)
//! then SIGKILL. Not `prctl(PR_SET_PDEATHSIG)` — Tauri externalBin has
//! no `pre_exec` hook.
//! NOTE: see docs/code-notes/tauri-host.md#kill-on-parent-exit

// ─── register_kill_on_parent_exit ────────────────────────────────

/// Best-effort kill-on-parent-exit for `pid`. Caller logs errors and
/// continues (spawn already happened). Does not panic.
pub(crate) fn register_kill_on_parent_exit(pid: u32) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        register_kill_on_parent_exit_windows(pid)
    }
    #[cfg(unix)]
    {
        return register_kill_on_parent_exit_posix(pid);
    }
    #[cfg(not(any(target_os = "windows", unix)))]
    {
        let _ = pid;
        log::warn!(
            "[process] register_kill_on_parent_exit: unsupported platform, \
             sidecar pid {} will NOT be auto-killed on host crash",
            pid
        );
        Ok(())
    }
}


#[cfg(target_os = "windows")]
#[path = "windows.rs"]
mod windows_impl;

#[cfg(target_os = "windows")]
use windows_impl::register_kill_on_parent_exit_windows;


#[cfg(unix)]
#[path = "posix.rs"]
mod posix_impl;

#[cfg(unix)]
use posix_impl::register_kill_on_parent_exit_posix;


/// Kill the process tree rooted at `pid` (the sidecar and its
/// descendants). Platform-native, best-effort: never panics.
///
/// On Unix this does a **recursive** depth-first walk over the
/// sidecar's descendants so ALL descendants are reaped, grandchildren
/// (native hotkey binary, model subprocesses) included. The root pid
/// itself is NOT killed here, the caller (`SidecarHandle::kill_tree`
/// / `spawn.rs` cleanup) kills the root separately via
/// `child.kill()` afterwards, so we focus on the descendants only.
///
/// # Implementation (syscall-based, no per-descendant shell-out)
///
/// Child enumeration is platform-stratified (see `enumerate_children`):
/// - **Linux**: reads `/proc/<pid>/task/<pid>/children` directly (a
///   single file read per pid, no fork/exec).
/// - **macOS / other Unix**: falls back to `pgrep -P <pid>` shell-out
///   (macOS has no `/proc`).
///
/// Per-pid signal delivery uses `libc::kill(2)` directly (see
/// `signal_pid`): NO `kill -TERM <pid>` / `kill -KILL <pid>`
/// shell-outs. The prior shell-out version forked+exec'd a child
/// process per descendant per signal phase (~5-10ms each on Linux);
/// for N descendants that was (1 + N) pgrep spawns + N TERM spawns +
/// N KILL spawns = 3N+1 process spawns per call. The syscall version
/// does the same work in-process via `libc::kill(2)` + a single
/// `/proc/<pid>/task/<pid>/children` read per pid, eliminating the
/// per-descendant fork/exec overhead.
///
/// The 200ms SIGTERM→SIGKILL grace `std::thread::sleep` is kept
/// (sync; the function is wrapped in `tokio::task::spawn_blocking`
/// by `SidecarHandle::kill_tree` so it doesn't stall a Tokio worker).
///
/// # Race-window mitigation (process-group kill)
///
/// The descendant snapshot is point-in-time: between the snapshot and
/// the `signal_pid` calls, the sidecar may spawn NEW children that the
/// snapshot missed. Those children would survive the per-pid kill and
/// keep holding the mic / IPC port. To close this race, we ALSO send
/// `kill(-<pgid>, SIGTERM)` (and later `kill(-<pgid>, SIGKILL)`) to
/// the sidecar's entire PROCESS GROUP via `signal_process_group` —
/// this catches any child spawned between the snapshot and the signal,
/// regardless of whether the snapshot saw it.
///
/// **Safety guard**: the sidecar is spawned via `tauri-plugin-shell`'s
/// `externalBin` API, which does NOT call `setsid()` / `setpgid()`.
/// The sidecar therefore inherits the HOST's process group by default.
/// Sending `kill(-<host_pgid>, ...)` would kill the HOST itself
/// (catastrophic). We ONLY send the process-group signal when
/// `getpgid(sidecar_pid) != getpgrp()`: i.e., the sidecar is
/// verifiably in its OWN group (which would require a future spawn-
/// path change to call `pre_exec(|| { setpgid(0, 0); Ok(()) })`, or
/// the Python sidecar to call `os.setsid()`). Until then, the
/// process-group kill is a no-op and we rely on the per-pid kills —
/// the race window is documented but not fully closed.
///
/// Exposed as `pub(crate)` so `spawn.rs`'s spawn-timeout cleanup paths
/// can call it directly (they only have the `CommandChild` /
/// `tokio::process::Child`, not a `SidecarHandle`, so they can't use
/// `kill_tree`).
pub(crate) fn kill_process_tree(pid: u32) {
    if pid == 0 {
        log::debug!(
            "[KILL-TREE] kill_process_tree(0) is a no-op (pid 0 is the kernel scheduler, not a process)"
        );
        return;
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        use std::process::Command;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let tool = "taskkill";
        match Command::new(tool)
            .args(["/F", "/T", "/PID", &pid.to_string()])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .creation_flags(CREATE_NO_WINDOW)
            .status()
        {
            Ok(s) if s.success() => {
                log::info!("[KILL-TREE] taskkill succeeded for pid={}", pid);
            }
            Ok(s) => {
                log::warn!(
                    "[KILL-TREE] {} exited with code {} for pid {}",
                    tool,
                    s.code()
                        .map(|c| c.to_string())
                        .unwrap_or_else(|| "<signal>".into()),
                    pid
                );
            }
            Err(e) => {
                log::warn!("[KILL-TREE] {} failed for pid={}: {}", tool, pid, e);
            }
        }
    }
    #[cfg(unix)]
    {
        use std::time::Duration;
        // SIGTERM→SIGKILL grace period is the named constant
        // `KILL_TREE_SIGTERM_GRACE_MS` in `util.rs` (was inline 200ms).
        use crate::util::KILL_TREE_SIGTERM_GRACE_MS;

        let mut all_descendants: Vec<u32> = Vec::new();
        let mut stack: Vec<u32> = vec![pid];
        while let Some(cur) = stack.pop() {
            for child_pid in posix_impl::enumerate_children(cur) {
                all_descendants.push(child_pid);
                stack.push(child_pid);
            }
        }

        let before_filter = all_descendants.len();
        all_descendants.retain(|&dpid| posix_impl::pid_is_alive(dpid));
        if all_descendants.len() != before_filter {
            log::debug!(
                "[KILL-TREE] dropped {} already-exited descendant(s) of pid {} before signalling",
                before_filter - all_descendants.len(),
                pid
            );
        }

        if all_descendants.is_empty() {
            log::debug!(
                "[KILL-TREE] no descendants for pid {}: skipping SIGTERM/SIGKILL cycle",
                pid
            );
            let term_fired = posix_impl::kill_process_group_if_safe(pid, libc::SIGTERM);
            if term_fired {
                std::thread::sleep(Duration::from_millis(KILL_TREE_SIGTERM_GRACE_MS));
                posix_impl::kill_process_group_if_safe(pid, libc::SIGKILL);
            } else {
                log::debug!(
                    "[KILL-TREE] process-group SIGTERM did not fire: skipping grace sleep + group SIGKILL entirely"
                );
            }
            return;
        }

        let pid_in_range = pid <= i32::MAX as u32;
        let sidecar_pgid: libc::pid_t = if pid_in_range {
            unsafe { libc::getpgid(pid as libc::pid_t) }
        } else {
            log::warn!(
                "[KILL-TREE] getpgid skipped for out-of-range pid {} (> i32::MAX), \
                 would truncate on cast to pid_t",
                pid
            );
            -1
        };

        // SIGTERM phase: per-pid kills (targeted) + process-group kill
        // (catches race-window children).
        for &dpid in &all_descendants {
            posix_impl::signal_pid(dpid, libc::SIGTERM);
        }
        if pid_in_range {
            posix_impl::signal_process_group(sidecar_pgid, libc::SIGTERM);
        }

        std::thread::sleep(Duration::from_millis(KILL_TREE_SIGTERM_GRACE_MS));

        // SIGKILL phase: per-pid kills (force) + process-group kill
        // (force, catches race-window children that ignored SIGTERM).
        for &dpid in &all_descendants {
            posix_impl::signal_pid(dpid, libc::SIGKILL);
        }
        // Process-group SIGKILL: force-kill any race-window child
        // that survived the SIGTERM phase. Same safety guard applies.
        if pid_in_range {
            posix_impl::signal_process_group(sidecar_pgid, libc::SIGKILL);
        }

        // Final summary line.
        log::info!(
            "[KILL-TREE] reaped {} descendants of pid {}",
            all_descendants.len(),
            pid
        );
    }
    #[cfg(not(any(target_os = "windows", unix)))]
    {
        let _ = pid;
        log::warn!(
            "[KILL-TREE] kill_process_tree is a no-op on this platform \
             (neither Windows nor Unix); pid {} will NOT be reaped: \
             the sidecar may leak the mic / IPC port until manually killed",
            pid
        );
    }
}

// Sibling test module: tests live in `tests.rs` in this directory
// (per C-TEST-5: no inline `#[cfg(test)] mod tests` blocks in
// production source).
#[cfg(test)]
#[path = "tests.rs"]
mod tests;
