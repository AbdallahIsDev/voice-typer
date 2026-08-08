//! Per-OS "open path in file manager" dispatch ().
//!
//! Previously this code lived in ``commands/system_cmds.rs`` alongside
//! Tauri command facades (``open_logs``, ``open_model_import_dialog``,
//! …). That mixed two concerns:
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
//! pre-flight existence check
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
//! ``spawn()`` is fire-and-forget from the CALLER's perspective (the
//! Tauri command thread does NOT block on ``.wait()`` — file-manager
//! binaries self-detach and waiting would block for the lifetime of
//! the file-manager window, which the user might keep open for hours).
//! The spawned ``Child`` handle is moved into a tiny detached reaper
//! thread that calls ``.wait()`` in the background — this reaps the
//! zombie PID (the OS keeps the child in the process table until a
//! parent ``wait()``s on it) without blocking the command thread.

use std::path::Path;

/// Open a filesystem path in the OS-native file manager. Best-effort:
/// returns an error string on failure (the caller surfaces it to the
/// UI). Mirrors Electron's ``shell.openPath()`` semantics.
///
/// pre-flight
///
/// Returns ``Err`` if ``path`` does not exist — the OS binary would
/// otherwise spawn and pop a "path not found" dialog to the user
/// while the caller believed the open succeeded.
pub(crate) fn open_path_in_file_manager(path: &Path) -> Result<(), String> {
    // pre-check existence BEFORE spawning the OS binary so a
    // missing path surfaces as a structured error string (which the
    // caller puts in the ``{"success": false, "error": ...}`` envelope)
    // rather than a silent Ok(()) followed by an OS error dialog.
    // The check lives in the pure [`preflight_path_exists`] helper so
    // unit tests can pin it WITHOUT spawning the OS file manager.
    preflight_path_exists(path)?;

    #[cfg(target_os = "windows")]
    {
        let mut child = std::process::Command::new("explorer.exe")
            .arg(path)
            .spawn()
            .map_err(|e| format!("explorer.exe spawn failed: {e}"))?;
        // Reap the zombie: the spawned `Child` handle owns the OS
        // process handle. If we let it drop without `wait()`, the OS
        // keeps the process entry in the kernel's process table until
        // a parent `wait()`s on it. On Windows this manifests as a
        // handle leak (not a zombie — Windows reaps via reference
        // counting on the handle), but on POSIX it's a true zombie
        // that lingers until the host exits. Spawning a tiny reaper
        // thread that calls `wait()` ensures the child is reaped
        // promptly on ALL platforms. The thread itself is ~8 KB of
        // stack and exits as soon as the child does — negligible cost
        // for a user-initiated "open logs" action.
        std::thread::spawn(move || {
            let _ = child.wait();
        });
        return Ok(());
    }
    #[cfg(target_os = "macos")]
    {
        let mut child = std::process::Command::new("open")
            .arg(path)
            .spawn()
            .map_err(|e| format!("open spawn failed: {e}"))?;
        // See the Windows branch for the zombie-reaping rationale.
        std::thread::spawn(move || {
            let _ = child.wait();
        });
        return Ok(());
    }
    #[cfg(target_os = "linux")]
    {
        let mut child = std::process::Command::new("xdg-open")
            .arg(path)
            .spawn()
            .map_err(|e| format!("xdg-open spawn failed: {e}"))?;
        // `xdg-open` is typically a shell script that forks a
        // desktop-specific binary (e.g. `gio open`, `dbus-send`) and
        // exits. The direct child we spawned (the `xdg-open` process
        // itself) becomes a zombie the moment it exits, because we
        // own the handle and haven't `wait()`ed on it. Without this
        // reaper thread, each "open logs" click would leak one zombie
        // PID until the host process exits. The reaper thread moves
        // the `Child` handle into a tiny detached thread that blocks
        // on `wait()`, reaping the zombie as soon as `xdg-open` exits
        // (usually <100ms after the file manager launches).
        std::thread::spawn(move || {
            let _ = child.wait();
        });
        return Ok(());
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        let _ = path;
        Err("unsupported platform: open_path is only implemented for Windows / macOS / Linux".to_string())
    }
}

/// Pure pre-flight existence check for [`open_path_in_file_manager`].
/// Returns `Err("path does not exist: ...")` for a missing path so
/// the caller can surface a structured error instead of spawning the
/// OS binary and popping a "path not found" dialog.
///
/// Extracted as a spawn-free helper so unit tests can pin the
/// contract WITHOUT launching the OS file manager — the previous
/// `test_open_path_accepts_existing_path` test called
/// `open_path_in_file_manager` on the temp dir, which spawned
/// `explorer.exe` and opened a real file-explorer window on the
/// developer's machine on every `cargo test` run on Windows.
pub(crate) fn preflight_path_exists(path: &Path) -> Result<(), String> {
    if !path.exists() {
        return Err(format!(
            "path does not exist: {}",
            path.display()
        ));
    }
    Ok(())
}


#[cfg(test)]
#[path = "open_path_tests.rs"]
mod open_path_tests;
