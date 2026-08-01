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
//! New Tauri config_dir: `platform::paths::config_dir()` (already implemented).
//!
//! Merge rules (do NOT overwrite newer data):
//! - `config.json`: if absent in target, copy whole; if present but differs,
//!   merge key-by-key. The entire newer file's values win for overlapping
//!   keys (single whole-file mtime comparison — NOT per-key mtime; see
//!    fix on `merge_config`).
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

use std::path::{Path, PathBuf};

// bring the `util` module into scope so the atomic-fs helpers
// (`util::atomic_copy`, `util::atomic_copy_file`, `util::atomic_write_bytes`)
// — which were moved from this module to `crate::util` — resolve without
// a per-call-site `crate::util::` qualification. Replaces the prior
// `use crate::util::atomic_write_bytes;` bridge import (which imported
// only the function, not the module) that lived here pre-.
use crate::util;



/// Resolve the OLD Electron `userData` directory candidates per platform.
///
/// Returns a list of candidate paths in probe order (most-likely first).
/// The caller probes each in turn and uses the first one that exists on
/// disk. Returns an empty `Vec` if the platform's relevant env vars are
/// missing (caller treats that as "nothing to migrate" — safe no-op).
///
//fix: the previous implementation only probed `Voice Typer`
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
        // derived the default `userData` path from `package.json`
        // `name` = `voice-typer-desktop`.
        "voice-typer-desktop",
        // 2. Newer Electron builds with `setupUserData` (bootstrap.ts:52-67):
        // `app.setPath("userData", computeConfigDir())` → `voice-typer`.
        // This is the SAME path Tauri now uses as its `config_dir`, so
        // the caller skips it when it equals the Tauri target.
        crate::platform::paths::APP_SLUG,
        // 3. Defensive third probe — the human-readable brand name with a
        // space, in case some ancient unreleased build used it as the
        // userData directory name. Uses `crate::branding::APP_NAME`
        // (const-context) so the probe stays in lockstep with the rest
        // of the UI's brand string.
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
        //fix: collapse dead conditional (both arms returned the
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
///
/// Visibility: `pub(crate)` — the only caller is `main.rs` at app
/// startup. Demoted from `pub` (which would expose the symbol on the
/// crate's public surface) because no external crate links against
/// `voice-typer` (it's a binary crate, not a library), and a tighter
/// visibility surfaces unintended cross-module couplings at compile
/// time rather than letting them slip through as silent API growth.
pub(crate) fn migrate_electron_userdata(_app: &tauri::AppHandle) {
    // `_app` was only used to call `platform::paths::config_dir(app)`;
    // now that `config_dir()` takes no args, the param is unused. Kept in
    // the signature for forward-compat (a future migration might need
    // `app.path().resource_dir()` to copy bundled defaults). Prefixed
    // with `_` to silence the unused-param lint under clippy::all.
    //
    // This synchronous wrapper runs the fs-heavy migration
    // inline on the caller's thread. Callers inside an async context
    // (e.g. the Tauri `setup` closure, which already spawns an async
    // task for `spawn_sidecar_and_get_port`) should prefer
    // `migrate_electron_userdata_async` instead — it moves the fs
    // ops onto `tauri::async_runtime::spawn_blocking` so the calling
    // task is not stalled for 5-30s on first launch. The body lives
    // in `migrate_inner` so both wrappers share the same logic.
    let new_dir = crate::platform::paths::config_dir();
    migrate_inner(&new_dir);
}

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
/// `#[allow(dead_code)]` is intentional: until `main.rs:164` switches
/// from the sync `migrate_electron_userdata` call to this async wrapper
/// (moving the call inside the existing
/// `tauri::async_runtime::spawn(async move { ... })` block at
/// `main.rs:186`), the function has no production caller. The body it
/// calls (`migrate_inner`) is exercised directly by the
/// `migrate_inner_returns_early_when_sentinel_present` unit test, and
/// the `spawn_blocking(move || migrate_inner(...)).await` plumbing
/// pattern is exercised by
/// `migrate_inner_runs_under_spawn_blocking_without_panic`. The wiring
/// switch is a one-line change in `main.rs` with no further work
/// needed here.
#[allow(dead_code)]
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

//write the `.migrated-from-electron` sentinel marker to
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

#[derive(Debug)]
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
fn merge_config(old: &Path, new: &Path) -> Result<MergeOutcome, String> {
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
    util::atomic_write_bytes(new, out.as_bytes())?;
    Ok(MergeOutcome::Merged(written))
}

// ─── atomic_copy / atomic_copy_file relocation note () ──────────
//
// `atomic_copy`, `atomic_copy_file`, and the local
// `use crate::util::atomic_write_bytes;` bridge import that lived
// here have been moved to `crate::util` (alongside `atomic_write_bytes`,
//which  already relocated). All call sites in this file now
// spell them as `util::atomic_copy(...)` / `util::atomic_copy_file(...)`
// / `util::atomic_write_bytes(...)`. The two helpers are generic
// fs-copy utilities with no coupling to Electron-migration logic,
// so co-locating them with `atomic_write_bytes` in `util.rs` is the
// correct architectural home. Pure refactor — no behavior change.

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

