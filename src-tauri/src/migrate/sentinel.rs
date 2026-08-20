//! Sentinel marker management for the Electron → Tauri migration.
//!
//! Extracted from the original `migrate.rs` monolith as part of the
//! Phase 4.5 split. Pure file move — no behavior change. See
//! `mod.rs` for the gating caller (`migrate_inner`).

use std::path::Path;

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
pub(crate) fn write_sentinel_if_clean(new_dir: &Path, migration_failed: usize) -> bool {
    if migration_failed > 0 {
        log::warn!(
            "[MIGRATE] {} critical steps failed — NOT writing sentinel marker; \
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
