
use std::sync::Mutex;

use windows::core::PCWSTR;
use windows::Win32::Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_BREAKAWAY_OK,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows::Win32::System::Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE};

struct JobHandle(HANDLE);

impl Drop for JobHandle {
    fn drop(&mut self) {
        // Best-effort close. `CloseHandle` returns Err on failure
        // (e.g. invalid handle): we log but don't panic.
        if !self.0.is_invalid() {
            unsafe {
                let _ = CloseHandle(self.0);
            }
        }
    }
}

unsafe impl Send for JobHandle {}
unsafe impl Sync for JobHandle {}

static JOB_OBJECT: Mutex<Option<JobHandle>> = Mutex::new(None);

fn open_process_for_job_assignment(pid: u32) -> Result<HANDLE, String> {
    let handle = unsafe {
        OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, false, pid)
            .map_err(|e| format!("OpenProcess({}) failed: {}", pid, e))?
    };
    Ok(handle)
}

fn create_job_object() -> Result<JobHandle, String> {
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

pub(crate) fn register_kill_on_parent_exit_windows(pid: u32) -> Result<(), String> {
    let mut guard = JOB_OBJECT
        .try_lock()
        .map_err(|e| format!("JOB_OBJECT lock contended/poisoned: {}", e))?;
    if guard.is_none() {
        let jh = create_job_object()?;
        *guard = Some(jh);
    }
    #[allow(clippy::expect_used)] // unreachable but pins the invariant (SAFETY note above)
    let job_handle: &HANDLE = &guard
        .as_ref()
        .expect("JOB_OBJECT must be Some after the create-or-reuse path above")
        .0;

    // Open a handle to the target process with the rights needed
    // for `AssignProcessToJobObject`.
    let proc_handle = open_process_for_job_assignment(pid)?;

    unsafe {
        AssignProcessToJobObject(*job_handle, proc_handle)
            .map_err(|e| format!("AssignProcessToJobObject({}) failed: {}", pid, e))?;
    }

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
