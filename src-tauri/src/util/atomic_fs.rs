//! Atomic filesystem write/copy helpers.
//!
//! Split out of the former catch-all `util.rs` so the generic
//! durable-fs primitives (temp file + fsync + same-filesystem rename)
//! live in one focused module — they are consumed almost entirely by
//! `migrate/*`, plus `sidecar/supervisor.rs` (restart counter) and
//! `commands/export.rs`. Re-exported from `crate::util` so every
//! existing `crate::util::atomic_write_bytes` /
//! `crate::util::atomic_copy` / `crate::util::atomic_copy_file` path
//! keeps resolving (including the unqualified `util::atomic_*` paths
//! used inside `migrate/`).

use rand::RngCore;
use std::path::Path;

/// Write `contents` to `path` atomically (temp + fsync + rename).
///
/// Originally implemented in `migrate.rs` for the Electron→Tauri
//config migration; promoted from `fn` to `pub(crate) fn` in
/// so the supervisor (`supervisor.rs::write_restart_counter`) could reuse
/// it for atomic persistence of the restart counter. Previously the
/// counter used `std::fs::write` (non-atomic: truncate-then-write),
/// which on a crash mid-write could leave a partially-written
/// `restart_counter.json` that fails to parse — falling back to 0
/// (the fail-open default in `read_restart_counter`), silently
/// bypassing the circuit breaker on the next launch.
///
/// Moved here because it is a generic fs-write helper that has nothing
/// to do with Electron migration; two non-migration callers
/// (`sidecar/supervisor.rs`, `commands/export.rs`) import it from
/// `crate::util` instead of `crate::migrate`.
pub(crate) fn atomic_write_bytes(path: &Path, contents: &[u8]) -> Result<(), String> {
    use std::io::Write;

    let dir = path
        .parent()
        .ok_or_else(|| format!("path has no parent: {}", path.display()))?;
    // Same dir as target so rename is atomic (same filesystem).
    // The temp filename is dotted so it doesn't show up in normal
    // directory listings and is prefixed with the target's filename
    // so a human inspecting the dir can tell what it's for.
    //
    //include a unique suffix (PID + 4 random bytes hex) so
    // concurrent invocations on the same target path don't race on
    // the same temp filename. Pre-fix the deterministic name
    // `.NAME.tmp.migrate` meant two concurrent `atomic_write_bytes`
    // calls to the same `path` would: (1) both open the SAME temp
    // file with `File::create` (which truncates), (2) interleave
    // writes, (3) race the rename — corrupted content + lost writes.
    // The PID disambiguates across processes; the 4-byte random
    // suffix disambiguates within a process (multiple threads, or
    // rapid sequential calls). `rand::rng()` is the thread-local RNG
    // (rand 0.9 API) — same as `generate_token` uses.
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
        // fsync the file so the data is on disk before we rename.
        // Without this, a crash after rename but before the kernel
        // flushes the file's data could leave the new file with zero
        // bytes (POSIX allows this).
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

    // Fsync the parent directory after the rename on
    // POSIX so the rename itself is durable. Without this, a crash
    // after the rename returns but before the kernel flushes the
    // directory entry could leave the OLD file (or NO file) at `path`
    // on next mount — the file data is durable (we fsync'd the temp
    // file above), but the directory metadata linking the new name to
    // the inode is not. Mirrors the Python side's
    // `_secure_atomic_write` pattern at
    // `voice_typer/server/secure_file_io.py:100-113`.
    //
    // Best-effort: a `sync_all` failure on the parent dir does NOT
    // fail the write (matches the Python side's suppress-and-continue
    // pattern). The data is already safely in the new file; the only
    // loss on a crash-before-dir-fsync is the rename itself, which on
    // next mount would surface as "the old file is still there" — a
    // known acceptable degradation that the Python side also accepts.
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

// ─── atomic_copy / atomic_copy_file ( moved from migrate.rs) ──
//
// these two helpers previously lived in `migrate.rs` next to
//the (now-removed) `atomic_write_bytes` impl.  already moved
// `atomic_write_bytes` here because it's a generic fs-write helper
// with no coupling to Electron-migration logic; the same reasoning
// applies to `atomic_copy` / `atomic_copy_file` — they're generic
// fs-copy helpers. Co-locating all three atomic-fs helpers in
// `util.rs` lets the `migrate.rs` callers reach them via a single
// `util::` qualification and drops the bridge
// `use crate::util::atomic_write_bytes;` import that lived in
// `migrate.rs` solely to let `atomic_copy` call `atomic_write_bytes`
// unqualified. Pure refactor — no behavior change.

/// Atomically copy `src` to `dst` by reading src into memory
/// then writing via `atomic_write_bytes`. Suitable for small-to-
/// medium files (config.json, history.db, WAL sidecars). For very
/// large files (model weights) use `atomic_copy_file` instead — it
/// streams via `std::fs::copy` to a sibling temp file then renames,
/// avoiding the memory doubling that `atomic_copy`'s read-into-memory
/// would impose on multi-GB model weights.
pub(crate) fn atomic_copy(src: &Path, dst: &Path) -> Result<(), String> {
    let bytes = std::fs::read(src).map_err(|e| format!("read src {}: {}", src.display(), e))?;
    atomic_write_bytes(dst, &bytes)
}

//atomically copy a (potentially large) file from `src` to
/// `dst` by streaming to a sibling temp file then renaming. Unlike
/// `atomic_copy` (which reads the entire source into memory), this
/// streams the bytes via `std::fs::copy` so it's suitable for multi-GB
/// model weight files. The temp file lives in the SAME directory as
/// `dst` (so `rename` is an atomic same-filesystem op on POSIX), is
/// fsync'd before the rename (so the data is durable), and is
/// best-effort cleaned up on failure (so we don't leak temp files).
///
/// On a crash mid-copy, the temp file is left behind (best-effort
/// cleanup only runs on the Err path of THIS function); a future
/// launch's `atomic_copy_file` call to the same `dst` will simply
/// create a NEW temp file (unique suffix via PID + random) and the
/// stale temp file will be orphaned. The orphan is harmless (it's a
/// dotfile in the user's config dir) and the destination is never
/// left in a partial state.
pub(crate) fn atomic_copy_file(src: &Path, dst: &Path) -> Result<(), String> {
    let dir = dst
        .parent()
        .ok_or_else(|| format!("dst has no parent: {}", dst.display()))?;
    // Same uniqueness scheme as `atomic_write_bytes` in util.rs —
    // PID + 4 random bytes hex so concurrent invocations on the same
    // dst don't race on the same temp filename.
    let tmp_name = match dst.file_name().and_then(|n| n.to_str()) {
        Some(n) => {
            let mut rng_bytes = [0u8; 4];
            rand::rng().fill_bytes(&mut rng_bytes);
            let suffix = u32::from_le_bytes(rng_bytes);
            format!("{}.tmp.copy.{}.{:08x}", n, std::process::id(), suffix)
        }
        None => return Err(format!("dst has no file_name: {}", dst.display())),
    };
    let tmp = dir.join(&tmp_name);

    // Stream-copy src → tmp via std::fs::copy (kernel-level splice on
    // Linux, no userspace buffering — efficient for large files).
    if let Err(e) = std::fs::copy(src, &tmp) {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!("copy {} → {}: {}", src.display(), tmp.display(), e));
    }

    // fsync the temp file so the data is durable before the rename.
    // Without this, a crash after rename but before the kernel flushes
    // the temp file's data could leave the renamed file with zero
    // bytes (ext4's auto-no-csum mode) — corrupting the destination.
    {
        let f = std::fs::File::open(&tmp)
            .map_err(|e| format!("open tmp for fsync {}: {}", tmp.display(), e))?;
        // Best-effort fsync — not all filesystems support it (tmpfs,
        // network FS), and a failure here doesn't invalidate the copy
        // (the data is still in the page cache and will be flushed
        // eventually). Log and continue.
        if let Err(e) = f.sync_all() {
            // kept the historical `[MIGRATE]` log prefix
            // (this helper was originally in `migrate.rs`) so log
            // aggregators / test fixtures that match on the prefix
            // keep working — pure refactor, no log-output change.
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
    Ok(())
}

// Sibling test module — tests live in `atomic_fs_tests.rs` (per
// C-TEST-5: no inline `#[cfg(test)] mod tests` blocks in production
// source).
#[cfg(test)]
#[path = "atomic_fs_tests.rs"]
mod atomic_fs_tests;
