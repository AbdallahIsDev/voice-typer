//! Process-wide decoded-tray-icon cache + whitelisted icon loader.

use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};
use tauri::image::Image;
use tauri::{AppHandle, Manager};

// Cache decoded tray icons by logical name so tray_state flips do not
// re-read + re-decode the PNG each time. Mutex (not RwLock): short
// critical section; I/O happens outside the lock on a miss.
static TRAY_ICON_CACHE: OnceLock<Mutex<HashMap<String, Image<'static>>>> = OnceLock::new();

/// Whitelisted tray icon names (`idle`, `recording`, `transcribing`,
/// `error`). Blocks a compromised sidecar from reading arbitrary files
/// via `icon: "../../../etc/passwd"`. Names MUST match
/// `generate-icons.mjs` output under `src-tauri/icons/tray/`.
pub(crate) fn is_allowed_icon_name(name: &str) -> bool {
    matches!(name, "idle" | "recording" | "transcribing" | "error")
}

/// Map a logical sidecar icon name to a bundled Tauri image. `None` if
/// unknown/missing (non-fatal: tray icon left unchanged). Cache hit clones
/// the Arc-backed Image; miss reads/decodes outside the lock.
pub(crate) fn load_tray_icon(app: &AppHandle, name: &str) -> Option<Image<'static>> {
    // Never load an arbitrary path from the sidecar.
    if !is_allowed_icon_name(name) {
        log::warn!("[TRAY] ignoring unknown tray_state icon name: {:?}", name);
        return None;
    }
    let allowed = name;

    let cache = TRAY_ICON_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    if let Ok(guard) = cache.lock() {
        if let Some(img) = guard.get(allowed) {
            return Some(img.clone());
        }
    } else {
        // Poisoned lock: bypass cache; best-effort, not a correctness req.
        log::warn!(
            "[TRAY] icon cache lock poisoned: bypassing cache for {:?}",
            allowed
        );
    }

    // Slow path outside the lock so a slow disk doesn't block cache hits.
    let resource_dir = app.path().resource_dir().ok()?;
    let path = resource_dir
        .join("icons")
        .join("tray")
        .join(format!("{}.png", allowed));
    let bytes = std::fs::read(&path).ok()?;
    let img = match Image::from_path(&path) {
        Ok(img) => img,
        Err(e) => {
            log::warn!(
                "[TRAY] failed to decode tray icon {:?} ({} bytes from {}): {}",
                allowed,
                bytes.len(),
                path.display(),
                e
            );
            return None;
        }
    };

    if let Ok(mut guard) = cache.lock() {
        guard.insert(allowed.to_string(), img.clone());
    }
    Some(img)
}
