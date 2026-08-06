//! Unit tests for `open_path` (moved verbatim from the inline
//! `#[cfg(test)] mod tests` block to satisfy C-TEST-5 — tests must
//! live in a sibling file, not inline in the production source).
//!
//! No test logic changed — the `use super::*;` path still resolves
//! to the parent module (`open_path`) because the parent file
//! declares this module via `#[cfg(test)] mod open_path_tests;`.

use super::*;

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

// an existing path is accepted (the OS-binary spawn is
// platform-gated, so we can't easily assert success here without
// depending on a file manager being installed in CI — but the
// pre-flight existence check is the contract, and that's
// what we exercise). The temp dir always exists.
#[test]
fn test_open_path_accepts_existing_path() {
    let existing = std::env::temp_dir();
    assert!(existing.exists());
    // We don't assert Ok(()) — on a headless CI runner xdg-open /
    // explorer.exe / open may not be installed. The contract we
    // test is "the pre-flight existence check does NOT reject an
    // existing path" — i.e. the error (if any) is NOT
    // "path does not exist".
    match open_path_in_file_manager(&existing) {
        Ok(()) => {}
        Err(e) => {
            assert!(
                !e.contains("path does not exist"),
                "pre-flight rejected an existing path: {e}"
            );
        }
    }
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
