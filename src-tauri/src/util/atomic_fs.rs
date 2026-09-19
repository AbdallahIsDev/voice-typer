
use rand::RngCore;
use std::path::Path;

pub(crate) fn atomic_write_bytes(path: &Path, contents: &[u8]) -> Result<(), String> {
    use std::io::Write;

    let dir = path
        .parent()
        .ok_or_else(|| format!("path has no parent: {}", path.display()))?;
    let tmp_name = match path.file_name().and_then(|n| n.to_str()) {
        Some(n) => {
            let mut rng_bytes = [0u8; 4];
            rand::rng().fill_bytes(&mut rng_bytes);
            let suffix = u32::from_le_bytes(rng_bytes);
            format!(".{}.tmp.{}.{:08x}", n, std::process::id(), suffix)
        }
        None => return Err(format!("path has no file_name: {}", path.display())),
    };
    let tmp = dir.join(&tmp_name);

    {
        let mut f = std::fs::File::create(&tmp)
            .map_err(|e| format!("create tmp {}: {}", tmp.display(), e))?;
        f.write_all(contents)
            .map_err(|e| format!("write tmp {}: {}", tmp.display(), e))?;
        f.sync_all()
            .map_err(|e| format!("fsync tmp {}: {}", tmp.display(), e))?;
        // Drop the file handle BEFORE rename so Windows can rename
        // (Windows refuses to rename a file that's still open).
    }

    std::fs::rename(&tmp, path).map_err(|e| {
        // Best-effort cleanup of the temp file on rename failure so
        // we don't leave orphaned .tmp.migrate files lying around.
        let _ = std::fs::remove_file(&tmp);
        format!("rename {} -> {}: {}", tmp.display(), path.display(), e)
    })?;

    #[cfg(unix)]
    {
        if let Some(parent) = path.parent() {
            if let Ok(dir) = std::fs::File::open(parent) {
                let _ = dir.sync_all();
            }
        }
    }

    Ok(())
}


pub(crate) fn atomic_copy(src: &Path, dst: &Path) -> Result<(), String> {
    let bytes = std::fs::read(src).map_err(|e| format!("read src {}: {}", src.display(), e))?;
    atomic_write_bytes(dst, &bytes)
}

pub(crate) fn atomic_copy_file(src: &Path, dst: &Path) -> Result<(), String> {
    let dir = dst
        .parent()
        .ok_or_else(|| format!("dst has no parent: {}", dst.display()))?;
    let tmp_name = match dst.file_name().and_then(|n| n.to_str()) {
        Some(n) => {
            let mut rng_bytes = [0u8; 4];
            rand::rng().fill_bytes(&mut rng_bytes);
            let suffix = u32::from_le_bytes(rng_bytes);
            format!(".{}.tmp.copy.{}.{:08x}", n, std::process::id(), suffix)
        }
        None => return Err(format!("dst has no file_name: {}", dst.display())),
    };
    let tmp = dir.join(&tmp_name);

    // Stream-copy src → tmp via std::fs::copy (kernel-level splice on
    // Linux, no userspace buffering: efficient for large files).
    if let Err(e) = std::fs::copy(src, &tmp) {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!("copy {} → {}: {}", src.display(), tmp.display(), e));
    }

    {
        let f = std::fs::OpenOptions::new()
            .write(true)
            .open(&tmp)
            .map_err(|e| format!("open tmp for fsync {}: {}", tmp.display(), e))?;
        if let Err(e) = f.sync_all() {
            log::warn!(
                "[MIGRATE] fsync of tmp {} failed (non-fatal): {}",
                tmp.display(),
                e
            );
        }
    }

    // Atomic rename (same-filesystem on POSIX; on Windows the dst is
    // absent so rename succeeds).
    if let Err(e) = std::fs::rename(&tmp, dst) {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!(
            "rename {} → {}: {}",
            tmp.display(),
            dst.display(),
            e
        ));
    }

    #[cfg(unix)]
    {
        if let Some(parent) = dst.parent() {
            if let Ok(dir) = std::fs::File::open(parent) {
                let _ = dir.sync_all();
            }
        }
    }

    Ok(())
}

// Sibling test module: tests live in `atomic_fs_tests.rs` (per
// C-TEST-5: no inline `#[cfg(test)] mod tests` blocks in production
// source).
#[cfg(test)]
#[path = "atomic_fs_tests.rs"]
mod atomic_fs_tests;