/// M-65: build the path of a SQLite sidecar file (`-wal` / `-shm`)
/// for a given main db path. Appends the suffix to the literal
/// file_name (NOT to the extension) so `history.db` →
/// `history.db-wal`.
fn sidecar_path(db: &Path, suffix: &str) -> PathBuf {
    //`file_name()` returns `Option<&OsStr>`; `to_str()` borrows
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
        //use `symlink_metadata` (NOT `metadata`) so we can
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
        //`entry.file_name().into_string()` consumes the OsString
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
            //use atomic copy (temp + rename in same dir)
            // so an interrupted migration never leaves a partial model
            // file at the destination. Pre-fix, `std::fs::copy` truncated
            // then wrote — combined with the `if dst_path.exists() { continue; }`
            // guard above, a partial file from a killed migration looked
            // "existing" on next launch and was skipped, leaving a
            // corrupt model file in the target. The atomic copy writes
            // to a sibling temp file then renames, so the destination
            // is either fully-present or fully-absent — never partial.
            if let Err(e) = util::atomic_copy_file(&path, &dst_path) {
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

    //a symlink in the source `models/` dir must NOT be
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

    //`atomic_copy_file` (now in `crate::util` per )
    /// must produce a destination whose bytes match the source exactly,
    /// and must NOT leave a temp file behind on success.
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
        util::atomic_copy_file(&src, &dst).expect("atomic copy should succeed");
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

    //`atomic_copy_file` must NOT leave a partial destination
    /// if the source doesn't exist (the copy fails before the rename).
    #[test]
    fn atomic_copy_file_no_partial_on_missing_source() {
        let _scratch = ScratchDir::new("atomic-copy-missing");
        let root = _scratch.path().to_path_buf();
        let src = root.join("nonexistent.bin");
        let dst = root.join("dst.bin");
        let err = util::atomic_copy_file(&src, &dst)
            .expect_err("missing source must produce an error");
        assert!(err.contains("copy"), "error should mention copy: {}", err);
        assert!(!dst.exists(), "destination must NOT exist after failure");
    }

    //when `merge_config` encounters a corrupt source
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

    //merge_config uses whole-file mtime (not per-key) to
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

    //`sidecar_path` appends the suffix to the file_name
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

    //(companion): `sidecar_path` for a db whose name
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

    //(companion): `copy_missing_files` must NOT clobber
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

    //sentinel gating on partial failures ───────────────

    //when `migration_failed == 0`, the sentinel marker
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

    //when `migration_failed > 0`, the sentinel marker
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

    //the gate must hold for any non-zero failure count
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

    //(companion): when `migration_failed == 0` AND the
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

    /// `atomic_copy` was moved from this module to `crate::util`
    /// (alongside `atomic_write_bytes`). This test now exercises the
    /// qualified `util::atomic_copy` call path end-to-end, guarding
    /// against an accidental future regression where someone removes
    /// the qualification at the call site or forgets to re-export the
    /// helper from `util`. (Pre-this test guarded the local
    /// `use crate::util::atomic_write_bytes;` bridge import; that
    /// bridge is gone now that `atomic_copy` itself lives in `util.rs`
    /// and calls `atomic_write_bytes` as a same-module sibling.)
    #[test]
    fn atomic_copy_uses_local_atomic_write_bytes_import() {
        let _scratch = ScratchDir::new("atomic-copy-import");
        let root = _scratch.path().to_path_buf();
        let src = root.join("src.bin");
        let dst = root.join("dst.bin");
        std::fs::write(&src, b"hello-migrate").unwrap();

        util::atomic_copy(&src, &dst).expect("atomic_copy must succeed");

        let written = std::fs::read(&dst).expect("dst must exist after copy");
        assert_eq!(written, b"hello-migrate");
        assert_eq!(
            std::fs::read(&src).expect("src must be unchanged"),
            b"hello-migrate",
            "atomic_copy must NOT mutate the source file"
        );
    }

    // migrate_inner + migrate_electron_userdata_async ────────────────
    //
    // `migrate_electron_userdata` (the sync entry-point) was refactored
    // so its body lives in `migrate_inner(new_dir: &Path)`. This makes
    // the migration logic callable from both:
    //   - the sync wrapper `migrate_electron_userdata` (still called
    //     from `main.rs:164`'s setup closure), and
    //   - the async wrapper `migrate_electron_userdata_async` (added
    //     so the fs-heavy body runs on
    //     `tauri::async_runtime::spawn_blocking` and the calling async
    //     task is not stalled for 5-30s on first launch).
    //
    // The two tests below pin the new behavior:
    //   1. `migrate_inner` short-circuits when the sentinel marker is
    //      already present (no env-var manipulation needed — the
    //      sentinel check runs BEFORE `electron_userdata_candidates()`
    //      reads any env vars).
    //   2. `migrate_inner` is callable from a `spawn_blocking` closure
    //      (the exact pattern `migrate_electron_userdata_async` uses
    //      internally) without panic and returns cleanly.

    /// `migrate_inner` must early-return without doing any fs work when
    /// the `.migrated-from-electron` sentinel marker is already present
    /// in `new_dir`. This is the idempotency short-circuit that makes
    /// the migration safe to call on every launch.
    ///
    /// We pre-create the sentinel before calling `migrate_inner` so the
    /// function returns at the FIRST guard (sentinel.exists() check) —
    /// BEFORE `electron_userdata_candidates()` is called. This means
    /// the test does NOT need to manipulate any env vars (HOME,
    /// APPDATA, XDG_CONFIG_HOME) and is safe to run in parallel with
    /// other tests.
    #[test]
    fn migrate_inner_returns_early_when_sentinel_present() {
        let _scratch = ScratchDir::new("sentinel-shortcircuit");
        let new_dir = _scratch.path().to_path_buf();
        // Pre-create the sentinel marker so migrate_inner short-circuits.
        std::fs::write(new_dir.join(".migrated-from-electron"), b"").unwrap();
        // Drop a "decoy" file that the migration WOULD copy if it ran —
        // proves the short-circuit didn't proceed past the sentinel guard.
        // (If the migration proceeded, it would have created config.json
        // from a candidate old Electron userData dir. By asserting no
        // config.json appears, we verify the short-circuit held.)
        assert!(
            !new_dir.join("config.json").exists(),
            "config.json must not exist before migrate_inner call"
        );

        // Call the refactored body. Should return immediately without
        // touching env vars or probing candidate paths.
        migrate_inner(&new_dir);

        // The sentinel must still be present (migrate_inner must not
        // delete it on the early-return path).
        assert!(
            new_dir.join(".migrated-from-electron").exists(),
            "sentinel marker must still exist after early-return"
        );
        // No config.json should have been created (the migration did
        // not proceed past the sentinel check).
        assert!(
            !new_dir.join("config.json").exists(),
            "config.json must NOT be created when sentinel short-circuited the migration"
        );
    }

    /// `migrate_inner` runs unchanged when called from inside a
    /// `spawn_blocking` closure — the exact pattern
    /// `migrate_electron_userdata_async` uses to move the fs-heavy
    /// migration off the async runtime's worker threads.
    ///
    /// This test exercises the same `spawn_blocking(move || migrate_inner(...))`
    /// plumbing that the async wrapper uses, but calls `tokio::task::spawn_blocking`
    /// directly (rather than `tauri::async_runtime::spawn_blocking`) so the
    /// test does not require initializing Tauri's global async runtime.
    /// `tauri::async_runtime::spawn_blocking` delegates to the same
    /// Tokio blocking-pool mechanism, so the pattern is functionally
    /// identical.
    ///
    /// We pre-create the sentinel marker so `migrate_inner` short-circuits
    /// at its first guard (no env-var manipulation needed — see the
    /// companion sync test above).
    #[tokio::test]
    async fn migrate_inner_runs_under_spawn_blocking_without_panic() {
        let _scratch = ScratchDir::new("spawn-blocking-plumbing");
        let new_dir = _scratch.path().to_path_buf();
        // Pre-create the sentinel so migrate_inner short-circuits
        // without needing env vars (which would be racy under parallel
        // test execution).
        std::fs::write(new_dir.join(".migrated-from-electron"), b"").unwrap();
        // Clone `new_dir` into the closure (the same move pattern used
        // by `migrate_electron_userdata_async`).
        let new_dir_for_closure = new_dir.clone();

        // Spawn migrate_inner on the blocking pool and await its
        // completion. This mirrors the exact shape of
        // `migrate_electron_userdata_async`'s body:
        //   tauri::async_runtime::spawn_blocking(move || migrate_inner(&new_dir)).await
        let join_result = tokio::task::spawn_blocking(move || {
            migrate_inner(&new_dir_for_closure);
        })
        .await;

        // The JoinHandle must resolve to Ok (no panic in the closure).
        // `migrate_inner` is designed to never panic (all fs ops are
        // wrapped in `match`/`if let Err(e)` with log-and-continue),
        // so a panic here would indicate a regression.
        assert!(
            join_result.is_ok(),
            "spawn_blocking(migrate_inner) must not panic: {:?}",
            join_result.err()
        );
        // Sentinel must still be present (migrate_inner short-circuited).
        assert!(
            new_dir.join(".migrated-from-electron").exists(),
            "sentinel marker must still exist after spawn_blocking migration"
        );
    }
}
