//! Share-stats image export command (MO-121): `save_stats_image`.
//!
//! predecessor served the Analytics/Dashboard share image through native
//! main-process handlers (`main/ipc/stats-image-handlers.ts`):
//! instant-save to the OS Downloads folder, a localized native Save-As
//! dialog, and `shell.showItemInFolder` reveal. Only the reveal half
//! existed under Tauri (MO-120b's `reveal_path_command`); Save-As and
//! Downloads fell back to a bare anchor download, which the Tauri
//! webview either blocks (CSP `default-src 'self'`) or saves to an
//! opaque app-internal location with no localized dialog.
//!
//! This command ports the SAVE half. The clipboard half stays on the
//! renderer's web-API `navigator.clipboard` path: it is not ACL-gated,
//! needs no host process, and adding an image-capable clipboard
//! dependency to the host would widen the dependency surface for zero
//! functional gain (see the command doc below).
//!
//! Payload validation mirrors the predecessor handler byte-for-byte:
//! the data URL must be a `data:image/png;base64,...` string, capped
//! at 25 MB (the share image is a fixed 1200×630 card at 2× pixel
//! ratio, typically 1–4 MB; the cap defends against a compromised
//! renderer feeding a multi-GB base64 blob to `BASE64_STANDARD`), and
//! the DECODED bytes must carry the PNG signature (not just the MIME
//! prefix). The filename is sanitized with the same traversal-neutral
//! rules as the predecessor's `safePngFilename`.

use base64::Engine as _;
use serde_json::{json, Value};
use tauri::Manager;
use tauri_plugin_dialog::DialogExt;
use tokio::sync::oneshot;

use crate::commands::export::await_dialog_bridge;
use crate::commands::require_main_window;
use crate::commands::system_cmds::dialog_titles::{localized_title_for, DialogTitle};
use crate::error::VoiceTyperError;
use crate::util::atomic_write_bytes;

/// Cap on the accepted PNG data-URL payload (25 MB, mirrors
/// `MAX_PNG_DATA_URL_BYTES` in the predecessor handler).
pub(crate) const MAX_PNG_DATA_URL_BYTES: usize = 25 * 1024 * 1024;

/// PNG file signature, validated on the DECODED bytes.
pub(crate) const PNG_SIGNATURE: [u8; 4] = [0x89, 0x50, 0x4e, 0x47];

/// The `data:image/png;base64,` prefix the payload must carry.
pub(crate) const PNG_DATA_URL_PREFIX: &str = "data:image/png;base64,";

/// Decode a `data:image/png;base64,...` URL into PNG bytes, validating
/// the MIME prefix, the payload size, and the decoded PNG signature.
///
/// Pure function so the validation contract is unit-testable without a
/// Tauri runtime (mirrors the predecessor's `decodePngDataUrl`).
pub(crate) fn decode_png_data_url(data_url: &str) -> Option<Vec<u8>> {
    let b64 = data_url.strip_prefix(PNG_DATA_URL_PREFIX)?;
    if b64.is_empty() || b64.len() > MAX_PNG_DATA_URL_BYTES {
        return None;
    }
    let bytes = base64::engine::general_purpose::STANDARD.decode(b64).ok()?;
    if bytes.len() < PNG_SIGNATURE.len()
        || bytes[..PNG_SIGNATURE.len()] != PNG_SIGNATURE
    {
        return None;
    }
    Some(bytes)
}

/// Make a filesystem-safe default filename: neutralize path traversal
/// and separators, collapse whitespace, strip a `.png` extension, and
/// guarantee the stem shape. Pure function (mirrors the predecessor's
/// `safePngFilename`).
///
/// The stem (without `.png`) is returned because the save-dialog path
/// passes it to `set_file_name` with the dialog's PNG filter appending
/// the extension, and the Downloads path appends it explicitly.
pub(crate) fn safe_png_stem(raw: Option<&str>) -> String {
    let base = raw.unwrap_or_default();
    // The old handler worked on the raw filename INCLUDING a possible
    // `.png`; mirror that normalization exactly, then peel the suffix.
    let mut sanitized: String = base
        .replace("..", "-")
        .chars()
        .map(|c| match c {
            '\\' | '/' | ':' | '*' | '?' | '"' | '<' | '>' | '|' | '\0' => '-',
            _ => c,
        })
        .collect();
    // Collapse whitespace runs to single dashes (the predecessor's
    // `replace(/\s+/g, "-")`).
    let mut collapsed = String::with_capacity(sanitized.len());
    let mut in_ws = false;
    for c in sanitized.chars() {
        if c.is_whitespace() {
            in_ws = true;
            continue;
        }
        if in_ws {
            collapsed.push('-');
            in_ws = false;
        }
        collapsed.push(c);
    }
    sanitized = collapsed;
    // Strip leading dots/dashes so the result is never a hidden file
    // or a bare `-`-prefixed name.
    let trimmed = sanitized.trim_start_matches(['-', '.']);
    let stem: String = trimmed.chars().take(80).collect();
    // Peel a `.png` extension case-insensitively (the save dialog filter
    // accepts `.PNG` too; the Downloads path always writes lowercase).
    let stem = if stem.len() >= 4 && stem[stem.len() - 4..].eq_ignore_ascii_case(".png") {
        &stem[..stem.len() - 4]
    } else {
        stem.as_str()
    };
    if stem.is_empty() {
        "voice-typer-stats".to_string()
    } else {
        stem.to_string()
    }
}

