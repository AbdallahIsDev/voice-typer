
use std::path::Path;

use crate::util;

#[derive(Debug)]
pub(crate) enum MergeOutcome {
    /// Whole file copied (target was absent).
    Copied,
    Merged(usize),
}

pub(crate) fn merge_config(old: &Path, new: &Path) -> Result<MergeOutcome, String> {
    if !new.exists() {
        // Atomic copy so an interrupted migration never leaves
        // a partially-written config.json at the target.
        util::atomic_copy(old, new)?;
        return Ok(MergeOutcome::Copied);
    }

    let old_txt = std::fs::read_to_string(old).map_err(|e| e.to_string())?;
    let new_txt = std::fs::read_to_string(new).map_err(|e| e.to_string())?;

    let old_val: serde_json::Value = match serde_json::from_str(&old_txt) {
        Ok(v) => v,
        Err(e) => {
            log::warn!(
                "[MIGRATE] old config.json parse failed (treating as empty): {}",
                e
            );
            backup_corrupt_config(old);
            serde_json::Value::Null
        }
    };
    let new_val: serde_json::Value = match serde_json::from_str(&new_txt) {
        Ok(v) => v,
        Err(e) => {
            log::warn!(
                "[MIGRATE] new config.json parse failed (treating as empty): {}",
                e
            );
            backup_corrupt_config(new);
            serde_json::Value::Null
        }
    };

    let old_obj: serde_json::Map<String, serde_json::Value> = match old_val {
        serde_json::Value::Object(o) => o,
        _ => return Ok(MergeOutcome::Merged(0)),
    };

    let old_newer = file_newer_than(old, new);

    let new_was_object = matches!(new_val, serde_json::Value::Object(_));
    let mut base = match new_val {
        serde_json::Value::Object(o) => o,
        _ => serde_json::Map::new(),
    };

    let mut written = 0usize;
    for (k, v) in old_obj {
        let take_old = match base.get(&k) {
            // Key present in target: ONE whole-file mtime comparison decides
            // every conflicting key (old wins only when strictly newer).
            Some(_) => old_newer == Some(true),
            // Key absent in target: always take old.
            None => true,
        };
        if take_old {
            base.insert(k, v);
            written += 1;
        }
    }

    if written == 0 && new_was_object {
        return Ok(MergeOutcome::Merged(0));
    }

    let merged = serde_json::Value::Object(base);
    let out = serde_json::to_string_pretty(&merged).map_err(|e| e.to_string())?;
    util::atomic_write_bytes(new, out.as_bytes())?;
    Ok(MergeOutcome::Merged(written))
}

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
                "[MIGRATE] corrupt config backed up to {}: manual recovery recommended",
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
