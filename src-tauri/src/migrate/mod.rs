//! One-time Electron `userData` → Tauri `<config_dir>` migration
//! (ADR-0020 §8).
//!
//! Add `mod migrate;` to main.rs and call
//! `migrate::migrate_electron_userdata_async(&app_handle).await` at
//! the start of the Tauri `setup` closure's async spawn block, BEFORE
//! the `spawn_sidecar` task.
//!
//! The migration is idempotent and SAFE: it never destroys data. It
//! early-returns after the first successful run (or when there is
//! nothing to do), so it is cheap and safe to call on every launch.
//!
//! Old Electron `userData` locations ( fix: probe all three in order,
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
//! New Tauri config_dir: `platform::paths::config_dir()` (already
//! implemented).
//!
//! Merge rules (do NOT overwrite newer data):
//! - `config.json`: if absent in target, copy whole; if present but differs,
//!   merge key-by-key. The entire newer file's values win for overlapping
//!   keys (single whole-file mtime comparison — NOT per-key mtime; see
//!   fix on `merge_config`).
//! - `models/`: copy only files ABSENT from the target (: symlinks
//!   are skipped; : copy is atomic via temp+rename).
//! - `history.db`: copy only if target absent (append is unsafe for SQLite —
//!   skip with a warning rather than risk corruption). WAL/SHM sidecars
//!   copied atomically; if either fails, target sidecars are deleted so
//!   SQLite starts fresh ().
//! - `voice-typer-recovery.json`: copy if target absent.
//!
//! All fs ops are wrapped so this function NEVER panics.
//!
//! : the sentinel marker is written ONLY when ALL critical
//! migration steps succeeded. If any step fails (config merge, history.db
//! copy, recovery.json copy, models dir creation), the sentinel is skipped
//! so the next launch re-attempts the migration (idempotent ops).
//!
//! # Module layout (Phase 4.5 split)
//!
//! The original `migrate.rs` (1339-line monolith) was split into a
//! `migrate/` directory with focused submodules. Public API preserved
//! — pure file move + `mod` declarations, no behavior change.
//!
//! - `mod.rs` (this file): entry points (`migrate_electron_userdata`,
//!   `migrate_electron_userdata_async`) + `migrate_inner` orchestration
//!   + re-exports.
//! - `candidates.rs`: platform probe `electron_userdata_candidates`.
//! - `sentinel.rs`: `write_sentinel_if_clean`.
//! - `config_merge.rs`: `merge_config`, `MergeOutcome`,
//!   `file_newer_than`, `backup_corrupt_config`. (Includes the fix
//!   that consumes `old_val` instead of borrowing+cloning.)
//! - `copy.rs`: `copy_missing_files`, `copy_missing_recursive`,
//!   `sidecar_path`.
//! - `tests.rs`: all unit tests (moved verbatim).

use std::path::{Path, PathBuf};

// bring the `util` module into scope so the atomic-fs helpers
// (`util::atomic_copy`, `util::atomic_copy_file`, `util::atomic_write_bytes`)
// — which were moved from this module to `crate::util` — resolve without
// a per-call-site `crate::util::` qualification. Replaces the prior
// `use crate::util::atomic_write_bytes;` bridge import (which imported
// only the function, not the module) that lived here pre-.
use crate::util;

mod candidates;
mod config_merge;
mod copy;
mod sentinel;
#[cfg(test)]
mod tests;

// Re-export the public APIs so callers (and the `tests` submodule via
// `use super::*;`) can reference them with the same paths the original
// monolith used (e.g. `merge_config(...)`, `MergeOutcome::Copied`,
// `write_sentinel_if_clean(...)`, `copy_missing_files(...)`,
// `sidecar_path(...)`, `electron_userdata_candidates()`). Pure
// mechanical relocation — no behavior change.
pub(crate) use candidates::electron_userdata_candidates;
pub(crate) use config_merge::{merge_config, MergeOutcome};
pub(crate) use copy::{copy_missing_files, sidecar_path};
pub(crate) use sentinel::write_sentinel_if_clean;

