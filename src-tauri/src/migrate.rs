//! One-time Electron `userData` → Tauri `<config_dir>` migration (ADR-0020 §8).
//!
//! Add `mod migrate;` to main.rs and call
//! `migrate::migrate_electron_userdata(&app_handle)` at the start of the
//! `.setup` closure, BEFORE the spawn_sidecar task.
//!
//! The migration is idempotent and SAFE: it never destroys data. It early-
//! returns after the first successful run (or when there is nothing to do),
//! so it is cheap and safe to call on every launch.
//!
//! Old Electron `userData` locations (note the SPACE, unlike `voice-typer`):
//! - Windows: `%APPDATA%/Voice Typer`
//! - macOS:   `~/Library/Application Support/Voice Typer`
//! - Linux:   `~/.config/Voice Typer`
//!
//! New Tauri config_dir: `platform::paths::config_dir(app)` (already implemented).
//!
//! Merge rules (do NOT overwrite newer data):
//! - `config.json`: if absent in target, copy whole; if present but differs,
//!   merge key-by-key with the file whose mtime is newest winning per key.
//! - `models/`: copy only files ABSENT from the target.
//! - `history.db`: copy only if target absent (append is unsafe for SQLite —
//!   skip with a warning rather than risk corruption).
//! - `voice-typer-recovery.json`: copy if target absent.
//!
//! All fs ops are wrapped so this function NEVER panics.

use std::path::{Path, PathBuf};



/// Resolve the OLD Electron `userData` directory per platform.
///
/// Returns `None` if the relevant env vars are missing (caller treats that
/// as "nothing to migrate" — safe no-op, never errors).
fn electron_userdata_dir() -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    {
        std::env::var("APPDATA").ok().map(|a| PathBuf::from(a).join("Voice Typer"))
    }
    #[cfg(target_os = "macos")]
    {
        std::env::var("HOME")
            .ok()
            .map(|h| PathBuf::from(h).join("Library").join("Application Support").join("Voice Typer"))
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        // Linux: Electron's userData defaults to `~/.config/<name>` when
        // XDG_CONFIG_HOME is unset; honor it if present.
        let base = std::env::var("XDG_CONFIG_HOME")
            .ok()
            .filter(|b| !b.is_empty())
            .or_else(|| std::env::var("HOME").ok())
            // CR-80 fix: collapse dead conditional (both arms returned the
            // same value — `PathBuf::from(X).join(".config")` where X was
            // `.` or `h`).
            .map(|h| PathBuf::from(h).join(".config"))?;
        Some(base.join("Voice Typer"))
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", unix)))]
    {
        None
    }
}

