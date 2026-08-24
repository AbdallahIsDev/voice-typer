//! Platform-specific process-lifecycle helpers.
//!
//! The release-mode Tauri sidecar (spawned via
//! `tauri-plugin-shell`'s `externalBin` API) returns a
//! `CommandChild` whose `Drop` implementation does NOT kill the
//! underlying OS process. If the host process crashes (segfault,
//! OOM kill, `kill -9`), the sidecar Python process is orphaned
//! and keeps running with the microphone, the IPC port, and the
//! native hotkey binary child still held — a privacy + resource
//! leak. This module attaches a kill-on-parent-exit guarantee to
//! a freshly-spawned sidecar pid so the orphan is reaped when the
//! host dies abnormally.
//!
//! # Platform strategy
//!
//! - **Windows**: the sidecar pid is assigned to a process-wide
//!   Job Object configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
//!   The Job Object handle is stored in a process-wide static so it
//!   stays alive for the lifetime of the host process. When the host
//!   exits (for any reason — normal exit, crash, `TerminateProcess`),
//!   Windows closes the Job Object handle, which triggers the kill-
//!   on-job-close limit and terminates every process assigned to the
//!   Job Object (including the sidecar and its own subprocesses if
//!   they were also assigned — which they aren't here, but the
//!   sidecar's own children will die when the sidecar dies because
//!   the sidecar uses `kill_on_drop`-equivalent patterns in its
//!   Python shutdown path).
//!
//! - **POSIX (Linux/macOS)**: a small "reaper" subprocess is spawned
//!   via `/bin/sh` that polls `kill -0 <parent_pid>` once per
//!   second. When the parent (the Tauri host) dies — for any
//!   reason, including `SIGKILL` which the host can't intercept —
//!   the reaper detects the failed `kill -0` and sends `SIGKILL` to
//!   the sidecar pid. The reaper is detached into its own session
//!   via `setsid()` so it doesn't receive signals sent to the host's
//!   process group. The reaper also exits cleanly when the sidecar
//!   target dies (so a normally-shutdown sidecar doesn't keep a
//!   zombie reaper running).
//!
//!   This is a subprocess-based reaper rather than the preferred
//!   `prctl(PR_SET_PDEATHSIG, SIGKILL)` because the latter can only
//!   be set INSIDE the child process (after fork, before exec) —
//!   and the Tauri `externalBin` API does NOT expose a `pre_exec`
//!   hook. The reaper subprocess approach is the next-best portable
//!   option; the polling interval (1s) is a deliberate tradeoff
//!   between CPU cost and orphan-detection latency.
//!
//! # Validation
//!
//! - **VALIDATE ON LINUX HOST**: spawn the release sidecar, kill
//!   the Tauri host with `kill -9 <host_pid>`, verify the sidecar
//!   pid is gone within ~2s (1s poll + 1s grace). Test command:
//!   ```sh
//!   cargo tauri build && \
//!     ./target/release/voice-typer-tauri & sleep 5 && \
//!     HOST_PID=$! && \
//!     SIDECAR_PID=$(pgrep -f python-sidecar) && \
//!     kill -9 $HOST_PID && \
//!     sleep 3 && \
//!     (kill -0 $SIDECAR_PID 2>/dev/null && echo FAIL || echo OK)
//!   ```
//! - **VALIDATE ON MACOS HOST**: same as Linux but the sidecar
//!   binary name may differ (`python-sidecar-x86_64-apple-darwin`).
//! - **VALIDATE ON WINDOWS HOST**: spawn the release sidecar, kill
//!   the Tauri host via Task Manager (or `taskkill /F /PID
//!   <host_pid>`), verify the sidecar pid is gone immediately
//!   (kill-on-job-close is synchronous). Test command (PowerShell):
//!   ```powershell
//!   cargo tauri build
//!   Start-Process .\target\release\voice-typer-tauri.exe
//!   $hostPid = (Get-Process voice-typer-tauri).Id
//!   $sidecarPid = (Get-Process python-sidecar -ErrorAction SilentlyContinue).Id
//!   Stop-Process -Id $hostPid -Force
//!   Start-Sleep -Seconds 2
//!   if (Get-Process -Id $sidecarPid -ErrorAction SilentlyContinue) {
//!       Write-Host "FAIL"
//!   } else {
//!       Write-Host "OK"
//!   }
//!   ```

