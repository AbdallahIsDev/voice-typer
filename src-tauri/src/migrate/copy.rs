//! SQLite sidecar pathing + recursive file copy for the Electron →
//! Tauri migration.
//!
//! Extracted from the original `migrate.rs` monolith as part of the
//! Phase 4.5 split. Pure file move — no behavior change. See
//! `mod.rs` for the gating caller (`migrate_inner`).

use std::path::{Path, PathBuf};

// bring the `util` module into scope so `util::atomic_copy_file`
// (the atomic copy helper that was relocated from this module to
// `crate::util`) resolves without per-call-site qualification.
use crate::util;

/// M-65: build the path of a SQLite sidecar file (`-wal` / `-shm`)
/// for a given main db path. Appends the suffix to the literal
/// file_name (NOT to the extension) so `history.db` →
/// `history.db-wal`.
pub(crate) fn sidecar_path(db: &Path, suffix: &str) -> PathBuf {
    //`file_name()` returns `Option<&OsStr>`; `to_str()` borrows
    // as `&str` and then `.to_string()` allocates a new String from
    // the borrow. Using `to_os_string()` (which copies the OsStr into
    // a new OsString) then `into_string()` (which moves the OsString's
    // inner buffer into a String) avoids the second allocation on
    // valid-UTF-8 file names. On non-UTF-8 names we fall through to
    // the prior `db.to_path_buf()` fallback (same behavior).
    let mut name = match db.file_name().map(|n| n.to_os_string().into_string()) {
        Some(Ok(n)) => n,
        Some(Err(_)) | None => return db.to_path_buf(),
    };
    name.push_str(suffix);
    match db.parent() {
        Some(dir) => dir.join(name),
        None => PathBuf::from(name),
    }
}

/// Recursively copy files from `src` to `dst` that are ABSENT in `dst`.
/// Returns the count of files copied. Directory structure under `src` is
/// preserved. Never overwrites an existing target file.
pub(crate) fn copy_missing_files(src: &Path, dst: &Path) -> usize {
    let mut count = 0usize;
    copy_missing_recursive(src, dst, &mut count);
    count
}

fn copy_missing_recursive(src: &Path, dst: &Path, count: &mut usize) {
    let entries = match std::fs::read_dir(src) {
        Ok(e) => e,
        Err(e) => {
            log::error!("[MIGRATE] cannot read models dir {}: {}", src.display(), e);
            return;
        }
    };
    for entry in entries.flatten() {
        let path = entry.path();
        //use `symlink_metadata` (NOT `metadata`) so we can
        // detect symlinks WITHOUT following them. A pre-planted symlink
        // in the old Electron `models/` directory could otherwise point
        // outside the config dir (e.g. `~/.ssh/id_rsa` or `/etc/shadow`)
        // and we'd happily copy its target into the new config dir,
        // silently exfiltrating sensitive files. The prior `path.is_dir()`
        // / `path.is_file()` calls followed symlinks — they returned the
        // TARGET's file type, not the link's. `symlink_metadata` returns
        // metadata about the link itself, so `file_type().is_symlink()`
        // is reliable.
        let file_type = match std::fs::symlink_metadata(&path) {
            Ok(m) => m.file_type(),
            Err(e) => {
                log::warn!("[MIGRATE] cannot stat {}: {}", path.display(), e);
                continue;
            }
        };
        // Skip symlinks entirely — we never copy a symlink OR its
        // target during migration. Only regular files and dirs.
        if file_type.is_symlink() {
            log::warn!(
                "[MIGRATE] skipping symlink during migration (potential exfil attempt): {}",
                path.display()
            );
            continue;
        }
        //`entry.file_name().into_string()` consumes the OsString
        // and returns `Result<String, OsString>` — for valid-UTF-8 file
        // names (the overwhelmingly common case on all platforms Voice
        // Typer targets) this is a zero-allocation move out of the
        // OsString's inner buffer. The prior `to_str()` + `.to_string()`
        // form borrowed the OsString as `&str` then allocated a NEW
        // String from the borrow, doubling the heap traffic per entry.
        // On non-UTF-8 file names (rare; can occur on Linux ext4 with
        // legacy byte-string filenames), `into_string()` returns Err and
        // we `continue` — same behavior as the prior `None => continue`.
        let name = match entry.file_name().into_string() {
            Ok(n) => n,
            Err(_) => continue,
        };
        let dst_path = dst.join(&name);
        if file_type.is_dir() {
            if let Err(e) = std::fs::create_dir_all(&dst_path) {
                log::error!("[MIGRATE] cannot create dir {}: {}", dst_path.display(), e);
                continue;
            }
            copy_missing_recursive(&path, &dst_path, count);
        } else if file_type.is_file() {
            if dst_path.exists() {
                continue; // never clobber a newer download
            }
            //use atomic copy (temp + rename in same dir)
            // so an interrupted migration never leaves a partial model
            // file at the destination. Pre-fix, `std::fs::copy` truncated
            // then wrote — combined with the `if dst_path.exists() { continue; }`
            // guard above, a partial file from a killed migration looked
            // "existing" on next launch and was skipped, leaving a
            // corrupt model file in the target. The atomic copy writes
            // to a sibling temp file then renames, so the destination
            // is either fully-present or fully-absent — never partial.
            if let Err(e) = util::atomic_copy_file(&path, &dst_path) {
                log::error!(
                    "[MIGRATE] model file copy failed {}: {}",
                    dst_path.display(),
                    e
                );
            } else {
                *count += 1;
            }
        }
    }
}
