// Windows implementation of the kill-on-parent-exit guarantee: a
// process-wide Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
// (split: body of the former inline `mod windows_impl` in
// `platform/process.rs`; declared from `process/mod.rs` via
// `#[path = "windows.rs"] mod windows_impl;`).

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
use windows::core::PCWSTR;
use windows::Win32::Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_BREAKAWAY_OK,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows::Win32::System::Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE};

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
    #[allow(clippy::cast_possible_truncation)] // struct size is a small compile-time constant
    let info_size = std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32;
    unsafe {
        SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const _,
            info_size,
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
    let mut guard = JOB_OBJECT
        .try_lock()
        .map_err(|e| format!("JOB_OBJECT lock contended/poisoned: {}", e))?;
    if guard.is_none() {
        let jh = create_job_object()?;
        *guard = Some(jh);
    }
    // SAFETY: `guard.is_none()` was just checked and `*guard`
    // was set to `Some(jh)` on the None path. The `expect` is
    // unreachable but documents the invariant.
    #[allow(clippy::expect_used)] // unreachable but pins the invariant (SAFETY note above)
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
