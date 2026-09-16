//! Per-OS "open path in file manager" dispatch ().
//!
//! Previously this code lived in ``commands/system_cmds.rs`` alongside
//! Tauri command facades (``open_logs``, ``open_model_import_dialog``,
//! …). That mixed two concerns:
//!
//! - The ``commands::`` layer is the Tauri ``#[tauri::command]`` facade —
//!   argument deserialization, ``require_main_window`` guards, response
//!   envelope shaping (``{"success": bool, "error": str}``).
//! - The ``platform::`` layer is per-OS binary dispatch, choosing
//!   ``explorer.exe`` / ``open`` / ``xdg-open`` based on
//!   ``#[cfg(target_os = ...)]``.
//!
//! ``platform/`` already hosts ``paths.rs`` (per-OS config-dir
//! resolution) and ``logging.rs`` (per-OS file logging), this module
//! is the natural home for per-OS file-manager dispatch.
//!
//! pre-flight existence check
//!
//! The prior implementation returned ``Ok(())`` based solely on whether
//! ``Command::spawn()`` succeeded. It did NOT verify the path existed,
//! did NOT wait for the child, did NOT check exit status. Triple
//! failure mode: (a) config_dir unwritable, (b) mkdir silently failed,
//! (c) explorer.exe spawns and shows "path not found" to the user
//! while ``open_logs`` returned ``{"success": true}``.
//!
//! We now pre-check ``path.exists()`` BEFORE spawning the OS binary
//! and surface a clear error string if the path is missing. The
//! ``spawn()`` is fire-and-forget from the CALLER's perspective (the
//! Tauri command thread does NOT block on ``.wait()``, file-manager
//! binaries self-detach and waiting would block for the lifetime of
//! the file-manager window, which the user might keep open for hours).
//! The spawned ``Child`` handle is moved into a tiny detached reaper
//! thread that calls ``.wait()`` in the background, this reaps the
//! zombie PID (the OS keeps the child in the process table until a
//! parent ``wait()``s on it) without blocking the command thread.

use std::path::Path;

/// Open a filesystem path in the OS-native file manager. Best-effort:
/// returns an error string on failure (the caller surfaces it to the
/// UI). Mirrors Electron's ``shell.openPath()`` semantics.
///
/// Sibling helpers in this module: [`open_external_url`] (https-only, the
/// replacement for Electron's ``shell.openExternal``) and
/// [`reveal_path_in_file_manager`] (Electron's
/// ``shell.showItemInFolder``).
///
/// pre-flight
///
/// Returns ``Err`` if ``path`` does not exist, the OS binary would
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
        // handle leak (not a zombie, Windows reaps via reference
        // counting on the handle), but on POSIX it's a true zombie
        // that lingers until the host exits. Spawning a tiny reaper
        // thread that calls `wait()` ensures the child is reaped
        // promptly on ALL platforms. The thread itself is ~8 KB of
        // stack and exits as soon as the child does, negligible cost
        // for a user-initiated "open logs" action.
        std::thread::spawn(move || {
            let _ = child.wait();
        });
        Ok(())
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
        Err(
            "unsupported platform: open_path is only implemented for Windows / macOS / Linux"
                .to_string(),
        )
    }
}

/// Open an https URL in the user's default browser.
///
/// Electron routed every external link through `shell.openExternal` with
/// a deny-rest policy (`windows/input-nav-guard.ts`: https only, the
/// renderer's `target="_blank"` / `window.open` calls are intercepted).
/// The Tauri host sets `plugins.shell.open = false` (C-TAURI-2) and the
/// webview CSP is `default-src 'self'`, so a renderer-side
/// `window.open` is either blocked or traps the page inside the app,
/// leaving every help / feedback / changelog / share link dead. This
/// helper is the replacement route (MO-118), and it keeps the same
/// https-only contract: anything else is refused BEFORE any OS binary is
/// spawned, so a compromised renderer cannot hand `file://` or a custom
/// scheme to the shell.
///
/// Per-OS dispatch mirrors [`open_path_in_file_manager`]:
/// - Windows: `explorer.exe <url>` (the shell's own URL handler; passing
///   the URL as a single argv element avoids any `cmd.exe` quoting /
///   injection surface).
/// - macOS: `open <url>`.
/// - Linux: `xdg-open <url>`.
///
/// The spawned child is reaped by the same detached waiter thread (see
/// the per-OS branches below for the zombie rationale).
pub(crate) fn open_external_url(url: &str) -> Result<(), String> {
    let trimmed = url.trim();
    if !is_allowed_external_url(trimmed) {
        return Err(format!(
            "refusing to open non-https URL: {}",
            sanitize_for_error(trimmed)
        ));
    }

    #[cfg(target_os = "windows")]
    {
        let mut child = std::process::Command::new("explorer.exe")
            .arg(trimmed)
            .spawn()
            .map_err(|e| format!("explorer.exe spawn failed: {e}"))?;
        std::thread::spawn(move || {
            let _ = child.wait();
        });
        return Ok(());
    }
    #[cfg(target_os = "macos")]
    {
        let mut child = std::process::Command::new("open")
            .arg(trimmed)
            .spawn()
            .map_err(|e| format!("open spawn failed: {e}"))?;
        std::thread::spawn(move || {
            let _ = child.wait();
        });
        return Ok(());
    }
    #[cfg(target_os = "linux")]
    {
        let mut child = std::process::Command::new("xdg-open")
            .arg(trimmed)
            .spawn()
            .map_err(|e| format!("xdg-open spawn failed: {e}"))?;
        std::thread::spawn(move || {
            let _ = child.wait();
        });
        return Ok(());
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        let _ = trimmed;
        Err("unsupported platform: open_external_url is only implemented for Windows / macOS / Linux".to_string())
    }
}

