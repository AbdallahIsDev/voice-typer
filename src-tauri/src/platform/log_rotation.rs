//! Log-rotation policy extracted from `platform/logging.rs`.
//!
//! Owns the *policy* — when to rotate and how to shuffle the rename
//! chain — while `platform::log_file` owns the file-handle management
//! (open/append/flush). The split mirrors the Python side's
//! `RotatingFileHandler` (which separates `shouldRollover` policy from
//! `doRollover` action + the per-record `emit` write).
//!
//! # Rotation policy
//!
//! - **Trigger**: a single `should_rotate(current_size)` predicate —
//!   `true` once the in-memory byte counter crosses `ROTATE_MAX_BYTES`
//!   (5 MB). The trigger is evaluated lazily on the write that crosses
//!   the threshold, not on a timer — fine for our write volume and
//!   avoids a background thread.
//! - **Action**: `rotate(dir, base_name)` shuffles `.log.N-1` →
//!   `.log.N` (deleting the oldest at `ROTATE_MAX_FILES - 1`) and
//!   renames `.log` → `.log.1`. Loop bounds are tight against
//!   `ROTATE_MAX_FILES` so the total file count on disk is EXACTLY
//!   `ROTATE_MAX_FILES=5` (no off-by-one — see GT-67 pin test below).
//! - **Permissions**: every renamed `.log.N` file gets a best-effort
//!   `0o600` chmod on POSIX so a pre-hardening leftover (mode `0o644`)
//!   is tightened on the next rotation cycle. `rename` preserves the
//!   source mode (already `0o600` for files created post-hardening);
//!   the chmod is belt-and-suspenders.
//!
//! # Concurrency
//!
//! `rotate()` does NOT take a mutex — callers (`RotatingFileWriter::
//! write_line`) are expected to serialize rotations via a separate
//! `rotation_lock` (see `log_file.rs`). Two concurrent writers that
//! both cross the threshold will both reach the rotation path; the
//! `rotation_lock` ensures only one `rotate()` executes at a time and
//! the losers' calls are no-ops (`rename` of a nonexistent source
//! fails silently via `let _ =`).

use crate::util::{ROTATE_MAX_BYTES, ROTATE_MAX_FILES};
use std::path::Path;

// POSIX-only `Permissions::from_mode` trait import. On Windows this is
// a no-op (the OS uses ACLs, not mode bits) — the `#[cfg(unix)]` blocks
// below gate every call site.
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

/// Rotation-trigger predicate: `true` once the current file's
/// in-memory byte counter exceeds `ROTATE_MAX_BYTES` (5 MB).
///
/// Pure function over the byte counter — no I/O, no lock acquisition.
/// The caller (`RotatingFileWriter::write_line`) holds the file-handle
/// `Mutex` while calling this, so the read is race-free.
///
/// `u64::from(ROTATE_MAX_BYTES)` widens the `u32`-sized const to the
/// `u64` counter type without an `as` cast (which would silently
/// truncate on a future const widening).
pub(crate) fn should_rotate(current_size: u64) -> bool {
    current_size > u64::from(ROTATE_MAX_BYTES)
}