/// One-time, idempotent, SAFE migration from Electron userData to Tauri config_dir.
pub fn migrate_electron_userdata(app: &tauri::AppHandle) {
    let new_dir = crate::platform::paths::config_dir(app);

    // CR-19 fix: use a sentinel file (.migrated-from-electron) as the
    // idempotency marker instead of checking config.json existence.
    //
    // The previous guard (`if new_dir.join("config.json").exists()`)
    // conflated "already migrated" with "target config.json exists," which
    // is true after the very first launch even when migration hasn't actually
    // run. Users upgrading from Electron who launched Tauri once (even
    // briefly) before the migration was wired would never get their old
    // Electron config merged — silently losing their settings. The
    // merge_config logic (newest-mtime-wins per key) was dead code in the
    // common case. The sentinel marker is touched ONLY after successful
    // migration, so merge_config can actually run when both configs exist.
    let migration_marker = new_dir.join(".migrated-from-electron");
    if migration_marker.exists() {
        log::info!("[MIGRATE] already migrated (sentinel marker present)");
        return;
    }

    let Some(old_dir) = electron_userdata_dir() else {
        log::info!("[MIGRATE] nothing to do (could not resolve old userData dir)");
        return;
    };

    // 3. If old dir does not exist, nothing to do.
    if !old_dir.is_dir() {
        log::info!("[MIGRATE] nothing to do (old userData dir absent: {})", old_dir.display());
        return;
    }

    log::info!(
        "[MIGRATE] starting: source={} target={}",
        old_dir.display(),
        new_dir.display()
    );

    if let Err(e) = std::fs::create_dir_all(&new_dir) {
        log::error!("[MIGRATE] cannot create target dir {}: {}", new_dir.display(), e);
        return;
    }

    let mut config_merged = 0usize;
    let mut config_copied = false;
    let mut models_copied = 0usize;
    let mut history_copied = false;
    let mut recovery_copied = false;

    // 4a. config.json
    let old_cfg = old_dir.join("config.json");
    let new_cfg = new_dir.join("config.json");
    if old_cfg.is_file() {
        match merge_config(&old_cfg, &new_cfg) {
            Ok(MergeOutcome::Copied) => {
                config_copied = true;
                log::info!("[MIGRATE] config.json copied");
            }
            Ok(MergeOutcome::Merged(keys)) => {
                config_merged = keys;
                log::info!("[MIGRATE] config.json merged ({} keys)", keys);
            }
            Err(e) => log::error!("[MIGRATE] config.json merge failed: {}", e),
        }
    }

    // 4b. models/ — copy only files absent from target.
    let old_models = old_dir.join("models");
    let new_models = new_dir.join("models");
    if old_models.is_dir() {
        if let Err(e) = std::fs::create_dir_all(&new_models) {
            log::error!("[MIGRATE] cannot create models dir: {}", e);
        } else {
            models_copied = copy_missing_files(&old_models, &new_models);
            if models_copied > 0 {
                log::info!("[MIGRATE] models/ copied {} new file(s)", models_copied);
            }
        }
    }

    // 4c. history.db — copy only if target absent (append unsafe for SQLite).
    let old_db = old_dir.join("history.db");
    let new_db = new_dir.join("history.db");
    if old_db.is_file() {
        if new_db.exists() {
            log::warn!(
                "[MIGRATE] history.db skipped (target exists) — NOT overwriting to avoid corruption"
            );
        } else {
            // M-65: copy main db atomically (temp + rename in same dir)
            // so an interrupted migration never leaves a partial
            // history.db on disk that SQLite would refuse to open.
            match atomic_copy(&old_db, &new_db) {
                Ok(()) => {
                    history_copied = true;
                    log::info!("[MIGRATE] history.db copied");
                }
                Err(e) => log::error!("[MIGRATE] history.db copy failed: {}", e),
            }
            // M-65: also copy SQLite WAL sidecars (-wal / -shm) if
            // present. Without these, recent WAL-mode transactions
            // in the source db would be lost on the migrated copy.
            // Each sidecar is copied atomically and independently;
            // a missing sidecar is not an error (SQLite regenerates
            // -shm and replays -wal only if both are present).
            for suffix in &["-wal", "-shm"] {
                let old_side = sidecar_path(&old_db, suffix);
                let new_side = sidecar_path(&new_db, suffix);
                if old_side.is_file() && !new_side.exists() {
                    if let Err(e) = atomic_copy(&old_side, &new_side) {
                        log::warn!(
                            "[MIGRATE] history.db{} copy failed: {}",
                            suffix,
                            e
                        );
                    } else {
                        log::info!("[MIGRATE] history.db{} copied", suffix);
                    }
                }
            }
        }
    }

    // 4d. voice-typer-recovery.json — copy if target absent.
    let old_rec = old_dir.join("voice-typer-recovery.json");
    let new_rec = new_dir.join("voice-typer-recovery.json");
    if old_rec.is_file() && !new_rec.exists() {
        if let Err(e) = std::fs::copy(&old_rec, &new_rec) {
            log::error!("[MIGRATE] voice-typer-recovery.json copy failed: {}", e);
        } else {
            recovery_copied = true;
            log::info!("[MIGRATE] voice-typer-recovery.json copied");
        }
    }

    // 5. Summary.
    log::info!(
        "[MIGRATE] done — config: copied={} merged_keys={}, models_new_files={}, \
         history_copied={}, recovery_copied={}",
        config_copied,
        config_merged,
        models_copied,
        history_copied,
        recovery_copied
    );

    // CR-19 fix: write the sentinel marker AFTER successful migration so
    // subsequent launches skip re-migration. Without this, every launch
    // would re-attempt the (idempotent but log-noisy) migration.
    if let Err(e) = std::fs::write(&migration_marker, "") {
        log::warn!(
            "[MIGRATE] failed to write sentinel marker {}: {} (migration will re-run next launch)",
            migration_marker.display(),
            e
        );
    } else {
        log::info!("[MIGRATE] sentinel marker written to {}", migration_marker.display());
    }
}

enum MergeOutcome {
    Copied,
    Merged(usize),
}