/// https-only predicate for [`open_external_url`], mirroring Electron's
/// `input-nav-guard.ts` deny-rest policy. Pure so the security contract
/// is unit-testable without spawning anything.
///
/// Accepted: `https://` (case-insensitive scheme) followed by a non-empty
/// host. Rejected: every other scheme (`http:`, `file:`, `javascript:`,
/// `data:`, `mailto:`, custom app schemes), protocol-relative `//host`,
/// relative paths, and anything with control characters or whitespace
/// inside (defense against argument smuggling into the OS handler).
pub(crate) fn is_allowed_external_url(url: &str) -> bool {
    let lowered = url.to_ascii_lowercase();
    if !lowered.starts_with("https://") {
        return false;
    }
    let rest = &url["https://".len()..];
    if rest.is_empty() || rest.starts_with('/') {
        return false;
    }
    // No whitespace or control characters anywhere (a URL never needs
    // them unencoded; they only help smuggle extra argv/registry input).
    !url.chars().any(|c| c.is_whitespace() || c.is_control())
}

/// Cap an arbitrary string for inclusion in an error message: the URL a
/// compromised renderer passes must not be able to inflate the log line.
fn sanitize_for_error(value: &str) -> String {
    const MAX: usize = 120;
    let collapsed: String = value
        .chars()
        .filter(|c| !c.is_control())
        .take(MAX)
        .collect();
    if value.chars().count() > MAX {
        format!("{collapsed}…")
    } else {
        collapsed
    }
}

/// Reveal a file in the OS file manager (select it where the platform
/// supports it), used by the Analytics share-image "Show in folder"
/// action (MO-120b). Mirrors Electron's
/// `shell.showItemInFolder(path)`.
///
/// - Windows: `explorer.exe /select,<path>` (Explorer selects the file).
/// - macOS: `open -R <path>` (Reveal in Finder).
/// - Linux: `xdg-open <parent dir>` (no portable select-in-folder API).
///
/// Returns an error string when the path does not exist, so the caller
/// can surface a structured failure instead of silently doing nothing.
pub(crate) fn reveal_path_in_file_manager(path: &Path) -> Result<(), String> {
    preflight_path_exists(path)?;

    #[cfg(target_os = "windows")]
    {
        // `/select,` must be a single argv element immediately followed
        // by the path (Explorer's documented multi-arg form).
        let select_arg = format!("/select,{}", path.display());
        let mut child = std::process::Command::new("explorer.exe")
            .arg(select_arg)
            .spawn()
            .map_err(|e| format!("explorer.exe spawn failed: {e}"))?;
        std::thread::spawn(move || {
            let _ = child.wait();
        });
        return Ok(());
    }
    #[cfg(target_os = "macos")]
    {
        let mut child = std::process::Command::new("open")
            .arg("-R")
            .arg(path)
            .spawn()
            .map_err(|e| format!("open -R spawn failed: {e}"))?;
        std::thread::spawn(move || {
            let _ = child.wait();
        });
        return Ok(());
    }
    #[cfg(target_os = "linux")]
    {
        let parent = path.parent().unwrap_or(path);
        return open_path_in_file_manager(parent);
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        let _ = path;
        Err("unsupported platform: reveal is only implemented for Windows / macOS / Linux".to_string())
    }
}

/// Pure pre-flight existence check for [`open_path_in_file_manager`].
/// Returns `Err("path does not exist: ...")` for a missing path so
/// the caller can surface a structured error instead of spawning the
/// OS binary and popping a "path not found" dialog.
///
/// Extracted as a spawn-free helper so unit tests can pin the
/// contract WITHOUT launching the OS file manager, the previous
/// `test_open_path_accepts_existing_path` test called
/// `open_path_in_file_manager` on the temp dir, which spawned
/// `explorer.exe` and opened a real file-explorer window on the
/// developer's machine on every `cargo test` run on Windows.
pub(crate) fn preflight_path_exists(path: &Path) -> Result<(), String> {
    if !path.exists() {
        return Err(format!("path does not exist: {}", path.display()));
    }
    Ok(())
}

#[cfg(test)]
#[path = "open_path_tests.rs"]
mod open_path_tests;
