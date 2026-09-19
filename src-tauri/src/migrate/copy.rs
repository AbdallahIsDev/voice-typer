
use std::path::{Path, PathBuf};

use crate::util;

pub(crate) fn sidecar_path(db: &Path, suffix: &str) -> PathBuf {
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

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub(crate) struct CopyStats {
    pub(crate) copied: usize,
    pub(crate) failed: usize,
}

pub(crate) fn copy_missing_files(src: &Path, dst: &Path) -> CopyStats {
    let mut stats = CopyStats::default();
    copy_missing_recursive(src, dst, &mut stats);
    stats
}

fn copy_missing_recursive(src: &Path, dst: &Path, stats: &mut CopyStats) {
    let entries = match std::fs::read_dir(src) {
        Ok(e) => e,
        Err(e) => {
            log::error!("[MIGRATE] cannot read models dir {}: {}", src.display(), e);
            return;
        }
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let file_type = match std::fs::symlink_metadata(&path) {
            Ok(m) => m.file_type(),
            Err(e) => {
                log::warn!("[MIGRATE] cannot stat {}: {}", path.display(), e);
                continue;
            }
        };
        // Skip symlinks entirely: we never copy a symlink OR its
        // target during migration. Only regular files and dirs.
        if file_type.is_symlink() {
            log::warn!(
                "[MIGRATE] skipping symlink during migration (potential exfil attempt): {}",
                path.display()
            );
            continue;
        }
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
            copy_missing_recursive(&path, &dst_path, stats);
        } else if file_type.is_file() {
            if dst_path.exists() {
                continue; // never clobber a newer download
            }
            if let Err(e) = util::atomic_copy_file(&path, &dst_path) {
                log::warn!(
                    "[MIGRATE] model file copy failed {}: {}",
                    dst_path.display(),
                    e
                );
                stats.failed += 1;
            } else {
                stats.copied += 1;
            }
        }
    }
}
