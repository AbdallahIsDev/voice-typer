//! Prewarm binary path resolution (release + dev) — extracted from the
//! former single-file `sidecar/spawn.rs` (EO-33 split).

use tauri::Manager;

use super::target_triple::current_target_triple;

/// dev-mode counterpart of `prewarm_resource_path` (which
/// needs an `AppHandle` and so can't be called from
/// `spawn_sidecar_dev_mode`). Resolves the prewarm exe path relative
/// to the source-tree root (the dev-mode cwd under `cargo tauri dev`)
/// so the Python sidecar's prewarm scheduled-task integration sees the
/// same env var the release path provides.
///
/// Returns the empty string when the path can't be constructed (only
/// fails when `current_dir()` itself errors, which is rare). An empty
/// string is a safe sentinel — the Python side's prewarm integration
/// treats a missing/empty `VOICE_TYPER_PREWARM_EXE` as "prewarm
/// disabled" (it checks `Path(exe).is_file()` before spawning), so no
/// crash follows. A warning is logged in that case so the developer
/// knows prewarm is disabled in this dev session.
pub(super) fn dev_prewarm_exe() -> String {
    let triple = current_target_triple();
    let suffix = if cfg!(windows) { ".exe" } else { "" };
    let name = format!("prewarm-{}{}", triple, suffix);
    match std::env::current_dir() {
        Ok(cwd) => cwd
            .join("voice_typer")
            .join("server")
            .join("native")
            .join(name)
            .to_string_lossy()
            .to_string(),
        Err(e) => {
            log::warn!(
                "[SIDECAR-DEV] could not resolve cwd for VOICE_TYPER_PREWARM_EXE (prewarm disabled): {}",
                e
            );
            String::new()
        }
    }
}

pub(crate) fn prewarm_resource_path(app: &tauri::AppHandle) -> Result<String, String> {
    let resource = app
        .path()
        .resource_dir()
        .map_err(|e| format!("resource_dir failed: {e}"))?;
    // ADR-0020 §4.1: target triple suffix on the binary name.
    let triple = current_target_triple();
    let suffix = if cfg!(windows) { ".exe" } else { "" };
    let name = format!("prewarm-{}{}", triple, suffix);
    Ok(resource.join(name).to_string_lossy().to_string())
}
