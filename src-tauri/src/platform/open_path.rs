//! Per-OS "open path in file manager" dispatch (AC-105).
//!
//! Previously this code lived in ``commands/system_cmds.rs`` alongside
//! Tauri command facades (``open_logs``, ``open_host_logs``,
//! ``open_model_import_dialog``, …). That mixed two concerns:
//!
//! - The ``commands::`` layer is the Tauri ``#[tauri::command]`` facade —
//!   argument deserialization, ``require_main_window`` guards, response
//!   envelope shaping (``{"success": bool, "error": str}``).
//! - The ``platform::`` layer is per-OS binary dispatch — choosing
//!   ``explorer.exe`` / ``open`` / ``xdg-open`` based on
//!   ``#[cfg(target_os = ...)]``.
//!
//! ``platform/`` already hosts ``paths.rs`` (per-OS config-dir
//! resolution) and ``logging.rs`` (per-OS file logging) — this module
//! is the natural home for per-OS file-manager dispatch.
//!
//! # AC-34: pre-flight existence check
//!
//! The prior implementation returned ``Ok(())`` based solely on whether
//! ``Command::spawn()`` succeeded — it did NOT verify the path existed,
//! did NOT wait for the child, did NOT check exit status. Triple
//! failure mode: (a) config_dir unwritable, (b) mkdir silently failed,
//! (c) explorer.exe spawns and shows "path not found" to the user
//! while ``open_logs`` returned ``{"success": true}``.
//!
//! We now pre-check ``path.exists()`` BEFORE spawning the OS binary
//! and surface a clear error string if the path is missing. The
//! ``spawn()`` is still fire-and-forget (we do NOT ``.wait()`` for
//! the child) — file-manager binaries self-detach and waiting would
//! block the Tauri command thread for the lifetime of the file
//! manager window (which the user might keep open for hours).

use std::path::Path;

/// Open a filesystem path in the OS-native file manager. Best-effort:
/// returns an error string on failure (the caller surfaces it to the
/// UI). Mirrors Electron's ``shell.openPath()`` semantics.
///
/// # AC-34 pre-flight
///
/// Returns ``Err`` if ``path`` does not exist — the OS binary would
/// otherwise spawn and pop a "path not found" dialog to the user
/// while the caller believed the open succeeded.
pub(crate) fn open_path_in_file_manager(path: &Path) -> Result<(), String> {
    // AC-34: pre-check existence BEFORE spawning the OS binary so a
    // missing path surfaces as a structured error string (which the
    // caller puts in the ``{"success": false, "error": ...}`` envelope)
    // rather than a silent Ok(()) followed by an OS error dialog.
    if !path.exists() {
        return Err(format!(
            "path does not exist: {}",
            path.display()
        ));
    }

    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer.exe")
            .arg(path)
            .spawn()
            .map_err(|e| format!("explorer.exe spawn failed: {e}"))?;
        return Ok(());
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(path)
            .spawn()
            .map_err(|e| format!("open spawn failed: {e}"))?;
        return Ok(());
    }
    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(path)
            .spawn()
            .map_err(|e| format!("xdg-open spawn failed: {e}"))?;
        return Ok(());
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        let _ = path;
        Err("unsupported platform: open_path is only implemented for Windows / macOS / Linux".to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // AC-34: pre-flight existence check rejects a missing path with a
    // structured error string. The error is what the caller puts in the
    // ``{"success": false, "error": ...}`` envelope — without this
    // check, the OS binary would spawn and pop a "path not found"
    // dialog to the user while ``open_logs`` believed the open
    // succeeded.
    #[test]
    fn test_open_path_rejects_missing_path() {
        let missing = std::env::temp_dir().join(format!(
            "voice-typer-ac34-missing-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        // Sanity: the path really doesn't exist.
        assert!(!missing.exists());
        let err = open_path_in_file_manager(&missing).unwrap_err();
        assert!(
            err.contains("path does not exist"),
            "expected 'path does not exist' in error, got: {err}"
        );
    }

    // AC-34: an existing path is accepted (the OS-binary spawn is
    // platform-gated, so we can't easily assert success here without
    // depending on a file manager being installed in CI — but the
    // pre-flight existence check is the AC-34 contract, and that's
    // what we exercise). The temp dir always exists.
    #[test]
    fn test_open_path_accepts_existing_path() {
        let existing = std::env::temp_dir();
        assert!(existing.exists());
        // We don't assert Ok(()) — on a headless CI runner xdg-open /
        // explorer.exe / open may not be installed. The contract we
        // test is "the pre-flight existence check does NOT reject an
        // existing path" — i.e. the error (if any) is NOT
        // "path does not exist".
        match open_path_in_file_manager(&existing) {
            Ok(()) => {}
            Err(e) => {
                assert!(
                    !e.contains("path does not exist"),
                    "pre-flight rejected an existing path: {e}"
                );
            }
        }
    }
}
