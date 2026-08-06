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
        return register_kill_on_parent_exit_windows(pid);
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
mod windows_impl {
    use std::sync::Mutex;

    // windows-sys is NOT in our Cargo.toml — the existing `windows`
    // crate (with `Win32_UI_WindowsAndMessaging`, `Win32_Foundation`,
    // `Win32_System_Threading` features) is. We need additional
    // features for Job Object APIs:
    //   - `Win32_System_JobObjects` for `CreateJobObjectW`,
    //     `SetInformationJobObject`, `AssignProcessToJobObject`.
    //   - `Win32_System_Threading` for `OpenProcess` (already in deps).
    //   - `Win32_Foundation` for `HANDLE`, `CloseHandle` (already in deps).
    //
    // We use the `windows` crate's re-exports (the feature-gated
    // submodules under `windows::Win32::*`). If the build fails with
    // "feature `Win32_System_JobObjects` is not enabled", the Cargo.toml
    // `[target.'cfg(windows)'.dependencies]` windows entry needs the
    // feature added (one-line change — see the fix instructions).
    use windows::Win32::Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE};
    use windows::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_BREAKAWAY_OK, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows::Win32::System::Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE};
    use windows::core::PCWSTR;

    /// Wrapper around a Win32 `HANDLE` that closes it on Drop. RAII so
    /// the Job Object handle is closed even if a panic happens between
    /// `CreateJobObjectW` and the `OnceLock::set` call.
    struct JobHandle(HANDLE);

    impl Drop for JobHandle {
        fn drop(&mut self) {
            // Best-effort close. `CloseHandle` returns Err on failure
            // (e.g. invalid handle) — we log but don't panic.
            if !self.0.is_invalid() {
                unsafe {
                    let _ = CloseHandle(self.0);
                }
            }
        }
    }

    // SAFETY: `HANDLE` is a raw pointer wrapper, but Windows handles
    // are NOT pointers — they're opaque process-relative tokens. The
    // `windows` crate's `HANDLE` is `Send`-safe in practice (the Win32
    // API is thread-safe for handle operations). The `windows` crate
    // intentionally does NOT impl `Send` for `HANDLE` to force callers
    // to acknowledge the safety implication, so we assert it here.
    // The Job Object handle is only ever accessed from the
    // `register_kill_on_parent_exit_windows` call site, which can run
    // on any thread (the sidecar spawn happens on a Tokio worker).
    unsafe impl Send for JobHandle {}
    unsafe impl Sync for JobHandle {}

    /// Process-wide Job Object. Created on first use; reused for all
    /// subsequent sidecar spawns. The `Mutex` ensures the Job Object
    /// is created exactly once even if multiple threads race to call
    /// `register_kill_on_parent_exit` concurrently (which shouldn't
    /// happen in practice — there's only one sidecar — but the lock
    /// makes the invariant explicit).
    ///
    /// Stored as `Mutex<Option<JobHandle>>` (NOT
    /// `OnceLock<Result<JobHandle, String>>`) so a transient
    /// `create_job_object` failure (e.g. Windows HANDLE-table
    /// exhaustion at boot, low-memory condition) is NOT cached — the
    /// next `register_kill_on_parent_exit` call retries
    /// `create_job_object` from scratch. The prior `OnceLock<Result>`
    /// design permanently cached the first failure, disabling the
    /// kill-on-parent-exit guarantee for the lifetime of the host
    /// even after the transient condition cleared.
    ///
    /// `try_lock` is used at the call site (not `lock`) so a
    /// contended lock — e.g. another thread mid-`AssignProcessToJobObject`
    /// — returns an error rather than blocking the sidecar-spawn
    /// path. Contention is virtually impossible (single sidecar per
    /// host) but the non-blocking contract keeps the spawn path
    /// latency-bounded.
    static JOB_OBJECT: Mutex<Option<JobHandle>> = Mutex::new(None);

    /// Open a handle to the target pid with the rights needed to
    /// assign it to a Job Object (`PROCESS_SET_QUOTA` +
    /// `PROCESS_TERMINATE`). Returns the raw `HANDLE` on success.
    fn open_process_for_job_assignment(pid: u32) -> Result<HANDLE, String> {
        // SAFETY: `OpenProcess` is safe to call with any pid; the
        // returned handle is owned by the caller and must be closed.
        // We close it explicitly after `AssignProcessToJobObject`
        // (the assignment does NOT take ownership — the pid is now
        // tracked by the Job Object, but the process handle is
        // still ours to close).
        let handle = unsafe {
            OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, false, pid)
                .map_err(|e| format!("OpenProcess({}) failed: {}", pid, e))?
        };
        Ok(handle)
    }

    /// Create the process-wide Job Object with
    /// `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Called on the first
    /// successful `register_kill_on_parent_exit_windows` invocation
    /// (and re-called if a prior call's `create_job_object` failed
    /// transiently — see the `JOB_OBJECT` static doc for why the
    /// retry-on-failure contract matters).
    fn create_job_object() -> Result<JobHandle, String> {
        // SAFETY: `CreateJobObjectW(NULL, NULL)` is safe — it returns
        // either a valid handle or `INVALID_HANDLE_VALUE` (which we
        // check). The `lpJobAttributes` parameter is NULL (default
        // security descriptor, not inheritable). The `lpName` parameter
        // is NULL (anonymous Job Object — avoids name collisions with
        // other Voice Typer instances).
        let handle = unsafe {
            CreateJobObjectW(None, PCWSTR::null())
                .map_err(|e| format!("CreateJobObjectW failed: {}", e))?
        };
        if handle == INVALID_HANDLE_VALUE {
            return Err("CreateJobObjectW returned INVALID_HANDLE_VALUE".to_string());
        }

        // Configure the kill-on-job-close limit.
        let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        info.BasicLimitInformation.LimitFlags =
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK;
        // SAFETY: `SetInformationJobObject` is safe given a valid
        // Job Object handle and a properly-initialized info struct.
        // The `JobObjectExtendedLimitInformation` class expects a
        // `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` struct.
        unsafe {
            SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const _,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
            .map_err(|e| format!("SetInformationJobObject failed: {}", e))?;
        }

        Ok(JobHandle(handle))
    }

    /// Windows: assign `pid` to the process-wide Job Object.
    /// The Job Object has `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` set, so
    /// when the host process exits and the Job Object handle is closed
    /// (by the static's drop), Windows kills every process assigned to
    /// the Job Object.
    pub(crate) fn register_kill_on_parent_exit_windows(pid: u32) -> Result<(), String> {
        // Get-or-create the process-wide Job Object. We use
        // `Mutex::try_lock` (not `lock`) so a contended lock returns
        // an error rather than blocking the sidecar-spawn path; the
        // contention window is virtually zero (single sidecar per
        // host) but the non-blocking contract keeps the spawn path
        // latency-bounded.
        //
        // On the `None` path (first call, OR a prior call's
        // `create_job_object` failure left the slot empty), we
        // (re)create the Job Object. A failure here is returned as
        // `Err` WITHOUT populating the slot — so the next call
        // retries `create_job_object` from scratch. This fixes the
        // prior `OnceLock<Result<JobHandle, String>>` bug where a
        // transient `create_job_object` failure was cached forever,
        // permanently disabling the kill-on-parent-exit guarantee.
        let mut guard = JOB_OBJECT.try_lock().map_err(|e| {
            format!("JOB_OBJECT lock contended/poisoned: {}", e)
        })?;
        if guard.is_none() {
            let jh = create_job_object()?;
            *guard = Some(jh);
        }
        // SAFETY: `guard.is_none()` was just checked and `*guard`
        // was set to `Some(jh)` on the None path. The `expect` is
        // unreachable but documents the invariant.
        let job_handle: &HANDLE = &guard
            .as_ref()
            .expect("JOB_OBJECT must be Some after the create-or-reuse path above")
            .0;

        // Open a handle to the target process with the rights needed
        // for `AssignProcessToJobObject`.
        let proc_handle = open_process_for_job_assignment(pid)?;

        // Assign the process to the Job Object. After this, the
        // process will be killed when the Job Object's last handle is
        // closed (i.e. when the host exits).
        //
        // SAFETY: `AssignProcessToJobObject(job, proc)` is safe given
        // valid handles. The process MUST still be running (it is —
        // we just spawned it). The process MUST NOT already be
        // assigned to a different Job Object (it isn't — Tauri's
        // shell-plugin spawn doesn't assign it to one).
        unsafe {
            AssignProcessToJobObject(*job_handle, proc_handle)
                .map_err(|e| format!("AssignProcessToJobObject({}) failed: {}", pid, e))?;
        }

        // Close the process handle — the Job Object now tracks the
        // pid, so we don't need to keep the handle open.
        //
        // SAFETY: `CloseHandle` is safe given a valid handle.
        unsafe {
            let _ = CloseHandle(proc_handle);
        }

        log::info!(
            "[process] assigned sidecar pid {} to process-wide Job Object \
             (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)",
            pid
        );
        Ok(())
    }
}

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
mod posix_impl {
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
}

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
                    s.code().map(|c| c.to_string()).unwrap_or_else(|| "<signal>".into()),
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
            for child_pid in enumerate_children(cur) {
                all_descendants.push(child_pid);
                stack.push(child_pid);
            }
        }

        // Short-circuit when no descendants exist — avoids the
        // unconditional 200ms sleep below on the Tauri event-loop thread
        // (called from shutdown_sidecar_for_exit via block_on).
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
            log::debug!("[KILL-TREE] no descendants for pid {} — skipping SIGTERM/SIGKILL cycle", pid);
            // Still attempt the process-group kill (best-effort, no-op
            // when the sidecar shares the host's pgid).
            kill_process_group_if_safe(pid, libc::SIGTERM);
            std::thread::sleep(Duration::from_millis(KILL_TREE_SIGTERM_GRACE_MS));
            kill_process_group_if_safe(pid, libc::SIGKILL);
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
            signal_pid(dpid, libc::SIGTERM);
        }
        // Process-group SIGTERM — catches any child spawned in the
        // race window between the snapshot and the per-pid kills
        // above. Best-effort: no-op when the sidecar shares the host's
        // pgid (the safety guard inside returns without signaling).
        if pid_in_range {
            signal_process_group(sidecar_pgid, libc::SIGTERM);
        }

        std::thread::sleep(Duration::from_millis(KILL_TREE_SIGTERM_GRACE_MS));

        // SIGKILL phase: per-pid kills (force) + process-group kill
        // (force, catches race-window children that ignored SIGTERM).
        for &dpid in &all_descendants {
            signal_pid(dpid, libc::SIGKILL);
        }
        // Process-group SIGKILL — force-kill any race-window child
        // that survived the SIGTERM phase. Same safety guard applies.
        if pid_in_range {
            signal_process_group(sidecar_pgid, libc::SIGKILL);
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
fn signal_pid(pid: u32, sig: libc::c_int) -> bool {
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
fn signal_name(sig: libc::c_int) -> &'static str {
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
fn enumerate_children(pid: u32) -> Vec<u32> {
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
fn enumerate_children_procfs(pid: u32) -> Result<Vec<u32>, std::io::Error> {
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
fn enumerate_children_pgrep(pid: u32) -> Vec<u32> {
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
fn signal_process_group(sidecar_pgid: libc::pid_t, sig: libc::c_int) {
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
fn kill_process_group_if_safe(pid: u32, sig: libc::c_int) {
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

// Sibling test module — tests live in `process_tests.rs` (per C-TEST-5:
// no inline `#[cfg(test)] mod tests` blocks in production source).
#[cfg(test)]
#[path = "process_tests.rs"]
mod process_tests;