/// Async wrapper around `migrate_inner` that runs the fs-heavy
/// migration on `tauri::async_runtime::spawn_blocking` so the calling
/// async task is not stalled for 5-30s on first launch.
///
/// The returned future resolves once the migration has completed (or
/// failed — `migrate_inner` never panics, all fs errors are logged and
/// swallowed), so a caller like the Tauri `setup` task can safely
/// `.await` it BEFORE `spawn_sidecar_and_get_port` to guarantee the
/// sidecar boots against already-migrated data without blocking the
/// async runtime's worker threads.
///
/// This wrapper is now the production entry point — `main.rs`
/// calls it inside the existing `tauri::async_runtime::spawn` block
/// at the start of `.setup`, BEFORE `sidecar::spawn::initialize_sidecar`.
/// The previous `#[allow(dead_code)]` is removed now that the call
/// site exists. The body it calls (`migrate_inner`) is exercised
/// directly by the `migrate_inner_returns_early_when_sentinel_present`
/// unit test, and the `spawn_blocking(move || migrate_inner(...)).await`
/// plumbing pattern is exercised by
/// `migrate_inner_runs_under_spawn_blocking_without_panic`.
pub(crate) async fn migrate_electron_userdata_async(_app: &tauri::AppHandle) {
    // Compute `new_dir` on the calling task (cheap —
    // `config_dir()` is cached via `config_dir_cached`) so the
    // blocking closure only owns a `PathBuf`, not an `AppHandle`
    // (which would require `Send + 'static` and complicate the
    // signature needlessly).
    let new_dir = crate::platform::paths::config_dir();
    // `spawn_blocking` runs the closure on a dedicated blocking-thread
    // pool (separate from the Tokio async runtime's worker threads),
    // so the fs ops (`atomic_copy`, `merge_config`,
    // `copy_missing_files`) do not starve other futures sharing the
    // runtime. `await` yields the calling task until the migration
    // completes — the sidecar spawn can then proceed against the
    // fully-migrated `config_dir`.
    //
    // Errors from the closure itself are impossible (`migrate_inner`
    // returns `()` and never panics — every fs op is wrapped in
    // `match`/`if let Err(e)` with a log-and-continue). The only
    // `Err` the `JoinHandle` can yield is `JoinError` (task panicked
    // OR was cancelled); we log-and-continue either way so a failed
    // migration never breaks the sidecar spawn — the next launch
    // re-attempts (idempotent).
    if let Err(e) = tauri::async_runtime::spawn_blocking(move || migrate_inner(&new_dir)).await {
        log::error!(
            "[MIGRATE] async spawn_blocking join failed: {} — migration skipped this launch; will retry next launch",
            e
        );
    }
}

/// Body of the Electron → Tauri `config_dir` migration. Extracted
/// from `migrate_electron_userdata` so the same logic is shared by the
/// sync and async wrappers AND so it can be unit-tested without a
/// Tauri `AppHandle` (the entry-point functions take one and are hard
/// to construct in `#[cfg(test)]`). `new_dir` is the Tauri
/// `config_dir` path; the function probes the old Electron
/// `userData` candidates itself (see `electron_userdata_candidates`).
///
/// Idempotent and SAFE: never panics, never destroys data. Early-
/// returns after the first successful run (sentinel marker present)
/// or when there is nothing to do.
fn migrate_inner(new_dir: &Path) {
    //fix: use a sentinel file (.migrated-from-electron) as the
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

    //fix: probe each candidate in order; use the first that exists
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
        if candidate.as_os_str() == new_dir.as_os_str() {
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
    //track critical-step failures so the sentinel marker
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
            // M-65: also copy SQLite WAL sidecars (-wal / -shm) if
            // present. Without these, recent WAL-mode transactions
            // in the source db would be lost on the migrated copy.
            // Each sidecar is copied atomically and independently;
            // a missing sidecar is not an error (SQLite regenerates
            // -shm and replays -wal only if both are present).
            //
            //if EITHER sidecar copy fails, delete all
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
                    if let Err(e) = util::atomic_copy(&old_side, &new_side) {
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
        //use atomic_copy for recovery.json too (was
        // std::fs::copy). A partial recovery.json would load as
        // invalid JSON on next launch and the user's recovery
        // snapshot would be silently dropped — same data-loss
        // risk as the config.json case. The recovery file is
        // small (a few KB), so atomic_copy's read-into-memory is
        // fine here.
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
        "[MIGRATE] done — config: copied={} merged_keys={}, models_new_files={}, \
         history_copied={}, recovery_copied={}, failures={}",
        config_copied,
        config_merged,
        models_copied,
        history_copied,
        recovery_copied,
        migration_failed
    );

    //fix: write the sentinel marker AFTER successful migration so
    // subsequent launches skip re-migration. Without this, every launch
    // would re-attempt the (idempotent but log-noisy) migration.
    //
    //only write the sentinel if ALL critical migration
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
