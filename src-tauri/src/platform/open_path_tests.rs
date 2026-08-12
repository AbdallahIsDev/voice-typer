#![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unreachable, clippy::todo, clippy::unimplemented, clippy::cast_possible_truncation)]

//! Unit tests for `open_path` (moved verbatim from the inline
//! `#[cfg(test)] mod tests` block to satisfy C-TEST-5 — tests must
//! live in a sibling file, not inline in the production source).
//!
//! No test logic changed — the `use super::*;` path still resolves
//! to the parent module (`open_path`) because the parent file
//! declares this module via `#[cfg(test)] mod open_path_tests;`.

use super::*;
// The reaper-thread test below spawns a REAL child process (child of
// the test binary) — serialize against the own-pid enumeration tests
// (see test_support.rs CHILD_PROCESS_TEST_LOCK).
use crate::test_support::CHILD_PROCESS_TEST_LOCK;

// pre-flight existence check rejects a missing path with a
// structured error string. The error is what the caller puts in the
// ``{"success": false, "error": ...}`` envelope — without this
// check, the OS binary would spawn and pop a "path not found"
// dialog to the user while ``open_logs`` believed the open
// succeeded.
#[test]
fn test_open_path_rejects_missing_path() {
    let missing = std::env::temp_dir().join(format!(
        "voice-typer-ac34-missing-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    ));
    // Sanity: the path really doesn't exist.
    assert!(!missing.exists());
    let err = open_path_in_file_manager(&missing).unwrap_err();
    assert!(
        err.contains("path does not exist"),
        "expected 'path does not exist' in error, got: {err}"
    );
}

// an existing path is accepted by the pre-flight existence check
// WITHOUT spawning the OS file manager. (The previous version of
// this test called `open_path_in_file_manager(&temp_dir)`, which on
// Windows spawned `explorer.exe` on the temp dir — opening a real
// file-explorer window on the developer's machine on every
// `cargo test` run. The spawn is a side effect the contract does
// NOT need, so we test the pure [`preflight_path_exists`] check
// instead.) The temp dir always exists.
#[test]
fn test_open_path_accepts_existing_path() {
    let existing = std::env::temp_dir();
    assert!(existing.exists());
    assert!(
        preflight_path_exists(&existing).is_ok(),
        "pre-flight must accept an existing path without spawning the OS file manager"
    );
}

// The reaper thread must not leak the `Child` handle. We can't
// directly observe the zombie from Rust (the kernel reaps it
// asynchronously), but we CAN verify the spawn→reap path doesn't
// panic. We use `true` (POSIX) / `cmd /c ver` (Windows) as a
// stand-in for the file-manager binary — the reaper behavior is
// identical regardless of which binary spawned, since it just
// calls `child.wait()`.
#[test]
fn test_open_path_reaper_thread_does_not_panic() {
    // Spawns a REAL child of the test binary — serialize against the
    // own-pid enumeration tests (see test_support.rs).
    let _child_lock = CHILD_PROCESS_TEST_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    // We can't call open_path_in_file_manager with an arbitrary
    // binary (it hard-codes explorer/open/xdg-open), so this test
    // instead exercises the same spawn→reaper-thread pattern
    // directly. If the reaper thread logic were broken (e.g. the
    // `move ||` closure captured the wrong variable, or `wait()`
    // panicked on a moved `Child`), this test would fail.
    #[cfg(unix)]
    {
        let mut child = match std::process::Command::new("true").spawn() {
            Ok(c) => c,
            Err(e) => {
                eprintln!("skipping reaper test: `true` not available: {e}");
                return;
            }
        };
        let reaper = std::thread::spawn(move || child.wait());
        let _ = reaper.join().expect("reaper thread panicked");
    }
    #[cfg(target_os = "windows")]
    {
        let mut child = match std::process::Command::new("cmd")
            .args(["/c", "ver"])
            .spawn()
        {
            Ok(c) => c,
            Err(e) => {
                eprintln!("skipping reaper test: cmd.exe not available: {e}");
                return;
            }
        };
        let reaper = std::thread::spawn(move || child.wait());
        let _ = reaper.join().expect("reaper thread panicked");
    }
}
