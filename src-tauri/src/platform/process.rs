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
// `OnceLock<JobHandle>`) and shared across all sidecar spawns. Every
// successful `register_kill_on_parent_exit` call assigns the new pid
// to the same Job Object. When the host process exits, Windows closes
// all handles (including the Job Object handle stored in the static),
// which triggers the `KILL_ON_JOB_CLOSE` limit and terminates every
// process assigned to the Job Object.
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
    use std::sync::OnceLock;

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
    /// subsequent sidecar spawns. The `OnceLock` ensures the Job Object
    /// is created exactly once even if multiple threads race to call
    /// `register_kill_on_parent_exit` concurrently (which shouldn't
    /// happen in practice — there's only one sidecar — but the lock
    /// makes the invariant explicit).
    /// Stores `Result` so the fallible `create_job_object` can be used
    /// with the stable `get_or_init` (the unstable `get_or_try_init`
    /// is not available in Rust 1.77).
    static JOB_OBJECT: OnceLock<Result<JobHandle, String>> = OnceLock::new();

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
    /// `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Called once via
    /// `OnceLock::get_or_init`-style logic.
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
    /// (by the `OnceLock`'s static-drop), Windows kills every process
    /// assigned to the Job Object.
    pub(crate) fn register_kill_on_parent_exit_windows(pid: u32) -> Result<(), String> {
        // Get-or-create the process-wide Job Object. Uses the stable
        // `get_or_init` API with a `Result`-wrapped OnceLock since
        // `get_or_try_init` is unstable until Rust 1.81+.
        let job_handle: &HANDLE = match JOB_OBJECT.get_or_init(|| create_job_object()) {
            Ok(jh) => &jh.0,
            Err(e) => return Err(e.clone()),
        };

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
/// On Unix this does a **recursive** walk via `pgrep -P <pid>`
/// (depth-first) so ALL descendants are reaped — grandchildren (native
/// hotkey binary, model subprocesses) included. The prior
/// `pkill -TERM -P <pid>` only matched DIRECT children, leaving
/// grandchildren holding the mic / input device after the sidecar
/// exited. The root pid itself is NOT killed here — the caller
/// (`SidecarHandle::kill_tree` / `spawn.rs` cleanup) kills the root
/// separately via `child.kill()` afterwards, so we focus on the
/// descendants only.
///
/// Exposed as `pub(crate)` so `spawn.rs`'s spawn-timeout cleanup paths
/// can call it directly (they only have the `CommandChild` /
/// `tokio::process::Child`, not a `SidecarHandle`, so they can't use
/// `kill_tree`).
pub(crate) fn kill_process_tree(pid: u32) {
    // Capture each shell-out result and log on Err / non-zero exit so
    // a broken `taskkill`/`pgrep`/`kill` (PATH issue, permissions,
    // etc.) isn't silently swallowed. The function remains best-effort
    // — failures are logged but don't abort shutdown.
    #[cfg(windows)]
    {
        use std::process::Command;
        let tool = "taskkill";
        match Command::new(tool)
            .args(["/F", "/T", "/PID", &pid.to_string()])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
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
        use std::process::Command;
        use std::time::Duration;
        // SIGTERM→SIGKILL grace period is the named constant
        // `KILL_TREE_SIGTERM_GRACE_MS` in `util.rs` (was inline 200ms).
        use crate::util::KILL_TREE_SIGTERM_GRACE_MS;

        let mut all_descendants: Vec<u32> = Vec::new();
        let mut stack: Vec<u32> = vec![pid];
        while let Some(cur) = stack.pop() {
            let pgrep = Command::new("pgrep")
                .args(["-P", &cur.to_string()])
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::null())
                .output();
            match pgrep {
                Ok(out) if out.status.success() => {
                    let stdout = String::from_utf8_lossy(&out.stdout);
                    for line in stdout.lines() {
                        if let Ok(child_pid) = line.trim().parse::<u32>() {
                            all_descendants.push(child_pid);
                            stack.push(child_pid);
                        }
                    }
                }
                Ok(out) => {
                    // Exit 1 = no children (normal leaf) — skip logging.
                    if out.status.code() != Some(1) {
                        log::warn!(
                            "[KILL-TREE] pgrep exited with code {:?} for pid {}",
                            out.status.code(),
                            cur
                        );
                    }
                }
                Err(e) => {
                    log::warn!("[KILL-TREE] pgrep failed for pid={}: {}", cur, e);
                }
            }
        }

        // Short-circuit when no descendants exist — avoids the
        // unconditional 200ms sleep below on the Tauri event-loop thread
        // (called from shutdown_sidecar_for_exit via block_on).
        if all_descendants.is_empty() {
            log::debug!("[KILL-TREE] no descendants for pid {} — skipping SIGTERM/SIGKILL cycle", pid);
            return;
        }

        for &dpid in &all_descendants {
            match Command::new("kill")
                .args(["-TERM", &dpid.to_string()])
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status()
            {
                Ok(s) if s.success() => {}
                Ok(s) => {
                    log::warn!(
                        "[KILL-TREE] kill -TERM exited with code {} for pid {}",
                        s.code().map(|c| c.to_string()).unwrap_or_else(|| "<signal>".into()),
                        dpid
                    );
                }
                Err(e) => {
                    log::warn!("[KILL-TREE] kill -TERM failed for pid={}: {}", dpid, e);
                }
            }
        }

        std::thread::sleep(Duration::from_millis(KILL_TREE_SIGTERM_GRACE_MS));

        for &dpid in &all_descendants {
            match Command::new("kill")
                .args(["-KILL", &dpid.to_string()])
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status()
            {
                Ok(s) if s.success() => {}
                Ok(s) => {
                    log::warn!(
                        "[KILL-TREE] kill -KILL exited with code {} for pid {}",
                        s.code().map(|c| c.to_string()).unwrap_or_else(|| "<signal>".into()),
                        dpid
                    );
                }
                Err(e) => {
                    log::warn!("[KILL-TREE] kill -KILL failed for pid={}: {}", dpid, e);
                }
            }
        }

        // Final summary line.
        log::info!(
            "[KILL-TREE] reaped {} descendants of pid {}",
            all_descendants.len(),
            pid
        );
    }
}

