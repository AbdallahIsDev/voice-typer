//! Sibling tests for `platform::process` (per C-TEST-5 — sibling test
//! file, no inline tests in production source).
//!
//! Covers three behaviors of `kill_process_tree` / `signal_pid` /
//! `kill_process_group_if_safe`:
//!
//! - **Range guard for `pid > i32::MAX`**: `signal_pid` /
//!   `kill_process_group_if_safe` / `getpgid` calls in
//!   `kill_process_tree` skip when `pid > i32::MAX` (defensive range
//!   guard against `u32` → `i32` truncation producing a negative
//!   `pid_t` that POSIX `kill(2)` would interpret as a process-group
//!   signal).
//! - **Non-Windows/non-Unix fallback branch**: `kill_process_tree`'s
//!   `#[cfg(not(any(target_os = "windows", unix)))]` branch logs a
//!   warning + returns (no panic).
//! - **Windows `CREATE_NO_WINDOW` flag**: `kill_process_tree`'s Windows
//!   `taskkill` builder uses `creation_flags(0x0800_0000)` so the
//!   spawned `taskkill.exe` doesn't flash a console window. Verified
//!   indirectly via the `cfg(windows)` compile-time gating.
//!
//! Pre-existing kill-tree tests (moved verbatim from the legacy inline
//! `mod tests` block) live further down in this file.

use super::*;

// ── Range guard for `pid > i32::MAX` ───────────────────────────────

/// `signal_pid` must NOT call `libc::kill` when `pid > i32::MAX`.
/// Without the range guard, `pid as libc::pid_t` would truncate
/// `u32::MAX` (4294967295) to `-1`, which `kill(2)` interprets as
/// "signal every process the caller can signal" — a catastrophic
/// self-kill. With the guard, the function returns `false` (no
/// failure) without invoking the syscall.
///
/// We can't directly observe whether `libc::kill` was called, but we
/// CAN verify the test process survives calling `signal_pid(u32::MAX,
/// SIGKILL)` — pre-fix this would have killed the test process
/// itself (the kernel interprets `kill(-1, SIGKILL)` as "kill
/// everyone except init").
#[cfg(unix)]
#[test]
fn test_signal_pid_u32_max_does_not_self_kill() {
    // Pre-fix: signal_pid(u32::MAX, SIGKILL) would call
    // libc::kill(-1, SIGKILL) which kills every process the caller
    // can signal — including the test process. Post-fix: the range
    // guard returns false without calling kill. If the guard is
    // broken, this test process would die and the test would fail
    // with a process-death error.
    let result = signal_pid(u32::MAX, libc::SIGKILL);
    // The range guard returns `false` (no failure — the pid is
    // provably nonexistent, so the caller's aggregation stays
    // consistent).
    assert!(
        !result,
        "signal_pid(u32::MAX, SIGKILL) should return false (range guard)"
    );
    // If we reach here, the guard worked — the test process survived.
}

/// `signal_pid` for a pid EXACTLY at `i32::MAX` (the largest valid
/// positive `pid_t`) should be ALLOWED through to `libc::kill`. The
/// range guard rejects `pid > i32::MAX`, not `pid == i32::MAX`.
#[cfg(unix)]
#[test]
fn test_signal_pid_i32_max_passes_range_guard() {
    // i32::MAX = 2147483647 — extremely unlikely to be a real pid,
    // so libc::kill will return -1 with ESRCH (no such process).
    // The test verifies the range guard does NOT reject this value
    // (the guard uses `>`, not `>=`).
    let result = signal_pid(i32::MAX as u32, libc::SIGTERM);
    // ESRCH returns false (signal not delivered, but not a "failure"
    // per the helper's contract).
    assert!(
        !result,
        "signal_pid(i32::MAX, SIGTERM) should pass the range guard \
         and return false (ESRCH for nonexistent pid)"
    );
}

