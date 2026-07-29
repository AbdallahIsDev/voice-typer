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
//!   merge key-by-key. The entire newer file's values win for overlapping
//!   keys (single whole-file mtime comparison — NOT per-key mtime; see
//!   XZ-R12-11 fix on `merge_config`).
//! - `models/`: copy only files ABSENT from the target (XZ-R4-013: symlinks
//!   are skipped; XZ-R12-04: copy is atomic via temp+rename).
//! - `history.db`: copy only if target absent (append is unsafe for SQLite —
//!   skip with a warning rather than risk corruption). WAL/SHM sidecars
//!   copied atomically; if either fails, target sidecars are deleted so
//!   SQLite starts fresh (XZ-R12-17).
//! - `voice-typer-recovery.json`: copy if target absent.
//!
//! All fs ops are wrapped so this function NEVER panics.
//!
//! XZ-R12-03: the sentinel marker is written ONLY when ALL critical
//! migration steps succeeded. If any step fails (config merge, history.db
//! copy, recovery.json copy, models dir creation), the sentinel is skipped
//! so the next launch re-attempts the migration (idempotent ops).

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
    // XZ-R12-03: track critical-step failures so the sentinel marker
    // is only written when ALL critical migration steps succeeded.
    // Pre-fix, the sentinel was written UNCONDITIONALLY — if config,
    // history.db, or recovery.json migration failed, the user's data
    // was silently dropped (next launch saw the sentinel and skipped
    // re-migration). Models dir creation failure also counts as
    // critical (without the target dir we can't copy anything).
    let mut migration_failed: usize = 0;

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
            Err(e) => {
                log::error!("[MIGRATE] config.json merge failed: {}", e);
                migration_failed += 1;
            }
        }
    }

    // 4b. models/ — copy only files absent from target.
    let old_models = old_dir.join("models");
    let new_models = new_dir.join("models");
    if old_models.is_dir() {
        if let Err(e) = std::fs::create_dir_all(&new_models) {
            log::error!("[MIGRATE] cannot create models dir: {}", e);
            migration_failed += 1;
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
                Err(e) => {
                    log::error!("[MIGRATE] history.db copy failed: {}", e);
                    migration_failed += 1;
                }
            }
            // M-65: also copy SQLite WAL sidecars (-wal / -shm) if
            // present. Without these, recent WAL-mode transactions
            // in the source db would be lost on the migrated copy.
            // Each sidecar is copied atomically and independently;
            // a missing sidecar is not an error (SQLite regenerates
            // -shm and replays -wal only if both are present).
            //
            // XZ-R12-17: if EITHER sidecar copy fails, delete all
            // target sidecars so SQLite starts fresh on next open.
            // Without this, the target db could end up with `history.db`
            // plus a partial `-wal` (no `-shm`), which SQLite refuses
            // to open — losing the entire migrated history, not just
            // the WAL transactions.
            let mut sidecar_failed = false;
            for suffix in &["-wal", "-shm"] {
                let old_side = sidecar_path(&old_db, suffix);
                let new_side = sidecar_path(&new_db, suffix);
                if old_side.is_file() && !new_side.exists() {
                    if let Err(e) = atomic_copy(&old_side, &new_side) {
                        log::warn!(
                            "[MIGRATE] history.db{} copy failed: {} — will delete target sidecars",
                            suffix,
                            e
                        );
                        sidecar_failed = true;
                    } else {
                        log::info!("[MIGRATE] history.db{} copied", suffix);
                    }
                }
            }
            if sidecar_failed {
                for suffix in &["-wal", "-shm"] {
                    let new_side = sidecar_path(&new_db, suffix);
                    if new_side.exists() {
                        if let Err(e) = std::fs::remove_file(&new_side) {
                            log::warn!(
                                "[MIGRATE] failed to delete partial target sidecar {}: {}",
                                new_side.display(),
                                e
                            );
                        }
                    }
                }
                log::warn!(
                    "[MIGRATE] history.db WAL sidecar migration incomplete — \
                     WAL transactions from source may be lost; SQLite will start fresh"
                );
            }
        }
    }

    // 4d. voice-typer-recovery.json — copy if target absent.
    let old_rec = old_dir.join("voice-typer-recovery.json");
    let new_rec = new_dir.join("voice-typer-recovery.json");
    if old_rec.is_file() && !new_rec.exists() {
        // XZ-R12-03: use atomic_copy for recovery.json too (was
        // std::fs::copy). A partial recovery.json would load as
        // invalid JSON on next launch and the user's recovery
        // snapshot would be silently dropped — same data-loss
        // risk as the config.json case. The recovery file is
        // small (a few KB), so atomic_copy's read-into-memory is
        // fine here.
        if let Err(e) = atomic_copy(&old_rec, &new_rec) {
            log::error!("[MIGRATE] voice-typer-recovery.json copy failed: {}", e);
            migration_failed += 1;
        } else {
            recovery_copied = true;
            log::info!("[MIGRATE] voice-typer-recovery.json copied");
        }
    }

    // 5. Summary.
    log::info!(
        "[MIGRATE] done — config: copied={} merged_keys={}, models_new_files={}, \
         history_copied={}, recovery_copied={}, failures={}",
        config_copied,
        config_merged,
        models_copied,
        history_copied,
        recovery_copied,
        migration_failed
    );

    // CR-19 fix: write the sentinel marker AFTER successful migration so
    // subsequent launches skip re-migration. Without this, every launch
    // would re-attempt the (idempotent but log-noisy) migration.
    //
    // XZ-R12-03: only write the sentinel if ALL critical migration
    // steps succeeded. If any failed, skip the sentinel so the next
    // launch re-attempts the migration (the operations are idempotent
    // — atomic_copy uses temp+rename, merge_config is key-by-key).
    // This prevents silently losing the user's config / history.db /
    // recovery.json when a step fails.
    //
    // The logic is extracted into `write_sentinel_if_clean` so it is
    // unit-testable without a Tauri AppHandle (the entry-point function
    // requires one and is hard to construct in `#[cfg(test)]`).
    let _ = write_sentinel_if_clean(&new_dir, migration_failed);
}