// ─── register_kill_on_parent_exit ────────────────────────────────
//
// The single public entry point. Dispatches by `#[cfg(target_os = ...)]`
// to the platform-specific implementation below. The function is
// best-effort: errors are returned as `Err(String)` so the caller can
// log them, but a failure here does NOT abort the sidecar spawn (the
// sidecar is already running — killing the host's spawn path wouldn't
// help). The caller in `sidecar::spawn::spawn_sidecar_release` logs
// the error and continues.

/// Attach a kill-on-parent-exit guarantee to the given pid. After this
/// returns `Ok(())`, the OS will (best-effort) terminate the process
/// identified by `pid` when the calling host process exits — whether
/// normally or via a hard crash that bypasses user-space exit handlers.
///
/// Returns `Err(String)` on best-effort failure (e.g. the Job Object
/// syscall failed on Windows, or the reaper subprocess failed to spawn
/// on POSIX). The caller should log the error and continue — the
/// sidecar is already running, and the missing kill-on-exit guarantee
/// is a degraded-mode fallback, not a fatal condition.
///
/// # Panics
///
/// This function does not panic. All syscalls are checked and converted
/// to `Err(String)` on failure.
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
        // Unsupported platform — log and return Ok so the caller doesn't
        // treat this as a fatal error. The sidecar will still run; it
        // just won't have the kill-on-parent-exit guarantee.
        log::warn!(
            "[process] register_kill_on_parent_exit: unsupported platform — \
             sidecar pid {} will NOT be auto-killed on host crash",
            pid
        );
        Ok(())
    }
}

// ─── Windows: Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE ───────
//
// The Job Object is created ONCE per process (lazy-init via
// `Mutex<Option<JobHandle>>`) and shared across all sidecar spawns.
// Every successful `register_kill_on_parent_exit` call assigns the new
// pid to the same Job Object. When the host process exits, Windows
// closes all handles (including the Job Object handle stored in the
// static), which triggers the `KILL_ON_JOB_CLOSE` limit and terminates
// every process assigned to the Job Object.
//
// The Job Object is created with:
//   - `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`: kill all assigned processes
//     when the LAST handle to the Job Object is closed.
//   - `JOB_OBJECT_LIMIT_BREAKAWAY_OK`: allow child processes of the
//     sidecar to break away from the Job Object if they choose to
//     (this is defensive — the sidecar's children aren't expected to
//     call `SetProcessJobObject`, but if they do, we don't want the
//     breakaway to silently fail and cause confusing behavior).

#[cfg(target_os = "windows")]
#[path = "windows.rs"]
mod windows_impl;

#[cfg(target_os = "windows")]
use windows_impl::register_kill_on_parent_exit_windows;

// ─── POSIX: reaper subprocess ──────────────────────────────────────────
//
// Spawns a small `/bin/sh` subprocess that polls `kill -0 <parent_pid>`
// once per second. When the parent (Tauri host) dies — for any reason,
// including `SIGKILL` — the reaper detects the failed `kill -0` and
// sends `SIGKILL` to the sidecar pid. The reaper also exits cleanly
// when the sidecar target dies.
//
// The reaper is detached into its own session via `setsid()` so it
// doesn't receive signals sent to the host's process group (e.g.
// `Ctrl-C` in the terminal that launched the host — the host should
// die and the reaper should then kill the sidecar, not die itself
// before it can do its job).

#[cfg(unix)]
#[path = "posix.rs"]
mod posix_impl;

#[cfg(unix)]
use posix_impl::register_kill_on_parent_exit_posix;

// ─── kill_process_tree ────────────────────────────────────────────────────
//
// Sibling helper to `register_kill_on_parent_exit`. Both deal with
// process-tree teardown. `kill_process_tree` is the *immediate* kill
// (used by `SidecarHandle::kill_tree` and `spawn.rs` cleanup paths);
// `register_kill_on_parent_exit` is the *deferred* kill (used on host
// crash). Both live here in `platform::process` so the host's process-
// control primitives aren't scattered across modules.

