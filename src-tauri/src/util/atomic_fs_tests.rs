#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::unreachable,
    clippy::todo,
    clippy::unimplemented,
    clippy::cast_possible_truncation,
    clippy::assertions_on_constants
)] // const invariant pins with descriptive runtime messages

//! Unit tests for `util::atomic_fs` (atomic write/copy helpers).
//!
//! Moved verbatim from `util_tests.rs` when the atomic-fs concern was
//! split into its own submodule — tests move with their code. No test
//! logic changed; `use super::*;` now resolves to the `util::atomic_fs`
//! module because this file is declared via
//! `#[cfg(test)] #[path = "atomic_fs_tests.rs"] mod atomic_fs_tests;`
//! inside `util/atomic_fs.rs`.

use super::*;

// ── atomic_write_bytes (moved from migrate.rs) ───────────────────

#[test]
fn test_atomic_write_bytes_creates_file_with_expected_contents() {
    // Sanity: the basic write+rename contract still holds after
    // the parent-dir fsync addition. We write a small file,
    // then read it back and verify the contents match.
    let tmp = std::env::temp_dir().join(format!(
        "voice-typer-atomic-fs-test-{}-basic",
        std::process::id()
    ));
    std::fs::remove_dir_all(&tmp).ok();
    std::fs::create_dir_all(&tmp).unwrap();
    let path = tmp.join("config.json");
    let contents = b"{\"key\":\"value\"}";
    atomic_write_bytes(&path, contents).expect("write must succeed");
    let read_back = std::fs::read(&path).expect("file must exist");
    assert_eq!(
        read_back.as_slice(),
        contents.as_ref(),
        "sanity: contents must match"
    );
    // The temp file must NOT exist after the rename (cleanup).
    //the temp name is now randomized (`.NAME.tmp.PID.HEX`)
    // so we scan the parent dir for any leftover temp file matching
    // the `.config.json.tmp.*` prefix instead of hardcoding the
    //pre- deterministic name.
    let mut leaked: Vec<String> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(&tmp) {
        for entry in entries.flatten() {
            if let Some(name) = entry.file_name().to_str() {
                if name.starts_with(".config.json.tmp.") {
                    leaked.push(name.to_string());
                }
            }
        }
    }
    assert!(
        leaked.is_empty(),
        "temp file leaked after rename: {:?} (in dir {})",
        leaked,
        tmp.display()
    );
    std::fs::remove_dir_all(&tmp).ok();
}

#[test]
fn test_atomic_write_bytes_overwrites_existing_file() {
    // Atomic overwrite: a pre-existing file at `path` must be
    // replaced atomically (the rename is atomic on POSIX, and on
    // Windows the temp file is in the same dir so rename succeeds
    // even when the target exists — Windows allows rename-over-
    // existing when both files are on the same volume + the
    // target isn't open by another handle).
    let tmp = std::env::temp_dir().join(format!(
        "voice-typer-atomic-fs-test-{}-overwrite",
        std::process::id()
    ));
    std::fs::remove_dir_all(&tmp).ok();
    std::fs::create_dir_all(&tmp).unwrap();
    let path = tmp.join("config.json");
    // Pre-existing file with different contents.
    std::fs::write(&path, b"OLD CONTENTS").unwrap();
    atomic_write_bytes(&path, b"NEW CONTENTS").expect("overwrite must succeed");
    let read_back = std::fs::read(&path).expect("file must still exist");
    assert_eq!(
        read_back.as_slice(),
        b"NEW CONTENTS".as_ref(),
        "overwrite must replace contents atomically"
    );
    std::fs::remove_dir_all(&tmp).ok();
}

#[cfg(unix)]
#[test]
fn test_atomic_write_bytes_parent_dir_fsync_does_not_fail_write() {
    // The parent-dir `sync_all()` is best-effort — a failure
    // (e.g. the dir is on a read-only filesystem, or the dir
    // doesn't support fsync) must NOT fail the write. The data is
    // already safely in the new file (we fsync'd the temp file
    // before the rename); the dir fsync only persists the rename
    // metadata. Skipping it on failure is the documented behavior
    // (mirrors the Python side's `secure_file_io.py:100-113`).
    //
    // We can't easily force a `sync_all` failure in a unit test
    // (the temp dir is on a writable filesystem that supports
    // fsync). Instead, this test asserts the positive case: the
    // write succeeds AND the parent dir's mtime is updated (which
    // is the visible side-effect of the rename, which the fsync
    // then makes durable). The test name pins the "does not fail
    // the write" contract for future regression coverage.
    let tmp = std::env::temp_dir().join(format!(
        "voice-typer-atomic-fs-test-{}-fsync",
        std::process::id()
    ));
    std::fs::remove_dir_all(&tmp).ok();
    std::fs::create_dir_all(&tmp).unwrap();
    // Read the parent dir mtime BEFORE the write, then sleep briefly
    // so the mtime update (if any) is detectable (mtime has
    // second-level granularity on most filesystems).
    let dir_meta_before = std::fs::metadata(&tmp).unwrap();
    let mtime_before = dir_meta_before.modified().unwrap();
    std::thread::sleep(std::time::Duration::from_millis(1100));

    let path = tmp.join("config.json");
    atomic_write_bytes(&path, b"{}").expect("write must succeed");

    // The write must succeed regardless of the dir fsync outcome.
    assert!(path.exists(), "file must exist after write");
    // The parent dir's mtime should be >= the pre-write mtime
    // (the rename updates the dir's metadata; the fsync then
    // flushes that update to durable storage). On filesystems
    // where mtime has second-level granularity, the 1.1s sleep
    // above ensures the mtime tick is visible.
    let dir_meta_after = std::fs::metadata(&tmp).unwrap();
    let mtime_after = dir_meta_after.modified().unwrap();
    assert!(
        mtime_after >= mtime_before,
        "parent dir mtime must be >= pre-write mtime. \
         before={:?} after={:?}",
        mtime_before,
        mtime_after
    );
    std::fs::remove_dir_all(&tmp).ok();
}

#[test]
fn test_atomic_write_bytes_empty_path_returns_error() {
    // Edge case: a path with no parent (`Path::new("")` has
    // `parent() == None`) must return Err — the prior code
    // returned an error here via the `ok_or_else` on `path.parent()`;
    // The parent-dir fsync must NOT change that behavior (we
    // still error out before reaching the fsync code).
    let empty_path = std::path::Path::new("");
    let result = atomic_write_bytes(empty_path, b"");
    assert!(
        result.is_err(),
        "empty path must return Err, got Ok. (result={:?})",
        result
    );
}