/// XZ-R12-03: write the `.migrated-from-electron` sentinel marker to
/// `new_dir` ONLY if `migration_failed == 0`.
///
/// Returns `true` if the sentinel was written (or already present from
/// a prior run — treated as success since the caller already
/// early-returns on an existing sentinel). Returns `false` if the
/// sentinel was deliberately skipped because a critical step failed
/// (so the next launch will re-attempt the migration), or if writing
/// the sentinel itself failed (transient I/O error — also retried on
/// next launch).
///
/// Extracted from `migrate_electron_userdata` so the gating logic is
/// unit-testable. The behavior we pin:
///
/// - `migration_failed > 0` → sentinel NOT written, returns `false`.
/// - `migration_failed == 0` → sentinel written, returns `true`
///   (unless `std::fs::write` errors — returns `false` and logs).
///
/// The pre-fix code wrote the sentinel UNCONDITIONALLY after every
/// migration attempt — even when `config.json` merge, `history.db`
/// copy, or `recovery.json` copy had failed. Next launch saw the
/// sentinel and skipped re-migration, silently dropping the user's
/// data. This helper enforces the gate as a single decision point.
fn write_sentinel_if_clean(new_dir: &Path, migration_failed: usize) -> bool {
    if migration_failed > 0 {
        log::warn!(
            "[MIGRATE] {} critical step(s) failed — NOT writing sentinel marker; \
             migration will re-attempt on next launch",
            migration_failed
        );
        return false;
    }
    let migration_marker = new_dir.join(".migrated-from-electron");
    match std::fs::write(&migration_marker, "") {
        Ok(()) => {
            log::info!(
                "[MIGRATE] sentinel marker written to {}",
                migration_marker.display()
            );
            true
        }
        Err(e) => {
            log::warn!(
                "[MIGRATE] failed to write sentinel marker {}: {} (migration will re-run next launch)",
                migration_marker.display(),
                e
            );
            false
        }
    }
}

