//! Windows toast-identity registration (AUMID).
//!
//! `tauri-plugin-notification` sends Windows toasts under the app
//! identifier from `tauri.conf.json` (`com.voicetyper.desktop`) used as the
//! toast's AppUserModelId. If that AUMID is not registered with Windows, the
//! toast is attributed to whatever process identity the shell can resolve —
//! in dev that is Windows PowerShell (the terminal that launched
//! `cargo tauri dev`) — and the toast plays no notification sound.
//!
//! Registering the AUMID per-user under
//! `HKCU\Software\Classes\AppUserModelId\<identifier>` with `DisplayName`
//! and `IconUri` makes every toast (dev AND installed) show the Voice Typer
//! name + icon and play the default notification sound. Idempotent: the key
//! is (re)written on each launch so icon path changes self-heal.

use tauri::{AppHandle, Manager};

/// The registry values written for the AUMID key.
#[cfg(windows)]
const ICON_BYTES: &[u8] = include_bytes!("../icons/icon.ico");

/// Register the app's AUMID for toast identity. No-op on non-Windows.
pub fn register(app: &AppHandle) {
    #[cfg(windows)]
    if let Err(e) = register_windows(app) {
        log::warn!("[AUMID] toast identity registration failed (notifications may show a generic host name): {e}");
    }
    #[cfg(not(windows))]
    let _ = app;
}

#[cfg(windows)]
fn register_windows(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    use std::io::Write;

    use winreg::enums::{HKEY_CURRENT_USER, KEY_SET_VALUE};
    use winreg::RegKey;

    let identifier = app.config().identifier.clone();
    let display_name = app
        .config()
        .product_name
        .clone()
        .unwrap_or_else(|| identifier.clone());

    // IconUri must be an absolute on-disk path; copy the embedded .ico into
    // the per-user config dir so it exists regardless of install/dev layout.
    let config_dir = app.path().app_config_dir()?;
    std::fs::create_dir_all(&config_dir)?;
    let icon_path = config_dir.join("toast-icon.ico");
    if !icon_path.exists()
        || std::fs::metadata(&icon_path).map(|m| m.len() != ICON_BYTES.len() as u64).unwrap_or(true)
    {
        let mut f = std::fs::File::create(&icon_path)?;
        f.write_all(ICON_BYTES)?;
    }

    let key_path = format!(r"Software\Classes\AppUserModelId\{identifier}");
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let (key, _) = hkcu.create_subkey_with_flags(&key_path, KEY_SET_VALUE)?;
    key.set_value("DisplayName", &display_name)?;
    key.set_value("IconUri", &icon_path.to_string_lossy().to_string())?;
    log::info!("[AUMID] registered '{identifier}' (DisplayName='{display_name}', IconUri={})", icon_path.display());
    Ok(())
}