/// Kill the process tree rooted at `pid` (the sidecar and its
/// descendants). Platform-native, best-effort — never panics.
///
/// On Unix this does a **recursive** depth-first walk over the
/// sidecar's descendants so ALL descendants are reaped — grandchildren
/// (native hotkey binary, model subprocesses) included. The root pid
/// itself is NOT killed here — the caller (`SidecarHandle::kill_tree`
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
/// `signal_pid`) — NO `kill -TERM <pid>` / `kill -KILL <pid>`
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
/// `getpgid(sidecar_pid) != getpgrp()` — i.e., the sidecar is
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
    // pid 0 is the kernel scheduler — never a real sidecar pid, and
    // passing it here is catastrophic on Unix: `enumerate_children(0)`
    // falls back to `pgrep -P 0`, which matches PID 1 (init) + kernel
    // threads, and the DFS then descends into the ENTIRE process tree
    // (including the caller's own host / a CI runner agent) and signals
    // every process the caller owns. POSIX `kill(0, sig)` also signals
    // the caller's own process group. Treat pid 0 as a no-op.
    //
    // CI incident: this killed the GitHub Actions runner agent during
    // `cargo test` (the job died with "The runner has received a
    // shutdown signal" right after `test_kill_process_tree_pid_zero_is_noop`).
    if pid == 0 {
        log::debug!(
            "[KILL-TREE] kill_process_tree(0) is a no-op (pid 0 is the kernel scheduler, not a process)"
        );
        return;
    }
    // Capture each shell-out / syscall result and log on Err / non-zero
    // exit so a broken `taskkill` / `pgrep` / `kill` (PATH issue,
    // permissions, etc.) isn't silently swallowed. The function
    // remains best-effort — failures are logged but don't abort
    // shutdown.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        use std::process::Command;
        // CREATE_NO_WINDOW (0x0800_0000) — without this flag,
        // `Command::new("taskkill")` spawns a visible console window
        // for the duration of the taskkill call. On a normal desktop
        // shutdown the window flashes for ~50-100ms — visually
        // jarring and can steal focus from the foreground app the
        // user is typing into (a dictation app shouldn't pop a
        // console on shutdown). The flag is a no-op on POSIX.
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

        // ER-93: drop descendants that already exited between their
        // enumeration and now (a sidecar's children can be short-lived
        // helpers that reap themselves at any moment). Signalling them
        // would be an ESRCH no-op per pid; filtering here lets the
        // truly-empty case fall into the zero-cost fast path below
        // instead of paying SIGTERM → 200ms grace → SIGKILL for
        // corpses.
        let before_filter = all_descendants.len();
        all_descendants.retain(|&dpid| posix_impl::pid_is_alive(dpid));
        if all_descendants.len() != before_filter {
            log::debug!(
                "[KILL-TREE] dropped {} already-exited descendant(s) of pid {} before signalling",
                before_filter - all_descendants.len(),
                pid
            );
        }

        // Short-circuit when no descendants exist — avoids the
        // unconditional 200ms sleep below on the Tauri event-loop thread
        // (called from shutdown_sidecar_for_exit via block_on).
        //
        // ER-93: the process-group kill below is (in the current
        // production spawn path) ALWAYS a safe no-op — the sidecar
        // inherits the host's pgid, and `signal_process_group` refuses
        // to signal its own host's group. The grace sleep existed only
        // to give that (nonexistent) group-signal time to land, so we
        // now sleep ONLY when the group kill actually fired
        // (`kill_process_group_if_safe` returns true). When it didn't,
        // this path returns immediately: zero-cost shutdown for the
        // common "sidecar already exited / has no children" case.
        //
        // NOTE: we DO still attempt the process-group kill below even
        // when `all_descendants` is empty, because the race window
        // (sidecar spawns a child after our snapshot but before we
        // return) could leave a child un-killed. But the process-group
        // kill only fires when the sidecar is in its own group (see
        // the safety guard in the function doc comment), which is
        // currently never the case — so in practice this short-circuit
        // is safe. When the spawn path is updated to put the sidecar
        // in its own group, this early return should be reconsidered.
        if all_descendants.is_empty() {
            log::debug!(
                "[KILL-TREE] no descendants for pid {} — skipping SIGTERM/SIGKILL cycle",
                pid
            );
            // Attempt the process-group kill (best-effort, no-op when
            // the sidecar shares the host's pgid). Only pay the grace
            // sleep when the group signal was actually delivered —
            // otherwise there is nothing in flight to wait for.
            let term_fired = posix_impl::kill_process_group_if_safe(pid, libc::SIGTERM);
            if term_fired {
                std::thread::sleep(Duration::from_millis(KILL_TREE_SIGTERM_GRACE_MS));
                posix_impl::kill_process_group_if_safe(pid, libc::SIGKILL);
            } else {
                log::debug!(
                    "[KILL-TREE] process-group SIGTERM did not fire — skipping grace sleep + group SIGKILL entirely"
                );
            }
            return;
        }

        // Resolve the sidecar's process-group ID ONCE, up-front. We
        // use it for both the SIGTERM and SIGKILL process-group sends.
        // `getpgid` returns the pgid of the process at the time of the
        // call — if the sidecar has already exited (and its pid was
        // recycled), this could return a stale or wrong pgid. We
        // mitigate by checking against the host's own pgid (see the
        // safety guard in `signal_process_group`).
        //
        // Range guard: `pid` is `u32` but `libc::pid_t` is `i32` on
        // all supported Unix platforms. A `pid > i32::MAX` would
        // silently wrap to a negative `pid_t` (which POSIX interprets
        // as a process-GROUP signal — `kill(-pgid, sig)` — and would
        // signal an UNRELATED process group). The kernel never
        // assigns pids > `i32::MAX` on any real system, but defensive
        // guarding prevents a future caller passing a malformed
        // `u32::MAX` from causing a process-group signal.
        //
        // When `pid > i32::MAX`, we skip the getpgid + signal_process_group
        // calls but STILL iterate the per-pid `signal_pid` kills below
        // (each descendant pid is independently range-guarded inside
        // `signal_pid`). The summary log + per-pid kills always run.
        let pid_in_range = pid <= i32::MAX as u32;
        let sidecar_pgid: libc::pid_t = if pid_in_range {
            unsafe { libc::getpgid(pid as libc::pid_t) }
        } else {
            log::warn!(
                "[KILL-TREE] getpgid skipped for out-of-range pid {} (> i32::MAX) — \
                 would truncate on cast to pid_t",
                pid
            );
            // -1 = sentinel meaning "getpgid failed"; the safety guard
            // inside `signal_process_group` rejects values <= 0, so the
            // subsequent group-signal calls become no-ops.
            -1
        };

        // SIGTERM phase: per-pid kills (targeted) + process-group kill
        // (catches race-window children).
        for &dpid in &all_descendants {
            posix_impl::signal_pid(dpid, libc::SIGTERM);
        }
        // Process-group SIGTERM — catches any child spawned in the
        // race window between the snapshot and the per-pid kills
        // above. Best-effort: no-op when the sidecar shares the host's
        // pgid (the safety guard inside returns without signaling).
        if pid_in_range {
            posix_impl::signal_process_group(sidecar_pgid, libc::SIGTERM);
        }

        std::thread::sleep(Duration::from_millis(KILL_TREE_SIGTERM_GRACE_MS));

        // SIGKILL phase: per-pid kills (force) + process-group kill
        // (force, catches race-window children that ignored SIGTERM).
        for &dpid in &all_descendants {
            posix_impl::signal_pid(dpid, libc::SIGKILL);
        }
        // Process-group SIGKILL — force-kill any race-window child
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
    // Fallback for platforms that are neither Windows nor Unix (e.g.
    // a future wasm / redox target). Without this `#[cfg(not(...))]`
    // branch, `cargo check` on those targets would fail with an
    // "unreachable code" warning, AND a future port to such a target
    // would silently inherit a no-op `kill_process_tree` that
    // doesn't even log — making shutdown debugging on the new target
    // harder than necessary. The branch logs at `warn!` (not `info!`)
    // because a sidecar that's NOT reaped on shutdown is a resource
    // leak (mic / IPC port held open) — operators need to see this.
    #[cfg(not(any(target_os = "windows", unix)))]
    {
        let _ = pid;
        log::warn!(
            "[KILL-TREE] kill_process_tree is a no-op on this platform \
             (neither Windows nor Unix); pid {} will NOT be reaped — \
             the sidecar may leak the mic / IPC port until manually killed",
            pid
        );
    }
}

// Sibling test module — tests live in `tests.rs` in this directory
// (per C-TEST-5: no inline `#[cfg(test)] mod tests` blocks in
// production source).
#[cfg(test)]
#[path = "tests.rs"]
mod tests;
