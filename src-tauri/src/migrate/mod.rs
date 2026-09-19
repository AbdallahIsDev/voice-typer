//! One-time predecessor `userData` → Tauri `<config_dir>` migration
//! (ADR-0020 §8). Idempotent + safe: never panics/destroys data;
//! sentinel `.migrated-from-legacy` written only when ALL critical steps
//! succeed (failed model copies also defer the sentinel).
//! NOTE: see docs/code-notes/tauri-host.md#migration

use std::path::{Path, PathBuf};

// Atomic-fs helpers live in crate::util.
use crate::util;

mod candidates;
mod config_merge;
mod copy;
mod sentinel;
#[cfg(test)]
mod tests;

// Preserve pre-split public paths (create-first split, E1).
pub(crate) use candidates::legacy_userdata_candidates;
pub(crate) use config_merge::{merge_config, MergeOutcome};
pub(crate) use copy::{copy_missing_files, sidecar_path};
pub(crate) use sentinel::write_sentinel_if_clean;

pub(crate) async fn migrate_legacy_userdata_async(_app: &tauri::AppHandle) {
    // Cheap cached path resolve on the calling task.
    let new_dir = crate::platform::paths::config_dir();
    if let Err(e) = tauri::async_runtime::spawn_blocking(move || migrate_inner(&new_dir)).await {
        log::error!(
            "[MIGRATE] async spawn_blocking join failed: {}, migration skipped this launch; will retry next launch",
            e
        );
    }
}

fn migrate_inner(new_dir: &Path) {
    // Sentinel: only a successful migration may mark "already migrated".
    let migration_marker = new_dir.join(".migrated-from-legacy");
    if migration_marker.exists() {
        log::info!("[MIGRATE] already migrated (sentinel marker present)");
        return;
    }

    // Probe candidates; skip a candidate equal to the Tauri target
    // (self-copy no-op). No sentinel if nothing exists (re-probe next launch).
    let candidates = legacy_userdata_candidates();
    if candidates.is_empty() {
        log::info!("[MIGRATE] nothing to do (could not resolve old userData dir, platform env vars missing)");
        return;
    }

    let mut old_dir: Option<PathBuf> = None;
    for candidate in &candidates {
        log::info!("[MIGRATE] probing legacy userdata at: {:?}", candidate);
        if candidate.as_os_str() == new_dir.as_os_str() {
            log::info!(
                "[MIGRATE]   skipping {:?} (same as Tauri config_dir target, self-copy no-op)",
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

    if let Err(e) = std::fs::create_dir_all(new_dir) {
        log::error!(
            "[MIGRATE] cannot create target dir {}: {}",
            new_dir.display(),
            e
        );
        return;
    }

    let mut config_merged = 0usize;
    let mut config_copied = false;
    let mut models_copied = 0usize;
    let mut models_failed = 0usize;
    let mut history_copied = false;
    let mut recovery_copied = false;
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

    // 4b. models/: copy only files absent from target.
    let old_models = old_dir.join("models");
    let new_models = new_dir.join("models");
    if old_models.is_dir() {
        if let Err(e) = std::fs::create_dir_all(&new_models) {
            log::error!("[MIGRATE] cannot create models dir: {}", e);
            migration_failed += 1;
        } else {
            let stats = copy_missing_files(&old_models, &new_models);
            models_copied = stats.copied;
            models_failed = stats.failed;
            if models_copied > 0 {
                log::info!("[MIGRATE] models/ copied {} new files", models_copied);
            }
            if models_failed > 0 {
                log::warn!(
                    "[MIGRATE] models/ {} file copies failed: targets left absent; \
                     sentinel deferred so next launch retries them",
                    models_failed
                );
            }
        }
    }

    // 4c. history.db: copy only if target absent (append unsafe for SQLite).
    let old_db = old_dir.join("history.db");
    let new_db = new_dir.join("history.db");
    if old_db.is_file() {
        if new_db.exists() {
            log::warn!(
                "[MIGRATE] history.db skipped (target exists): NOT overwriting to avoid corruption"
            );
        } else {
            match util::atomic_copy(&old_db, &new_db) {
                Ok(()) => {
                    history_copied = true;
                    log::info!("[MIGRATE] history.db copied");
                }
                Err(e) => {
                    log::error!("[MIGRATE] history.db copy failed: {}", e);
                    migration_failed += 1;
                }
            }
            let mut sidecar_failed = false;
            for suffix in &["-wal", "-shm"] {
                let old_side = sidecar_path(&old_db, suffix);
                let new_side = sidecar_path(&new_db, suffix);
                if old_side.is_file() && !new_side.exists() {
                    if let Err(e) = util::atomic_copy(&old_side, &new_side) {
                        log::warn!(
                            "[MIGRATE] history.db{} copy failed: {}, will delete target sidecars",
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
                    "[MIGRATE] history.db WAL sidecar migration incomplete: \
                     WAL transactions from source may be lost; SQLite will start fresh"
                );
            }
        }
    }

    // 4d. voice-typer-recovery.json: copy if target absent.
    let old_rec = old_dir.join("voice-typer-recovery.json");
    let new_rec = new_dir.join("voice-typer-recovery.json");
    if old_rec.is_file() && !new_rec.exists() {
        if let Err(e) = util::atomic_copy(&old_rec, &new_rec) {
            log::error!("[MIGRATE] voice-typer-recovery.json copy failed: {}", e);
            migration_failed += 1;
        } else {
            recovery_copied = true;
            log::info!("[MIGRATE] voice-typer-recovery.json copied");
        }
    }

    // 5. Summary.
    log::info!(
        "[MIGRATE] done: config: copied={} merged_keys={}, models_new_files={}, \
         models_failed={}, history_copied={}, recovery_copied={}, failures={}",
        config_copied,
        config_merged,
        models_copied,
        models_failed,
        history_copied,
        recovery_copied,
        migration_failed
    );

    let _ = write_sentinel_if_clean(new_dir, migration_failed + models_failed);
}
