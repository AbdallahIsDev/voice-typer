//! JSON `config.json` merge logic for the Electron → Tauri migration.
//!
//! Extracted from the original `migrate.rs` monolith as part of the
//! Phase 4.5 split. Pure file move — no behavior change
//! EXCEPT for the micro-fix that consumes `old_val` (move) in
//! the merge loop instead of borrowing + cloning. See `mod.rs` for
//! the gating caller (`migrate_inner`).

use std::path::Path;

// bring the `util` module into scope so the atomic-fs helpers
// (`util::atomic_copy`, `util::atomic_write_bytes`) resolve without
// per-call-site `crate::util::` qualification.
use crate::util;

#[derive(Debug)]
pub(crate) enum MergeOutcome {
    Copied,
    Merged(usize),
}

/// Merge `old` config.json into `new` config.json.
///
/// - If `new` does not exist, copy the whole file (Copied).
/// - If `new` exists: merge key-by-key. The entire newer file's values
///   win for overlapping keys (single whole-file mtime comparison —
///   NOT per-key mtime). Keys present only in `old` are always taken.
///   Returns Merged(keys_from_old_written).
///
//the previous docstring promised "newest-mtime-wins per
/// key" which suggested per-key mtime resolution. The implementation
/// uses a single whole-file mtime comparison (the file written later
/// is treated as authoritative for ALL its overlapping keys, not just
/// per-key). The docstring now matches the implementation.
///
/// H-19 (IMPROVE-2026-07-19): all writes are now ATOMIC (temp-file +
/// `rename`). Previously `std::fs::copy` and `std::fs::write` truncated
/// the target before writing — a crash mid-write (power loss, SIGKILL,
/// OOM) would leave `config.json` truncated/corrupt. Since `migrate.rs`
/// runs BEFORE the Python sidecar spawns, the sidecar would boot against
/// a corrupt config and fall back to defaults — permanently losing the
/// user's migrated Electron config. The atomic write ensures the target
/// is either fully-old or fully-new, never partial.
///
/// The merge loop now CONSUMES `old_val` (moves the owned
/// `Map<String, Value>` out of the `Value::Object` variant) instead of
/// borrowing `old_val.as_object()` and cloning every key+value pair on
/// insertion. The move is sound because `old_val` is owned by this
/// function and is never read again after the loop. For users with
/// multi-MB Electron configs this drops N deep-clone allocations per
/// migration (first-launch-only cost, but the pattern is also more
/// idiomatic — future copy-paste won't replicate the clone).
pub(crate) fn merge_config(old: &Path, new: &Path) -> Result<MergeOutcome, String> {
    if !new.exists() {
        // M-65: atomic copy so an interrupted migration never leaves
        // a partially-written config.json at the target.
        util::atomic_copy(old, new)?;
        return Ok(MergeOutcome::Copied);
    }

    let old_txt = std::fs::read_to_string(old).map_err(|e| e.to_string())?;
    let new_txt = std::fs::read_to_string(new).map_err(|e| e.to_string())?;

    // T3-05: previously used `unwrap_or(Value::Null)` which silently
    // swallowed parse errors. A corrupt source or target config.json
    // would silently be treated as `null` (an empty object on merge),
    // potentially losing the user's settings on the next migration
    // pass. Log a warning so the failure is observable in user logs
    // (the merge itself still proceeds fail-open — we prefer to keep
    // whatever parses rather than abort the whole migration).
    //
    //before treating a corrupt source/target as Null,
    // back up the corrupt file to `<path>.corrupt-pre-migration.<ts>.bak`
    // so the user can recover their settings manually. Without the
    // backup, a corrupt `config.json` would be silently dropped on
    // the next migration pass — the user's old Electron settings
    // vanish with no recovery path.
    let old_val: serde_json::Value = match serde_json::from_str(&old_txt) {
        Ok(v) => v,
        Err(e) => {
            log::warn!("[MIGRATE] old config.json parse failed (treating as empty): {}", e);
            backup_corrupt_config(old);
            serde_json::Value::Null
        }
    };
    let new_val: serde_json::Value = match serde_json::from_str(&new_txt) {
        Ok(v) => v,
        Err(e) => {
            log::warn!("[MIGRATE] new config.json parse failed (treating as empty): {}", e);
            backup_corrupt_config(new);
            serde_json::Value::Null
        }
    };

    // Consume `old_val` (owned) by matching on the `Value` enum
    // and moving the inner `Map<String, Value>` out. Pre-fix the code
    // borrowed `old_val.as_object()` and cloned every key+value pair
    // when inserting into `base`. The owned form lets the loop body
    // MOVE both `k` and `v` into `base` (no clone). If `old_val` is
    // not an object, there's nothing useful to merge — keep target.
    let old_obj: serde_json::Map<String, serde_json::Value> = match old_val {
        serde_json::Value::Object(o) => o,
        _ => return Ok(MergeOutcome::Merged(0)),
    };

    // Newest-mtime-wins: compare the two files' mtimes; the file written
    // later is treated as authoritative for its keys. Per-key we pick the
    // source (old vs new) that wins for that key.
    let old_newer = file_newer_than(old, new);

    let mut base = match new_val.as_object() {
        Some(o) => o.clone(),
        None => serde_json::Map::new(),
    };

    let mut written = 0usize;
    // Iterate OWNED entries of `old_obj` (consumes the Map).
    // `base.get(&k)` borrows `k` for the lookup; once the borrow ends
    // we move `k` (and `v`) into `base` via `insert`. No `.clone()`
    // on either key or value.
    for (k, v) in old_obj {
        let take_old = match base.get(&k) {
            // Key present in target — winner determined by file mtime.
            Some(_) => old_newer == Some(true),
            // Key absent in target — always take old.
            None => true,
        };
        if take_old {
            base.insert(k, v);
            written += 1;
        }
    }

    let merged = serde_json::Value::Object(base);
    let out = serde_json::to_string_pretty(&merged).map_err(|e| e.to_string())?;
    // M-65: write atomically (temp + fsync + rename in the same dir)
    // so an interrupted migration never leaves a partially-written
    // config.json that would fail to parse on next launch and cause
    // the user's merged settings to be lost.
    util::atomic_write_bytes(new, out.as_bytes())?;
    Ok(MergeOutcome::Merged(written))
}