/// Rotate the rename chain: `.log.(N-1)` → `.log.N`, …,
/// `.log` → `.log.1`. Files at index `ROTATE_MAX_FILES - 1` (the
/// oldest) are deleted before the rename so the slot is free.
///
/// # Loop bound
///
/// The previous loop bound was `(1..ROTATE_MAX_FILES).rev()` (= 1,2,3,4)
/// with delete check `i + 1 >= ROTATE_MAX_FILES` (= `5 >= 5`). That
/// kept 6 files total (`.log`, `.log.1`..`.log.5`), one MORE than
/// `ROTATE_MAX_FILES=5` — an off-by-one that grew the disk cap from
/// 25 MB to 30 MB. The fix tightens the loop to
/// `(1..ROTATE_MAX_FILES - 1).rev()` (= 1,2,3) and the delete check
/// to `i + 1 >= ROTATE_MAX_FILES - 1` (= `4 >= 4`), so the total
/// file count is exactly `ROTATE_MAX_FILES=5` (pinned by the GT-67
/// test below).
///
/// # Errors
///
/// Returns `io::Result<()>` for API symmetry with the write path, but
/// individual `rename` / `remove_file` failures are silently
/// swallowed via `let _ =` — a missing source file (e.g. another
/// thread already rotated it) is the expected no-op case.
pub(crate) fn rotate(dir: &Path, base_name: &str) -> std::io::Result<()> {
    for i in (1..ROTATE_MAX_FILES - 1).rev() {
        let from = dir.join(format!("{}.log.{}", base_name, i));
        let to = dir.join(format!("{}.log.{}", base_name, i + 1));
        if from.exists() {
            if i + 1 >= ROTATE_MAX_FILES - 1 {
                // Oldest slot — delete what's there before renaming
                // (best-effort; ignore errors if the file is gone).
                let _ = std::fs::remove_file(&to);
            }
            let _ = std::fs::rename(&from, &to);
            // Belt-and-suspenders chmod of the renamed file to 0o600
            // on POSIX. `rename` preserves the source file's mode,
            // which should already be 0o600 (set by `write_line`'s
            // `OpenOptionsExt::mode` call), but a leftover rotated
            // file from a pre-hardening build may still be 0o644.
            // Best-effort: ignore errors (the file may have been
            // moved/deleted between the rename and the chmod —
            // extremely unlikely but defensive).
            #[cfg(unix)]
            {
                let _ = std::fs::set_permissions(
                    &to,
                    std::fs::Permissions::from_mode(0o600),
                );
            }
        }
    }
    // Final step: rotate the current `.log` → `.log.1`.
    let from = dir.join(format!("{}.log", base_name));
    let to = dir.join(format!("{}.log.1", base_name));
    if from.exists() {
        let _ = std::fs::rename(&from, &to);
        // Same belt-and-suspenders chmod for the `.log` → `.log.1`
        // rename above.
        #[cfg(unix)]
        {
            let _ = std::fs::set_permissions(
                &to,
                std::fs::Permissions::from_mode(0o600),
            );
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── should_rotate predicate ──────────────────────────────────────

    #[test]
    fn test_should_rotate_false_below_threshold() {
        // Bytes one below the threshold → no rotation.
        assert!(!should_rotate(0));
        assert!(!should_rotate(1));
        assert!(!should_rotate(u64::from(ROTATE_MAX_BYTES)));
    }

    #[test]
    fn test_should_rotate_true_above_threshold() {
        // One byte over the threshold → rotate.
        assert!(should_rotate(u64::from(ROTATE_MAX_BYTES) + 1));
        assert!(should_rotate(u64::from(ROTATE_MAX_BYTES) * 10));
    }

    // ── rotate() end-to-end (integration with a real tmpdir) ─────────
    //
    // The `rotate()` function is a pure filesystem operation — we
    // exercise it by creating `.log` + `.log.N` files in a tmpdir,
    // calling `rotate()`, and asserting the rename chain produced the
    // expected file layout. This is the same suite of assertions the
    // prior in-logging.rs tests made; they were moved here when the
    // rotation policy was extracted.

    fn tmpdir(label: &str) -> std::path::PathBuf {
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-{label}",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        std::fs::create_dir_all(&tmp).unwrap();
        tmp
    }

    #[test]
    fn test_rotate_renames_log_to_log_1() {
        let tmp = tmpdir("rotate-basic");
        let dir = tmp.as_path();
        let base = "test-log";
        // Seed the current `.log` file.
        std::fs::write(dir.join(format!("{base}.log")), b"current").unwrap();
        // Rotate.
        rotate(dir, base).unwrap();
        // `.log` should be gone; `.log.1` should contain "current".
        assert!(!dir.join(format!("{base}.log")).exists());
        let one = std::fs::read_to_string(dir.join(format!("{base}.log.1"))).unwrap();
        assert_eq!(one, "current");
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn test_rotate_shifts_existing_log_n_files_up_by_one() {
        let tmp = tmpdir("rotate-shift");
        let dir = tmp.as_path();
        let base = "test-log";
        // Seed `.log`, `.log.1`, `.log.2`, `.log.3` (just under the cap
        // of ROTATE_MAX_FILES=5, so all should survive the rotation).
        std::fs::write(dir.join(format!("{base}.log")), b"c").unwrap();
        std::fs::write(dir.join(format!("{base}.log.1")), b"1").unwrap();
        std::fs::write(dir.join(format!("{base}.log.2")), b"2").unwrap();
        std::fs::write(dir.join(format!("{base}.log.3")), b"3").unwrap();
        rotate(dir, base).unwrap();
        // After rotation: `.log` gone; `.log.1`="c"; `.log.2`="1";
        // `.log.3`="2"; `.log.4`="3".
        assert!(!dir.join(format!("{base}.log")).exists());
        assert_eq!(std::fs::read_to_string(dir.join(format!("{base}.log.1"))).unwrap(), "c");
        assert_eq!(std::fs::read_to_string(dir.join(format!("{base}.log.2"))).unwrap(), "1");
        assert_eq!(std::fs::read_to_string(dir.join(format!("{base}.log.3"))).unwrap(), "2");
        assert_eq!(std::fs::read_to_string(dir.join(format!("{base}.log.4"))).unwrap(), "3");
        std::fs::remove_dir_all(&tmp).ok();
    }

    // ── GT-67: pin the exact file count after many rotations ──────────
    //
    // The previous rotation loop kept `ROTATE_MAX_FILES + 1` files on
    // disk (off-by-one). This test stages a full rename chain (`.log`
    // + `.log.1`..`.log.4` = 5 files, the max) AND a stale `.log.5`
    // (the slot that should be deleted), then rotates and asserts:
    // - `.log.5` is gone (deleted by the rotate loop's delete-oldest
    //   branch).
    // - Total file count is EXACTLY `ROTATE_MAX_FILES` (no more, no
    //   less).

    #[test]
    fn test_rotate_pins_exact_file_count_at_cap() {
        let tmp = tmpdir("rotate-cap");
        let dir = tmp.as_path();
        let base = "test-log";
        // Stage the max allowed files PLUS one stale overflow slot.
        std::fs::write(dir.join(format!("{base}.log")), b"c").unwrap();
        for i in 1..=ROTATE_MAX_FILES {
            std::fs::write(dir.join(format!("{base}.log.{i}")), format!("{i}")).unwrap();
        }
        rotate(dir, base).unwrap();
        // `.log.ROTATE_MAX_FILES` (=5) — the stale overflow slot —
        // must be gone (deleted by the loop's delete-oldest branch).
        assert!(
            !dir.join(format!("{base}.log.{ROTATE_MAX_FILES}")).exists(),
            "GT-67: `.log.{ROTATE_MAX_FILES}` (one past the cap) must NOT exist after rotate"
        );
        // Count survivors: `.log.1`..`.log.{ROTATE_MAX_FILES - 1}`.
        let mut count = 0;
        for i in 1..ROTATE_MAX_FILES {
            if dir.join(format!("{base}.log.{i}")).exists() {
                count += 1;
            }
        }
        assert_eq!(
            count,
            ROTATE_MAX_FILES - 1,
            "GT-67: expected {} rotated files (`.log.1`..`.log.{}); found {}",
            ROTATE_MAX_FILES - 1,
            ROTATE_MAX_FILES - 1,
            count
        );
        // `.log` (current) was renamed to `.log.1` — so it no longer
        // exists at the original path.
        assert!(!dir.join(format!("{base}.log")).exists());
        std::fs::remove_dir_all(&tmp).ok();
    }

    // ── PI-7: rotated files get 0o600 on POSIX ────────────────────────
    //
    // After rotation, the renamed `.log.1` file must also have mode
    // `0o600`. `rename` preserves the source file's mode, plus the
    // belt-and-suspenders `chmod` in `rotate` re-asserts 0o600 in case
    // a pre-hardening leftover file had looser perms.

    #[cfg(unix)]
    #[test]
    fn test_rotate_chmods_rotated_files_to_0o600_on_posix() {
        use std::os::unix::fs::PermissionsExt;
        let tmp = tmpdir("rotate-mode");
        let dir = tmp.as_path();
        let base = "test-log";
        // Seed `.log` AND a pre-hardening leftover `.log.1` at 0o644
        // (mimics a stale rotated file from a pre-hardening build).
        std::fs::write(dir.join(format!("{base}.log")), b"current").unwrap();
        std::fs::write(dir.join(format!("{base}.log.1")), b"stale").unwrap();
        std::fs::set_permissions(
            dir.join(format!("{base}.log.1")),
            std::fs::Permissions::from_mode(0o644),
        )
        .unwrap();
        rotate(dir, base).unwrap();
        // After rotation, the old `.log.1` is now `.log.2` and the
        // belt-and-suspenders chmod should have tightened it to 0o600.
        let rotated_to_2 = dir.join(format!("{base}.log.2"));
        assert!(rotated_to_2.exists(), "renamed .log.1 -> .log.2 must exist");
        let mode = std::fs::metadata(&rotated_to_2)
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(
            mode, 0o600,
            "PI-7: rotated (was 0o644) file mode must be tightened to 0o600; got 0o{:o}",
            mode
        );
        // The new `.log.1` (was `.log`) must also be 0o600 — `rename`
        // preserves the source's mode (which was 0o600) AND the chmod
        // re-asserts it.
        let new_one = dir.join(format!("{base}.log.1"));
        let mode = std::fs::metadata(&new_one).unwrap().permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o600,
            "PI-7: renamed .log -> .log.1 mode must be 0o600; got 0o{:o}",
            mode
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    // ── no-op when no files exist ─────────────────────────────────────
    //
    // `rotate()` must be safe to call on an empty dir — the rename
    // chain has nothing to do, every `from.exists()` returns false,
    // and the function returns `Ok(())` without touching the disk.

    #[test]
    fn test_rotate_no_op_on_empty_dir() {
        let tmp = tmpdir("rotate-empty");
        let dir = tmp.as_path();
        let base = "test-log";
        // No files staged.
        rotate(dir, base).unwrap();
        // Still no files.
        assert!(!dir.join(format!("{base}.log")).exists());
        assert!(!dir.join(format!("{base}.log.1")).exists());
        std::fs::remove_dir_all(&tmp).ok();
    }
}