enum MergeOutcome {
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
/// XZ-R12-11: the previous docstring promised "newest-mtime-wins per
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
    //
    // XZ-R12-12: before treating a corrupt source/target as Null,
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

// Re-export for backward compat: `sidecar/supervisor.rs` still imports
// `atomic_write_bytes` from `crate::migrate`. The implementation has
// been moved to `crate::util` (it's a generic fs-write helper with no
// migration-specific logic). Once `supervisor.rs` is updated to import
// from `crate::util` directly, this re-export can be removed.
pub(crate) use crate::util::atomic_write_bytes;

/// M-65: atomically copy `src` to `dst` by reading src into memory
/// then writing via `atomic_write_bytes`. Suitable for small-to-
/// medium files (config.json, history.db, WAL sidecars). For very
/// large files (model weights) use `atomic_copy_file` instead — it
/// streams via `std::fs::copy` to a sibling temp file then renames,
/// avoiding the memory doubling that `atomic_copy`'s read-into-memory
/// would impose on multi-GB model weights.
fn atomic_copy(src: &Path, dst: &Path) -> Result<(), String> {
    let bytes = std::fs::read(src)
        .map_err(|e| format!("read src {}: {}", src.display(), e))?;
    atomic_write_bytes(dst, &bytes)
}

/// XZ-R12-04: atomically copy a (potentially large) file from `src` to
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
fn atomic_copy_file(src: &Path, dst: &Path) -> Result<(), String> {
    use rand::RngCore;

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
        return Err(format!(
            "copy {} → {}: {}",
            src.display(),
            tmp.display(),
            e
        ));
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
            log::warn!("[MIGRATE] fsync of tmp {} failed (non-fatal): {}", tmp.display(), e);
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

/// XZ-R12-12: back up a corrupt config.json before the migration
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

/// M-65: build the path of a SQLite sidecar file (`-wal` / `-shm`)
/// for a given main db path. Appends the suffix to the literal
/// file_name (NOT to the extension) so `history.db` →
/// `history.db-wal`.
fn sidecar_path(db: &Path, suffix: &str) -> PathBuf {
    // ER-66: `file_name()` returns `Option<&OsStr>`; `to_str()` borrows
    // as `&str` and then `.to_string()` allocates a new String from
    // the borrow. Using `to_os_string()` (which copies the OsStr into
    // a new OsString) then `into_string()` (which moves the OsString's
    // inner buffer into a String) avoids the second allocation on
    // valid-UTF-8 file names. On non-UTF-8 names we fall through to
    // the prior `db.to_path_buf()` fallback (same behavior).
    let mut name = match db
        .file_name()
        .map(|n| n.to_os_string().into_string())
    {
        Some(Ok(n)) => n,
        Some(Err(_)) | None => return db.to_path_buf(),
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
        // XZ-R4-013: use `symlink_metadata` (NOT `metadata`) so we can
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
        // ER-66: `entry.file_name().into_string()` consumes the OsString
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
            // XZ-R12-04: use atomic copy (temp + rename in same dir)
            // so an interrupted migration never leaves a partial model
            // file at the destination. Pre-fix, `std::fs::copy` truncated
            // then wrote — combined with the `if dst_path.exists() { continue; }`
            // guard above, a partial file from a killed migration looked
            // "existing" on next launch and was skipped, leaving a
            // corrupt model file in the target. The atomic copy writes
            // to a sibling temp file then renames, so the destination
            // is either fully-present or fully-absent — never partial.
            if let Err(e) = atomic_copy_file(&path, &dst_path) {
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
    use std::sync::atomic::{AtomicU64, Ordering};

    /// Best-effort scratch directory — unique per test invocation so
    /// parallel `cargo test` threads don't race on the same path. The
    /// directory is created fresh and removed on drop (best-effort —
    /// if removal fails we leak a temp dir, which is harmless in tests).
    struct ScratchDir(PathBuf);
    impl ScratchDir {
        fn new(label: &str) -> Self {
            static COUNTER: AtomicU64 = AtomicU64::new(0);
            let n = COUNTER.fetch_add(1, Ordering::SeqCst);
            let mut p = std::env::temp_dir();
            p.push(format!(
                "vt-migrate-test-{}-{}-{}-{}",
                label,
                std::process::id(),
                n,
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_nanos())
                    .unwrap_or(0)
            ));
            std::fs::create_dir_all(&p).expect("create scratch dir");
            ScratchDir(p)
        }
        fn path(&self) -> &Path {
            &self.0
        }
    }
    impl Drop for ScratchDir {
        fn drop(&mut self) {
            // Best-effort recursive remove — ignore errors (test runner
            // may have left files open on Windows).
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    /// XZ-R4-013: a symlink in the source `models/` dir must NOT be
    /// followed — neither the link itself nor its target is copied.
    /// Pre-fix, `path.is_file()` followed the symlink and `std::fs::copy`
    /// happily copied whatever the link pointed at (potentially
    /// `~/.ssh/id_rsa`).
    #[test]
    fn copy_missing_recursive_skips_symlinks() {
        let _scratch = ScratchDir::new("symlink-skip");
        let root = _scratch.path().to_path_buf();
        let src = root.join("src");
        let dst = root.join("dst");
        std::fs::create_dir_all(&src).unwrap();
        std::fs::create_dir_all(&dst).unwrap();

        // Regular file — should be copied.
        std::fs::write(src.join("real.bin"), b"model-weights").unwrap();
        // Symlink pointing OUTSIDE the source dir (the exfil scenario).
        // Create the target file first so the symlink resolves.
        let outside = root.join("secret.txt");
        std::fs::write(&outside, b"PRIVATE").unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink(&outside, src.join("evil.bin")).unwrap();

        let count = copy_missing_files(&src, &dst);
        // Only `real.bin` should have been copied — the symlink is skipped.
        assert_eq!(count, 1, "only the regular file should be copied");
        assert!(dst.join("real.bin").is_file());
        // The symlink target's contents must NOT appear under the dst.
        assert!(
            !dst.join("evil.bin").exists(),
            "symlink must NOT be copied or followed"
        );
        // Sanity: the original secret file is untouched.
        assert_eq!(std::fs::read_to_string(&outside).unwrap(), "PRIVATE");
    }

    /// XZ-R12-04: `atomic_copy_file` must produce a destination whose
    /// bytes match the source exactly, and must NOT leave a temp file
    /// behind on success.
    #[test]
    fn atomic_copy_file_produces_identical_destination() {
        let _scratch = ScratchDir::new("atomic-copy");
        let root = _scratch.path().to_path_buf();
        let src = root.join("src.bin");
        let dst = root.join("dst.bin");
        // 8 KiB of pseudo-random bytes (deterministic seed so the test
        // is reproducible).
        let mut data = Vec::with_capacity(8 * 1024);
        let mut state: u32 = 0x1234_5678;
        for _ in 0..(8 * 1024) {
            // xorshift32 — fast, deterministic, no extra deps.
            state ^= state << 13;
            state ^= state >> 17;
            state ^= state << 5;
            data.push((state & 0xFF) as u8);
        }
        std::fs::write(&src, &data).unwrap();
        atomic_copy_file(&src, &dst).expect("atomic copy should succeed");
        let got = std::fs::read(&dst).unwrap();
        assert_eq!(got, data, "destination bytes must match source");
        // No `.tmp.copy.*` files left behind on success.
        let leftover: Vec<_> = std::fs::read_dir(&root)
            .unwrap()
            .flatten()
            .filter(|e| {
                e.file_name()
                    .to_string_lossy()
                    .contains(".tmp.copy.")
            })
            .collect();
        assert!(leftover.is_empty(), "no temp files should remain: {:?}", leftover);
    }

    /// XZ-R12-04: `atomic_copy_file` must NOT leave a partial destination
    /// if the source doesn't exist (the copy fails before the rename).
    #[test]
    fn atomic_copy_file_no_partial_on_missing_source() {
        let _scratch = ScratchDir::new("atomic-copy-missing");
        let root = _scratch.path().to_path_buf();
        let src = root.join("nonexistent.bin");
        let dst = root.join("dst.bin");
        let err = atomic_copy_file(&src, &dst)
            .expect_err("missing source must produce an error");
        assert!(err.contains("copy"), "error should mention copy: {}", err);
        assert!(!dst.exists(), "destination must NOT exist after failure");
    }

    /// XZ-R12-12: when `merge_config` encounters a corrupt source
    /// config.json (invalid JSON), it must back up the corrupt file
    /// to `<name>.corrupt-pre-migration.<ts>.bak` BEFORE treating it
    /// as Null. Without the backup, the user's old Electron settings
    /// would be silently dropped on the next migration pass.
    #[test]
    fn merge_config_backs_up_corrupt_source() {
        let _scratch = ScratchDir::new("corrupt-backup");
        let root = _scratch.path().to_path_buf();
        let old = root.join("config.json");
        let new = root.join("config.new.json");
        // Source is corrupt JSON.
        std::fs::write(&old, b"{not valid json").unwrap();
        // Target must exist for the merge (parse-failure) path to run.
        std::fs::write(&new, b"{\"existing\": true}").unwrap();

        let outcome = merge_config(&old, &new).expect("merge should not error");
        // The merge proceeds fail-open: corrupt source treated as Null,
        // so 0 keys are written from old.
        match outcome {
            MergeOutcome::Merged(0) => {}
            MergeOutcome::Merged(n) => panic!("expected Merged(0) for corrupt source, got Merged({})", n),
            MergeOutcome::Copied => panic!("expected Merged for existing target, got Copied"),
        }
        // A backup file matching the corrupt-pre-migration pattern
        // must now exist next to the source.
        let backups: Vec<_> = std::fs::read_dir(&root)
            .unwrap()
            .flatten()
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .filter(|n| n.starts_with("config.json.corrupt-pre-migration."))
            .filter(|n| n.ends_with(".bak"))
            .collect();
        assert_eq!(backups.len(), 1, "exactly one backup should exist: {:?}", backups);
        // The backup must contain the original corrupt bytes.
        let backup_contents = std::fs::read(root.join(&backups[0])).unwrap();
        assert_eq!(backup_contents, b"{not valid json");
    }

    /// XZ-R12-11: merge_config uses whole-file mtime (not per-key) to
    /// decide which file's values win for overlapping keys. When `old`
    /// is newer, ALL of old's keys overwrite new's; when `new` is
    /// newer, NONE of old's overlapping keys are taken (only keys
    /// present solely in old are merged).
    #[test]
    fn merge_config_whole_file_mtime_wins() {
        let _scratch = ScratchDir::new("mtime-wins");
        let root = _scratch.path().to_path_buf();
        let old = root.join("old.json");
        let new = root.join("new.json");

        // Write old first, then new (so new is newer by mtime).
        std::fs::write(&old, b"{\"a\": 1, \"b\": 2}").unwrap();
        // Sleep briefly so mtimes are distinguishable on filesystems
        // with coarse mtime granularity (HFS+ has 1s resolution).
        std::thread::sleep(std::time::Duration::from_millis(1100));
        std::fs::write(&new, b"{\"b\": 99, \"c\": 3}").unwrap();

        // new is newer -> b stays 99 (new's value), a is taken from old
        // (only in old), c stays 3 (only in new).
        let outcome = merge_config(&old, &new).expect("merge should succeed");
        // Only `a` is solely in old -> 1 key written.
        match outcome {
            MergeOutcome::Merged(1) => {}
            other => panic!("expected Merged(1) (only `a` is solely in old), got {:?}", other),
        }
        let merged: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&new).unwrap()).unwrap();
        let obj = merged.as_object().expect("merged should be an object");
        assert_eq!(obj.get("a").and_then(|v| v.as_i64()), Some(1), "a taken from old");
        assert_eq!(obj.get("b").and_then(|v| v.as_i64()), Some(99),
            "b stays new's value (new is newer)");
        assert_eq!(obj.get("c").and_then(|v| v.as_i64()), Some(3), "c stays new's value");

        // Now flip: make old newer than new. ALL of old's overlapping
        // keys win.
        std::fs::write(&old, b"{\"b\": 7, \"d\": 4}").unwrap();
        // old is now newer (just rewritten). new's mtime is unchanged.
        let outcome2 = merge_config(&old, &new).expect("merge should succeed");
        // `b` overlaps and old is newer -> taken. `d` is solely in old -> taken.
        // `a` and `c` are absent from old (this run's old only has b, d),
        // so they stay in new.
        match outcome2 {
            MergeOutcome::Merged(2) => {}
            other => panic!("expected Merged(2) (b + d from old), got {:?}", other),
        }
        let merged2: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&new).unwrap()).unwrap();
        let obj2 = merged2.as_object().unwrap();
        assert_eq!(obj2.get("b").and_then(|v| v.as_i64()), Some(7),
            "b taken from old (old is newer)");
        assert_eq!(obj2.get("d").and_then(|v| v.as_i64()), Some(4), "d taken from old");
    }

    /// XZ-R12-17: `sidecar_path` appends the suffix to the file_name
    /// (NOT to the extension) so `history.db` -> `history.db-wal`.
    /// Critical for SQLite WAL mode — the sidecar files live next to
    /// the main db with the literal `-wal` / `-shm` suffix.
    #[test]
    fn sidecar_path_appends_suffix_to_filename() {
        let db = PathBuf::from("/tmp/config/history.db");
        assert_eq!(
            sidecar_path(&db, "-wal"),
            PathBuf::from("/tmp/config/history.db-wal")
        );
        assert_eq!(
            sidecar_path(&db, "-shm"),
            PathBuf::from("/tmp/config/history.db-shm")
        );
    }

    /// XZ-R12-17 (companion): `sidecar_path` for a db whose name
    /// already contains dots — the suffix is appended to the WHOLE
    /// name, not after the first dot.
    #[test]
    fn sidecar_path_handles_dotfiles() {
        let db = PathBuf::from("/tmp/config/my.history.db");
        assert_eq!(
            sidecar_path(&db, "-wal"),
            PathBuf::from("/tmp/config/my.history.db-wal")
        );
    }

    /// XZ-R4-013 (companion): `copy_missing_files` must NOT clobber
    /// an existing target file. Even with the symlink-skip fix, a
    /// pre-existing target file (e.g. a newer model the user just
    /// downloaded) must be preserved.
    #[test]
    fn copy_missing_files_does_not_clobber_existing() {
        let _scratch = ScratchDir::new("no-clobber");
        let root = _scratch.path().to_path_buf();
        let src = root.join("src");
        let dst = root.join("dst");
        std::fs::create_dir_all(&src).unwrap();
        std::fs::create_dir_all(&dst).unwrap();

        std::fs::write(src.join("model.bin"), b"OLD").unwrap();
        std::fs::write(dst.join("model.bin"), b"NEW (preserved)").unwrap();

        let count = copy_missing_files(&src, &dst);
        assert_eq!(count, 0, "no files should be copied (target exists)");
        // The target's content must be unchanged.
        assert_eq!(
            std::fs::read_to_string(dst.join("model.bin")).unwrap(),
            "NEW (preserved)"
        );
    }

    // ── XZ-R12-03: sentinel gating on partial failures ───────────────

    /// XZ-R12-03: when `migration_failed == 0`, the sentinel marker
    /// MUST be written so the next launch skips re-migration.
    /// Pre-fix, the sentinel was written UNCONDITIONALLY — even after
    /// a partial failure — which silently dropped the user's data on
    /// the next launch (migration skipped because sentinel present).
    #[test]
    fn write_sentinel_if_clean_writes_on_success() {
        let _scratch = ScratchDir::new("sentinel-success");
        let new_dir = _scratch.path().to_path_buf();
        let sentinel = new_dir.join(".migrated-from-electron");
        assert!(!sentinel.exists(), "sentinel must not exist before call");

        let written = write_sentinel_if_clean(&new_dir, 0);
        assert!(written, "sentinel should be written when migration_failed == 0");
        assert!(sentinel.exists(), "sentinel file must exist on disk");
        // The sentinel is an empty marker file — its presence (not its
        // contents) is what the early-return guard checks.
        assert_eq!(
            std::fs::read(&sentinel).unwrap(),
            b"",
            "sentinel marker must be empty"
        );
    }

    /// XZ-R12-03: when `migration_failed > 0`, the sentinel marker
    /// MUST NOT be written. Next launch will re-attempt the migration
    /// (the operations are idempotent — atomic_copy uses temp+rename,
    /// merge_config is key-by-key). This is the core fix: a partial
    /// failure must not silently skip re-migration on next launch.
    #[test]
    fn write_sentinel_if_clean_skips_on_failure() {
        let _scratch = ScratchDir::new("sentinel-skip");
        let new_dir = _scratch.path().to_path_buf();
        let sentinel = new_dir.join(".migrated-from-electron");
        assert!(!sentinel.exists(), "sentinel must not exist before call");

        // Simulate 1 critical-step failure (e.g. history.db copy failed).
        let written = write_sentinel_if_clean(&new_dir, 1);
        assert!(!written, "sentinel must NOT be written when migration_failed > 0");
        assert!(
            !sentinel.exists(),
            "sentinel file must NOT exist on disk after a failed migration"
        );
    }

    /// XZ-R12-03: the gate must hold for any non-zero failure count
    /// (not just 1). A migration that fails 2 critical steps (e.g.
    /// config.json merge AND history.db copy both fail) must still
    /// skip the sentinel.
    #[test]
    fn write_sentinel_if_clean_skips_on_multiple_failures() {
        let _scratch = ScratchDir::new("sentinel-multi-fail");
        let new_dir = _scratch.path().to_path_buf();
        let sentinel = new_dir.join(".migrated-from-electron");

        let written = write_sentinel_if_clean(&new_dir, 3);
        assert!(!written, "sentinel must NOT be written when migration_failed > 1");
        assert!(!sentinel.exists(), "sentinel file must NOT exist on disk");
    }

    /// XZ-R12-03 (companion): when `migration_failed == 0` AND the
    /// target directory does not exist, `write_sentinel_if_clean`
    /// must return `false` (the `std::fs::write` will fail because
    /// the parent dir doesn't exist). The function must not panic —
    /// the caller (`migrate_electron_userdata`) treats a `false`
    /// return as "try again next launch", which is the safe
    /// behavior.
    #[test]
    fn write_sentinel_if_clean_handles_write_error() {
        // Use a path whose parent doesn't exist — std::fs::write will
        // fail with NotFound.
        let bogus_dir = std::env::temp_dir().join(format!(
            "vt-migrate-sentinel-noexist-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        // Note: we deliberately do NOT create bogus_dir.
        let written = write_sentinel_if_clean(&bogus_dir, 0);
        assert!(!written, "write to non-existent dir must return false");
        assert!(
            !bogus_dir.join(".migrated-from-electron").exists(),
            "no sentinel must be left behind"
        );
    }
}