//back up a corrupt config.json before the migration
/// treats it as `Value::Null`. The backup is written next to the
/// original as `<filename>.corrupt-pre-migration.<unix_ts>.bak` so
/// the user can recover their settings manually. The timestamp
/// ensures we never overwrite a prior backup (two corrupt migrations
/// at different times produce two distinct .bak files).
///
/// Best-effort — if the backup fails (e.g. disk full, permissions),
/// we log a warning and continue. The migration's fail-open behavior
/// (treat as Null) proceeds either way; this function only adds a
/// recovery path, it doesn't change the merge logic.
fn backup_corrupt_config(path: &Path) {
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let name = match path.file_name().and_then(|n| n.to_str()) {
        Some(n) => n.to_string(),
        None => {
            log::warn!(
                "[MIGRATE] cannot back up corrupt config (invalid file name): {}",
                path.display()
            );
            return;
        }
    };
    let backup_name = format!("{}.corrupt-pre-migration.{}.bak", name, ts);
    let backup = match path.parent() {
        Some(dir) => dir.join(&backup_name),
        None => return,
    };
    // Don't overwrite an existing backup (extremely unlikely with the
    // timestamp suffix, but defensive against clock skew).
    if backup.exists() {
        log::warn!(
            "[MIGRATE] corrupt-config backup already exists (skipping): {}",
            backup.display()
        );
        return;
    }
    match std::fs::copy(path, &backup) {
        Ok(_) => {
            log::warn!(
                "[MIGRATE] corrupt config backed up to {} — manual recovery recommended",
                backup.display()
            );
        }
        Err(e) => {
            log::warn!(
                "[MIGRATE] failed to back up corrupt config {}: {}",
                path.display(),
                e
            );
        }
    }
}

/// Returns Some(true) if `a` is newer than `b`, Some(false) if `b` is
/// newer, or None if either mtime is unreadable.
fn file_newer_than(a: &Path, b: &Path) -> Option<bool> {
    let ma = std::fs::metadata(a).ok()?.modified().ok()?;
    let mb = std::fs::metadata(b).ok()?.modified().ok()?;
    Some(ma > mb)
}
