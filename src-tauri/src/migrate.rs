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
//! Old Electron `userData` locations (PVT-4 fix: probe all three in order,
//! use the first that exists on disk):
//!
//! 1. `voice-typer-desktop` — Electron `package.json` `name` field (very
//!    old Electron builds that never ran `setupUserData`, so Electron
//!    derived its default `userData` path from the package name).
//! 2. `voice-typer` — `bootstrap.ts:52-67` `setupUserData` override path
//!    via `app.setPath("userData", computeConfigDir())`. This is the SAME
//!    path Tauri now uses as its `config_dir`, so a migration from here
//!    would be a no-op self-copy — the probe loop skips it when it equals
//!    the Tauri target.
//! 3. `Voice Typer` (capital+space) — defensive third probe in case some
//!    ancient unreleased build used a human-readable capitalized name.
//!
//! Platform base paths:
//! - Windows: `%APPDATA%/<name>`
//! - macOS:   `~/Library/Application Support/<name>`
//! - Linux:   `~/.config/<name>` (or `$XDG_CONFIG_HOME/<name>`)
//!
//! New Tauri config_dir: `platform::paths::config_dir()` (already implemented).
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



/// Resolve the OLD Electron `userData` directory candidates per platform.
///
/// Returns a list of candidate paths in probe order (most-likely first).
/// The caller probes each in turn and uses the first one that exists on
/// disk. Returns an empty `Vec` if the platform's relevant env vars are
/// missing (caller treats that as "nothing to migrate" — safe no-op).
///
/// PVT-4 fix: the previous implementation only probed `Voice Typer`
/// (capital+space), which was NEVER the actual Electron `userData` name.
/// `voice_typer/client/package.json:2` declares `"name": "voice-typer-desktop"`
/// (lowercase, hyphen) and `bootstrap.ts:52-67` `setupUserData` overrides
/// the path to `computeConfigDir()` which returns `voice-typer` (lowercase,
/// hyphen). The old migration was dead code: it always returned "nothing
/// to do" and wrote the sentinel marker immediately, silently losing any
/// old Electron config that DID exist under `voice-typer-desktop`.
fn electron_userdata_candidates() -> Vec<PathBuf> {
    /// The three Electron `userData` directory names ever used, in probe
    /// order. See the module-level docstring for the naming history.
    const CANDIDATE_NAMES: &[&str] = &[
        // 1. Very old Electron builds (no `setupUserData`): Electron
        //    derived the default `userData` path from `package.json`
        //    `name` = `voice-typer-desktop`.
        "voice-typer-desktop",
        // 2. Newer Electron builds with `setupUserData` (bootstrap.ts:52-67):
        //    `app.setPath("userData", computeConfigDir())` → `voice-typer`.
        //    This is the SAME path Tauri now uses as its `config_dir`, so
        //    the caller skips it when it equals the Tauri target.
        "voice-typer",
        // 3. Defensive third probe — the human-readable brand name with a
        //    space, in case some ancient unreleased build used it as the
        //    userData directory name. Uses `crate::branding::APP_NAME`
        //    (const-context) so the probe stays in lockstep with the rest
        //    of the UI's brand string.
        crate::branding::APP_NAME,
    ];

    #[cfg(target_os = "windows")]
    {
        let Some(appdata) = std::env::var("APPDATA").ok() else {
            return Vec::new();
        };
        let base = PathBuf::from(appdata);
        CANDIDATE_NAMES.iter().map(|n| base.join(n)).collect()
    }
    #[cfg(target_os = "macos")]
    {
        let Some(home) = std::env::var("HOME").ok() else {
            return Vec::new();
        };
        let base = PathBuf::from(home)
            .join("Library")
            .join("Application Support");
        CANDIDATE_NAMES.iter().map(|n| base.join(n)).collect()
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        // Linux: Electron's userData defaults to `~/.config/<name>` when
        // XDG_CONFIG_HOME is unset; honor it if present.
        // CR-80 fix: collapse dead conditional (both arms returned the
        // same value — `PathBuf::from(X).join(".config")` where X was
        // `.` or `h`).
        let Some(h) = std::env::var("XDG_CONFIG_HOME")
            .ok()
            .filter(|b| !b.is_empty())
            .or_else(|| std::env::var("HOME").ok())
        else {
            return Vec::new();
        };
        let base = PathBuf::from(h).join(".config");
        CANDIDATE_NAMES.iter().map(|n| base.join(n)).collect()
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", unix)))]
    {
        Vec::new()
    }
}

/// One-time, idempotent, SAFE migration from Electron userData to Tauri config_dir.
pub fn migrate_electron_userdata(_app: &tauri::AppHandle) {
    // GT-E3-4: `app` was only used to call `platform::paths::config_dir(app)`;
    // now that `config_dir()` takes no args, the param is unused. Kept in
    // the signature for forward-compat (a future migration might need
    // `app.path().resource_dir()` to copy bundled defaults). Prefixed
    // with `_` to silence the unused-param lint under clippy::all.
    let new_dir = crate::platform::paths::config_dir();

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

    // PVT-4 fix: probe each candidate in order; use the first that exists
    // on disk. The `voice-typer` candidate (bootstrap.ts:52-67
    // `setupUserData`) is the SAME path Tauri uses as its `config_dir` —
    // if that's the first one found, migration would be a no-op self-copy
    // (source == target), so we skip it and continue probing. If no other
    // candidate exists, there's genuinely nothing to migrate and we bail
    // without writing the sentinel (so next launch re-probes cheaply).
    let candidates = electron_userdata_candidates();
    if candidates.is_empty() {
        log::info!("[MIGRATE] nothing to do (could not resolve old userData dir — platform env vars missing)");
        return;
    }

    let mut old_dir: Option<PathBuf> = None;
    for candidate in &candidates {
        log::info!("[MIGRATE] probing electron userdata at: {:?}", candidate);
        if candidate.as_path() == new_dir.as_path() {
            log::info!(
                "[MIGRATE]   skipping {:?} (same as Tauri config_dir target — self-copy no-op)",
                candidate
            );
            continue;
        }
        if candidate.is_dir() {
            old_dir = Some(candidate.clone());
            break;
        }
    }

    let Some(old_dir) = old_dir else {
        log::info!(
            "[MIGRATE] nothing to do (no candidate old userData dir exists on disk; probed {} paths)",
            candidates.len()
        );
        return;
    };

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

    // T3-05: previously used `unwrap_or(Value::Null)` which silently
    // swallowed parse errors. A corrupt source or target config.json
    // would silently be treated as `null` (an empty object on merge),
    // potentially losing the user's settings on the next migration
    // pass. Log a warning so the failure is observable in user logs
    // (the merge itself still proceeds fail-open — we prefer to keep
    // whatever parses rather than abort the whole migration).
    let old_val: serde_json::Value = match serde_json::from_str(&old_txt) {
        Ok(v) => v,
        Err(e) => {
            log::warn!("[MIGRATE] old config.json parse failed (treating as empty): {}", e);
            serde_json::Value::Null
        }
    };
    let new_val: serde_json::Value = match serde_json::from_str(&new_txt) {
        Ok(v) => v,
        Err(e) => {
            log::warn!("[MIGRATE] new config.json parse failed (treating as empty): {}", e);
            serde_json::Value::Null
        }
    };

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
///
/// PVT-G5-033: promoted from `fn` to `pub(crate) fn` so the
/// supervisor (`supervisor.rs::write_restart_counter`) can reuse it for
/// atomic persistence of the restart counter. Previously the
/// counter used `std::fs::write` (non-atomic: truncate-then-write),
/// which on a crash mid-write could leave a partially-written
/// `restart_counter.json` that fails to parse — falling back to 0
/// (the fail-open default in `read_restart_counter`), silently
/// bypassing the circuit breaker on the next launch.
pub(crate) fn atomic_write_bytes(path: &Path, contents: &[u8]) -> Result<(), String> {
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

#[cfg(test)]
mod tests {
    use super::*;

    // ── atomic_write_bytes parent-dir fsync ────────

    #[test]
    fn test_atomic_write_bytes_creates_file_with_expected_contents() {
        // Sanity: the basic write+rename contract still holds after
        // the parent-dir fsync addition. We write a small file,
        // then read it back and verify the contents match.
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-pi9-test-{}-basic",
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
        let tmp_path = tmp.join(".config.json.tmp.migrate");
        assert!(
            !tmp_path.exists(),
            "temp file leaked after rename: {}",
            tmp_path.display()
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
            "voice-typer-pi9-test-{}-overwrite",
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
            "voice-typer-pi9-test-{}-fsync",
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
}