/// Pick a non-colliding path in `dir`: if `dir/stem.png` exists,
/// append ` (1)`, ` (2)`, … so an instant Downloads save never
/// silently overwrites a previous export. Pure-ish (filesystem
/// `exists` probes only) so it runs on the blocking pool.
fn non_colliding_png_path(dir: &std::path::Path, stem: &str) -> std::path::PathBuf {
    let mut candidate = dir.join(format!("{stem}.png"));
    let mut index = 1;
    while candidate.exists() {
        candidate = dir.join(format!("{stem} ({index}).png"));
        index += 1;
    }
    candidate
}

/// The OS Downloads directory for the instant-save mode.
///
/// `tauri::path::PathResolver::download_dir()` maps to
/// `SHGetKnownFolderPath(FOLDERID_Downloads)` on Windows,
/// `NSSearchPathForDirectoriesInDomains(.downloadsDirectory)` on macOS
/// and `XDG_DOWNLOAD_DIR`/`~/Downloads` on Linux — the same directories
/// the predecessor's `app.getPath("downloads")` resolves to. Falls back to
/// the user's home dir when the OS refuses to name one (headless
/// Linux without XDG user dirs).
fn downloads_dir(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
    app.path()
        .download_dir()
        .map_err(|e| format!("download_dir resolution failed: {e}"))
}

/// Save the share-stats PNG (MO-121).
///
/// Payload: `{ dataUrl: string, defaultName?: string, mode?: "downloads" | "saveAs" }`.
///
/// - `mode: "downloads"` (default): instant-save to the OS Downloads
///   folder with a non-colliding name, no dialog.
/// - `mode: "saveAs"`: localized native save dialog (title key
///   `dialog.export.statsImage`, byte-identical to the predecessor
///   handler's `mainT("dialog.export.statsImage")`).
///
/// Return shapes mirror the predecessor `stats-image:save` handler:
/// success → `{"success": true, "path": "<file>"}`; a canceled dialog
/// → `{"success": false, "canceled": true}` (silent no-op on the
/// renderer side); invalid payload / write failure →
/// `{"success": false, "error": "<msg>"}`.
///
/// `window` is auto-injected by Tauri; `require_main_window` runs
/// FIRST so a compromised bubble renderer cannot write files or open
/// save dialogs.
#[tauri::command]
pub async fn save_stats_image(
    payload: Value,
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, VoiceTyperError> {
    require_main_window(&window)?;

    let data_url = payload
        .get("dataUrl")
        .and_then(Value::as_str)
        .ok_or_else(|| "Invalid PNG data".to_string())?;
    let png = decode_png_data_url(data_url)
        .ok_or_else(|| "Invalid PNG data".to_string())?;
    let stem = safe_png_stem(payload.get("defaultName").and_then(Value::as_str));
    let save_as = payload.get("mode").and_then(Value::as_str) == Some("saveAs");

    if !save_as {
        // Instant save to the OS Downloads folder, no dialog. The
        // collision probe + atomic write are blocking filesystem work.
        let dir = downloads_dir(&app)?;
        let result = tauri::async_runtime::spawn_blocking(
            move || -> Result<std::path::PathBuf, String> {
                let target = non_colliding_png_path(&dir, &stem);
                atomic_write_bytes(&target, &png)
                    .map_err(|e| format!("write failed: {e}"))?;
                Ok(target)
            },
        )
        .await
        .map_err(|e| format!("save_stats_image task failed: {e}"))?;
        return match result {
            Ok(path) => Ok(json!({
                "success": true,
                "path": path.to_string_lossy(),
            })),
            Err(e) => Ok(json!({"success": false, "error": e})),
        };
    }

    // Localized Save-As dialog (same await_dialog_bridge bounded-await
    // pattern as export_data: a dialog whose callback never fires
    // resolves as canceled instead of parking the command future).
    let title = localized_title_for(DialogTitle::ExportStatsImage, &app);
    let (tx, rx) = oneshot::channel();
    app.dialog()
        .file()
        .set_title(title)
        .add_filter("PNG", &["png"])
        .set_file_name(format!("{stem}.png"))
        .save_file(move |f| {
            let _ = tx.send(f);
        });
    let file_path = await_dialog_bridge(rx).await;
    let Some(file_path) = file_path else {
        return Ok(json!({"success": false, "canceled": true}));
    };
    let path = file_path
        .into_path()
        .map_err(|e| format!("invalid path: {e}"))?;
    let result = tauri::async_runtime::spawn_blocking(
        move || -> Result<std::path::PathBuf, String> {
            atomic_write_bytes(&path, &png).map_err(|e| format!("write failed: {e}"))?;
            Ok(path)
        },
    )
    .await
    .map_err(|e| format!("save_stats_image task failed: {e}"))?;
    match result {
        Ok(path) => Ok(json!({
            "success": true,
            "path": path.to_string_lossy(),
        })),
        Err(e) => Ok(json!({"success": false, "error": e})),
    }
}

// Unit tests for the pure validation/sanitization core live in the
// sibling `stats_image_tests.rs` file (C-TEST-5: no inline test code in
// production source).
#[cfg(test)]
#[path = "stats_image_tests.rs"]
mod stats_image_tests;