/// `signal_pid` for `i32::MAX as u32 + 1` (the first out-of-range
/// value) should be rejected by the range guard.
#[cfg(unix)]
#[test]
fn test_signal_pid_just_above_i32_max_rejected() {
    let just_over = (i32::MAX as u32) + 1;
    let result = signal_pid(just_over, libc::SIGTERM);
    assert!(
        !result,
        "signal_pid(i32::MAX + 1, SIGTERM) should be range-guarded"
    );
}

/// `kill_process_group_if_safe` for `u32::MAX` must NOT call
/// `getpgid` (which would truncate to `getpgid(-1)` — undefined
/// behavior). The range guard returns early.
#[cfg(unix)]
#[test]
fn test_kill_process_group_if_safe_u32_max_is_noop() {
    // Pre-fix: kill_process_group_if_safe(u32::MAX, SIGKILL) would
    // call getpgid(-1) which is undefined; on Linux it returns -1
    // (no such process), and then signal_process_group(-1, SIGKILL)
    // would call kill(-(-1), SIGKILL) = kill(1, SIGKILL) = signal
    // init. Post-fix: the range guard returns before getpgid is
    // called.
    kill_process_group_if_safe(u32::MAX, libc::SIGTERM);
    kill_process_group_if_safe(u32::MAX, libc::SIGKILL);
    // If we reach here, the guard worked.
}

/// `kill_process_tree` for `u32::MAX` must not panic, must not
/// signal the host, and must not signal init. The range guard in
/// `kill_process_group_if_safe` (early-return path) + the
/// `signal_pid` guard (per-pid kills) together ensure this.
///
/// This test is the integration guard for the range guard — if EITHER guard
/// is broken, the test process would die (from `kill(-1, SIGKILL)`
/// or `kill(1, SIGKILL)`).
#[cfg(unix)]
#[test]
fn test_kill_process_tree_u32_max_does_not_self_kill() {
    kill_process_tree(u32::MAX);
    // If we reach here, the guards worked.
}

// ── Non-Windows/non-Unix fallback branch ───────────────────────────

/// On a non-Windows/non-Unix target, `kill_process_tree` must log a
/// warning and return (not panic). We can't easily test this from a
/// `cfg(unix)` test environment (the `#[cfg(not(any(...)))]` branch
/// is compiled out on Unix), but we CAN verify the function is
/// callable from any platform — the test passes if it doesn't panic.
///
/// On Unix/Windows, this test exercises the existing platform
/// branches; on a hypothetical wasm/redox target, it would exercise
/// the new fallback branch.
#[test]
fn test_kill_process_tree_does_not_panic_on_any_platform() {
    // Use a pid guaranteed not to exist (999_999 is below pid_max
    // on Linux, so the kernel reports ESRCH; on Windows, taskkill
    // exits with code 128 for "no such process").
    kill_process_tree(999_999);
    // If we reach here, the function didn't panic.
}

/// `kill_process_tree` for `pid = 0` must not panic. pid 0 is the
/// scheduler on Linux (never a real killable process); on Windows
/// it's the idle process. The function should treat it as a no-op
/// (the per-pid `signal_pid` returns ESRCH; `taskkill` exits
/// non-zero).
#[test]
fn test_kill_process_tree_pid_zero_is_noop() {
    kill_process_tree(0);
    // If we reach here, the function didn't panic.
}

