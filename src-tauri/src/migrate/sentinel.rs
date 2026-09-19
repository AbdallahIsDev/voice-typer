
use std::path::Path;

pub(crate) fn write_sentinel_if_clean(new_dir: &Path, migration_failed: usize) -> bool {
    if migration_failed > 0 {
        log::warn!(
            "[MIGRATE] {} critical steps failed: NOT writing sentinel marker; \
             migration will re-attempt on next launch",
            migration_failed
        );
        return false;
    }
    let migration_marker = new_dir.join(".migrated-from-legacy");
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
