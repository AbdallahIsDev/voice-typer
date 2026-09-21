
use std::path::Path;

pub(crate) fn open_path_in_file_manager(path: &Path) -> Result<(), String> {
    preflight_path_exists(path)?;

    #[cfg(target_os = "windows")]
    {
        let mut child = std::process::Command::new("explorer.exe")
            .arg(path)
            .spawn()
            .map_err(|e| format!("explorer.exe spawn failed: {e}"))?;
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
/// predecessor routed every external link through `shell.openExternal` with
/// a deny-rest policy (`windows/input-nav-guard.ts`: https only, the
/// renderer's `target="_blank"` / `window.open` calls are intercepted).
/// The Tauri host sets `plugins.shell.open = false` (C-TAURI-2) and the
/// webview CSP is `default-src 'self'`, so a renderer-side
/// `window.open` is either blocked or traps the page inside the app,
/// leaving every help / feedback / changelog / share link dead. This
/// helper is the replacement route, and it keeps the same
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

pub(crate) fn preflight_path_exists(path: &Path) -> Result<(), String> {
    if !path.exists() {
        return Err(format!("path does not exist: {}", path.display()));
    }
    Ok(())
}

#[cfg(test)]
#[path = "open_path_tests.rs"]
mod open_path_tests;