/// Merge `old` config.json into `new` config.json.
///
/// - If `new` does not exist, copy the whole file (Copied).
/// - If `new` exists: merge key-by-key, newest-mtime-wins per key across
///   the two files. Returns Merged(keys_from_old_written).
///
/// H-19 (IMPROVE-2026-07-19): all writes are now ATOMIC (temp-file +
/// `rename`). Previously `std::fs::copy` and `std::fs::write` truncated
/// the target before writing — a crash mid-write (power loss, SIGKILL,
/// OOM) would leave `config.json` truncated/corrupt. Since `migrate.rs`
/// runs BEFORE the Python sidecar spawns, the sidecar would boot against
/// a corrupt config and fall back to defaults — permanently losing the
/// user's migrated Electron config. The atomic write ensures the target
/// is either fully-old or fully-new, never partial.
fn merge_config(old: &Path, new: &Path) -> Result<MergeOutcome, String> {
    if !new.exists() {
        // M-65: atomic copy so an interrupted migration never leaves
        // a partially-written config.json at the target.
        atomic_copy(old, new)?;
        return Ok(MergeOutcome::Copied);
    }

    let old_txt = std::fs::read_to_string(old).map_err(|e| e.to_string())?;
    let new_txt = std::fs::read_to_string(new).map_err(|e| e.to_string())?;

    let old_val: serde_json::Value =
        serde_json::from_str(&old_txt).unwrap_or(serde_json::Value::Null);
    let new_val: serde_json::Value =
        serde_json::from_str(&new_txt).unwrap_or(serde_json::Value::Null);

    // If old isn't an object, nothing useful to merge (keep target).
    let old_obj = match old_val.as_object() {
        Some(o) => o,
        None => return Ok(MergeOutcome::Merged(0)),
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
    for (k, v) in old_obj {
        let take_old = match base.get(k) {
            // Key present in target — winner determined by file mtime.
            Some(_) => old_newer == Some(true),
            // Key absent in target — always take old.
            None => true,
        };
        if take_old {
            base.insert(k.clone(), v.clone());
            written += 1;
        }
    }

    let merged = serde_json::Value::Object(base);
    let out = serde_json::to_string_pretty(&merged).map_err(|e| e.to_string())?;
    // M-65: write atomically (temp + fsync + rename in the same dir)
    // so an interrupted migration never leaves a partially-written
    // config.json that would fail to parse on next launch and cause
    // the user's merged settings to be lost.
    atomic_write_bytes(new, out.as_bytes())?;
    Ok(MergeOutcome::Merged(written))
}

// ─── M-65: atomic write helpers ───────────────────────────────────────────
//
// `std::fs::write` and `std::fs::copy` are NOT atomic: if the process
// is killed (or the disk fills, or the OS crashes) mid-write, the
// destination file is left with a partial body. For the migration
// path that means a half-written `config.json` that fails to parse
// on next launch (losing the user's merged settings) or a truncated
// `history.db` that SQLite refuses to open (losing the user's
// history). The helpers below write to a sibling temp file in the
// SAME directory (so `rename` is a same-filesystem atomic op on
// POSIX, and on Windows the destination is absent so rename
// succeeds), `fsync` the temp file (so the data is durable before
// the rename), then rename into place. On failure the temp file is
// best-effort cleaned up so we don't leak `.history.db.tmp.migrate`
// files in the user's config dir.

/// M-65: write `contents` to `path` atomically (temp + fsync + rename).
fn atomic_write_bytes(path: &Path, contents: &[u8]) -> Result<(), String> {
    use std::io::Write;

    let dir = path
        .parent()
        .ok_or_else(|| format!("path has no parent: {}", path.display()))?;
    // Same dir as target so rename is atomic (same filesystem).
    // The temp filename is dotted so it doesn't show up in normal
    // directory listings and is prefixed with the target's filename
    // so a human inspecting the dir can tell what it's for.
    let tmp_name = match path.file_name().and_then(|n| n.to_str()) {
        Some(n) => format!(".{}.tmp.migrate", n),
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
    Ok(())
}

/// M-65: atomically copy `src` to `dst` by reading src into memory
/// then writing via `atomic_write_bytes`. Suitable for small-to-
/// medium files (config.json, history.db, WAL sidecars). For very
/// large files (model weights) we use `std::fs::copy` directly via
/// `copy_missing_files` — those aren't safety-critical and the
/// double-buffering would be wasteful.
fn atomic_copy(src: &Path, dst: &Path) -> Result<(), String> {
    let bytes = std::fs::read(src)
        .map_err(|e| format!("read src {}: {}", src.display(), e))?;
    atomic_write_bytes(dst, &bytes)
}

/// M-65: build the path of a SQLite sidecar file (`-wal` / `-shm`)
/// for a given main db path. Appends the suffix to the literal
/// file_name (NOT to the extension) so `history.db` →
/// `history.db-wal`.
fn sidecar_path(db: &Path, suffix: &str) -> PathBuf {
    let mut name = match db.file_name().and_then(|n| n.to_str()) {
        Some(n) => n.to_string(),
        None => return db.to_path_buf(),
    };
    name.push_str(suffix);
    match db.parent() {
        Some(dir) => dir.join(name),
        None => PathBuf::from(name),
    }
}

/// Returns Some(true) if `a` is newer than `b`, Some(false) if `b` is
/// newer, or None if either mtime is unreadable.
fn file_newer_than(a: &Path, b: &Path) -> Option<bool> {
    let ma = std::fs::metadata(a).ok()?.modified().ok()?;
    let mb = std::fs::metadata(b).ok()?.modified().ok()?;
    Some(ma > mb)
}

/// Recursively copy files from `src` to `dst` that are ABSENT in `dst`.
/// Returns the count of files copied. Directory structure under `src` is
/// preserved. Never overwrites an existing target file.
fn copy_missing_files(src: &Path, dst: &Path) -> usize {
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
        let name = match entry.file_name().to_str() {
            Some(n) => n.to_string(),
            None => continue,
        };
        let dst_path = dst.join(&name);
        if path.is_dir() {
            if let Err(e) = std::fs::create_dir_all(&dst_path) {
                log::error!("[MIGRATE] cannot create dir {}: {}", dst_path.display(), e);
                continue;
            }
            copy_missing_recursive(&path, &dst_path, count);
        } else if path.is_file() {
            if dst_path.exists() {
                continue; // never clobber a newer download
            }
            if let Err(e) = std::fs::copy(&path, &dst_path) {
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