#[cfg(test)]
mod tests {
    // Unit tests for this module are limited because the actual
    // behavior (kill-on-parent-exit) requires process-level testing
    // that can't be done in-process. The integration test lives in
    // `tests/tauri/zr2_kill_on_parent_exit.rs` (to be added by the
    // validation phase).
    //
    // The one thing we CAN unit-test is that the function is callable
    // and returns Ok(()) or Err(String) without panicking on the
    // current platform.

    /// `kill_process_tree` must be best-effort — calling it with a
    /// non-existent pid must not panic.
    #[test]
    fn test_kill_process_tree_nonexistent_pid_is_noop() {
        super::kill_process_tree(999_999);
    }

    /// `kill_process_tree` must not panic on a pathologically large pid
    /// (e.g. u32::MAX).
    #[test]
    fn test_kill_process_tree_u32_max_is_noop() {
        super::kill_process_tree(u32::MAX);
    }

    #[test]
    fn test_register_kill_on_parent_exit_returns_result_not_panic() {
        // We don't actually register a real pid here (that would
        // spawn a reaper subprocess or assign a non-existent pid to
        // the Job Object). We just verify the function exists and
        // is callable. The real behavior is validated by the
        // VALIDATE ON LINUX HOST / WINDOWS HOST commands in the
        // module-level doc comment.
        //
        // Use a pid that's guaranteed to not exist (pid 0 is the
        // scheduler — never a real process; pid 1 is init — never
        // assigned to a Job Object). On Windows, `OpenProcess` will
        // fail with "invalid parameter" for pid 0. On POSIX, the
        // reaper will spawn successfully (it doesn't validate the
        // pid at spawn time — it just embeds it in the script).
        //
        // We call the function and accept any Result — the test
        // passes as long as it doesn't panic.
        #[cfg(unix)]
        {
            // POSIX: the reaper spawns successfully even with a
            // bogus pid (it just exits immediately on the first
            // `kill -0 $target` check). We expect Ok(()).
            let result = super::register_kill_on_parent_exit(0);
            assert!(
                result.is_ok(),
                "POSIX reaper spawn should succeed even with bogus pid 0, got: {:?}",
                result
            );
        }
        #[cfg(target_os = "windows")]
        {
            // Windows: OpenProcess(pid=0) fails. We expect Err(_).
            // We don't assert the exact error string (it varies by
            // Windows version) but we do assert it's an Err.
            let result = super::register_kill_on_parent_exit(0);
            assert!(
                result.is_err(),
                "Windows Job Object assignment for pid 0 should fail (OpenProcess rejects pid 0), got: {:?}",
                result
            );
        }
        #[cfg(not(any(unix, target_os = "windows")))]
        {
            let result = super::register_kill_on_parent_exit(0);
            assert!(result.is_ok());
        }
    }
}