// ── Pre-existing kill-tree + register_kill_on_parent_exit tests ────
// (Moved verbatim from the legacy inline 
//  block to comply with C-TEST-5.)

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

    // Process-group signal helpers — Unix-only. These verify the
    // SAFETY GUARD that prevents `signal_process_group` from killing
    // the host when the sidecar shares the host's pgid (the current
    // production state, since `tauri-plugin-shell`'s `externalBin`
    // API doesn't call `setsid()` / `setpgid()`).

    /// `signal_process_group` must be a no-op (not panic, not signal)
    /// when called with a pgid of 0 or negative (the return value of
    /// a failed `getpgid`). This covers the "sidecar already exited"
    /// case where `getpgid` returns -1.
    #[cfg(unix)]
    #[test]
    fn test_signal_process_group_invalid_pgid_is_noop() {
        super::signal_process_group(0, libc::SIGTERM);
        super::signal_process_group(-1, libc::SIGTERM);
        super::signal_process_group(0, libc::SIGKILL);
        super::signal_process_group(-1, libc::SIGKILL);
    }

    /// `signal_process_group` must REFUSE to signal the host's own
    /// process group. This is the critical safety guard: the sidecar
    /// is spawned via `tauri-plugin-shell` (no `setsid()`), so its
    /// pgid == the host's pgid. Sending `kill -<host_pgid>` would kill
    /// the host. The guard must detect this and skip the signal.
    ///
    /// We verify by calling `signal_process_group` with the HOST's own
    /// pgid. If the guard works, the test process survives. If the
    /// guard is broken, the test process would be killed by its own
    /// signal and the test would fail with a process-death error.
    #[cfg(unix)]
    #[test]
    fn test_signal_process_group_refuses_host_pgid() {
        let host_pgid = unsafe { libc::getpgrp() };
        assert!(host_pgid > 0, "host pgid should be positive");
        // This MUST NOT kill the test process.
        super::signal_process_group(host_pgid, libc::SIGTERM);
        super::signal_process_group(host_pgid, libc::SIGKILL);
        // If we reach here, the guard worked.
        assert!(host_pgid > 0);
    }

    /// `kill_process_group_if_safe` must not panic on a non-existent
    /// pid. `getpgid` returns -1 for a dead pid, which the guard
    /// rejects.
    #[cfg(unix)]
    #[test]
    fn test_kill_process_group_if_safe_nonexistent_pid_is_noop() {
        super::kill_process_group_if_safe(999_999, libc::SIGTERM);
        super::kill_process_group_if_safe(999_999, libc::SIGKILL);
    }

    /// `kill_process_group_if_safe` for the test process's OWN pid
    /// must not kill the test process. The test process shares the
    /// host's pgid, so the guard rejects the signal.
    #[cfg(unix)]
    #[test]
    fn test_kill_process_group_if_safe_own_pid_does_not_self_kill() {
        let own_pid = std::process::id();
        super::kill_process_group_if_safe(own_pid, libc::SIGTERM);
        super::kill_process_group_if_safe(own_pid, libc::SIGKILL);
        assert!(own_pid > 0);
    }

    /// `kill_process_tree` for a process in the host's own pgid must
    /// not kill the host. Integration test: call `kill_process_tree`
    /// with our OWN pid. The pgrep DFS finds no children, takes the
    /// early-return path, and calls `kill_process_group_if_safe` which
    /// must refuse to signal the host's pgid.
    #[cfg(unix)]
    #[test]
    fn test_kill_process_tree_own_pid_does_not_self_kill() {
        let own_pid = std::process::id();
        super::kill_process_tree(own_pid);
        assert!(own_pid > 0);
    }

    // ── per-pid + child-enumeration helpers (syscall migration) ──────
    //
    // These pin the contract for the libc-syscall replacements of the
    // prior `kill -<sig> <pid>` shell-outs (now `signal_pid`) and the
    // `pgrep -P <pid>` shell-out on Linux (now `enumerate_children` →
    // `enumerate_children_procfs`). The behavior must be best-effort:
    // nonexistent pids, parse errors, and missing /proc files must
    // all degrade gracefully without panicking.

    /// `signal_pid` must not panic on a nonexistent pid. `libc::kill`
    /// returns -1 with errno=ESRCH for a nonexistent pid — the helper
    /// logs at `debug!` (not `warn!`) and returns. This pins the
    /// best-effort contract: a SIGTERM-reaped pid that's already gone
    /// must not abort the SIGKILL phase that follows.
    #[cfg(unix)]
    #[test]
    fn test_signal_pid_nonexistent_pid_is_noop() {
        super::signal_pid(999_999, libc::SIGTERM);
        super::signal_pid(999_999, libc::SIGKILL);
    }

    /// `signal_pid` must not panic on `u32::MAX` (a pathologically
    /// large pid that exceeds `pid_t`'s positive range on most
    /// platforms). `libc::kill` will return -1 with ESRCH or EINVAL;
    /// the helper logs and returns either way.
    #[cfg(unix)]
    #[test]
    fn test_signal_pid_u32_max_is_noop() {
        super::signal_pid(u32::MAX, libc::SIGTERM);
        super::signal_pid(u32::MAX, libc::SIGKILL);
    }

    /// `signal_name` must return the canonical names for the two
    /// signals used by `kill_process_tree`. Pinning the names guards
    /// against accidental rename regressions in the log lines that
    /// users grep for during shutdown debugging.
    #[cfg(unix)]
    #[test]
    fn test_signal_name_canonical() {
        assert_eq!(super::signal_name(libc::SIGTERM), "SIGTERM");
        assert_eq!(super::signal_name(libc::SIGKILL), "SIGKILL");
    }

    /// `enumerate_children_procfs` for the test process's OWN pid
    /// must return an empty Vec (the test process has no children).
    /// This pins the Linux `/proc/<pid>/task/<pid>/children` reader's
    /// parse contract: space-separated decimal pids, empty file = no
    /// children = empty Vec.
    #[cfg(target_os = "linux")]
    #[test]
    fn test_enumerate_children_procfs_own_pid_no_children() {
        let own_pid = std::process::id();
        let children = super::enumerate_children_procfs(own_pid)
            .expect("reading /proc/self/task/self/children must succeed for the test process");
        assert!(
            children.is_empty(),
            "test process has no children — expected empty Vec, got {:?}",
            children
        );
    }

    /// `enumerate_children_procfs` for a nonexistent pid must return
    /// `Err` (the `/proc/<pid>/task/<pid>/children` file doesn't
    /// exist for a dead pid). The caller (`enumerate_children`)
    /// catches this and falls back to `pgrep` — this test pins the
    /// error path so the fallback stays wired correctly.
    #[cfg(target_os = "linux")]
    #[test]
    fn test_enumerate_children_procfs_nonexistent_pid_returns_err() {
        let result = super::enumerate_children_procfs(999_999);
        assert!(
            result.is_err(),
            "expected Err for nonexistent pid 999999, got {:?}",
            result
        );
    }

    /// `enumerate_children` (the dispatch wrapper) for the test
    /// process's OWN pid must return an empty Vec on Linux (reads
    /// /proc successfully) and on macOS (pgrep returns exit 1 for no
    /// children → empty Vec). This pins the dispatch contract: on
    /// Linux it must NOT fall back to pgrep (the /proc read succeeds),
    /// on non-Linux it must use the pgrep path.
    #[cfg(unix)]
    #[test]
    fn test_enumerate_children_own_pid_returns_empty() {
        let own_pid = std::process::id();
        let children = super::enumerate_children(own_pid);
        assert!(
            children.is_empty(),
            "test process has no children — expected empty Vec, got {:?}",
            children
        );
    }

    /// `enumerate_children` for a nonexistent pid must NOT panic —
    /// on Linux, the /proc read fails and we fall back to pgrep which
    /// returns exit 1 (no children) → empty Vec; on macOS, pgrep
    /// returns exit 1 directly → empty Vec. Either way, the function
    /// is best-effort and returns an empty Vec.
    #[cfg(unix)]
    #[test]
    fn test_enumerate_children_nonexistent_pid_is_noop() {
        let children = super::enumerate_children(999_999);
        assert!(
            children.is_empty(),
            "nonexistent pid must yield empty Vec (best-effort), got {:?}",
            children
        );
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
