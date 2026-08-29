//! Native OS-surface commands: `open_logs` (OS file manager) and
//! `open_model_import_dialog` (native folder picker). Both are
//! main-window-only commands that hand control to an OS-provided UI
//! surface, so they are grouped by that concern.

use serde_json::{json, Value};
use tauri_plugin_dialog::DialogExt;
use tokio::sync::oneshot;

use crate::commands::require_main_window;
use crate::error::VoiceTyperError;
use crate::platform::open_path::open_path_in_file_manager;
use crate::platform::paths::config_dir;

/// Open the Voice Typer log directory in the OS file manager.
///
/// Mirrors the Electron `window:open-logs` IPC handler in
/// `voice_typer/client/src/main/ipc/window-handlers.ts:57-76` which
/// calls `shell.openPath(logDir)`. The Tauri path uses the OS-native
/// open command via `std::process::Command`:
/// - Windows: `explorer.exe <path>`
/// - macOS:   `open <path>`
/// - Linux:   `xdg-open <path>`
///
/// The path is the same `config_dir` used by the Python sidecar's
/// `_paths.config_dir()` resolution (see `platform::paths::config_dir`)
/// — under Tauri the logs live under `<config_dir>/voice-typer/` (NOT
/// the Electron `~/.voice-typer/` path the old handler used, because
/// the Tauri host writes to the platform-canonical config dir per
/// ADR-0020 §8).
///
/// The response no longer includes the `path` field — the absolute
/// path can contain the user's home directory / username (PII leak in
/// shared logs / crash reports), and no renderer call site consumes it
/// (`window-namespace.ts::openLogs` strips `path` before returning to
/// the React layer). Returns `{"success": true}` on success or
/// `{"success": false, "error": "<msg>"}` on failure.
///
/// `window` is auto-injected by Tauri at runtime — the renderer's
/// `invoke('open_logs')` call is unchanged.
/// `require_main_window(&window)?` runs FIRST so a compromised bubble
/// renderer cannot trigger OS file-manager opens.
#[tauri::command]
pub async fn open_logs(
    _app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, VoiceTyperError> {
    require_main_window(&window)?;
    let log_dir = config_dir();
    // Offload the synchronous fs mkdir + the OS file-manager spawn to
    // the dedicated blocking-thread pool so this `async fn` does not
    // hold a Tauri async-runtime worker thread for the duration of the
    // mkdir syscall (which can stall >100ms under a contended disk /
    // antivirus scan on Windows). Mirrors the pattern already used by
    // `migrate::mod::migrate_electron_userdata` and
    // `sidecar::supervisor::restart_loop`. `spawn_blocking` moves the
    // closure to the cached blocking pool; `.await` yields the calling
    // task until the closure completes.
    //
    // `open_path_in_file_manager` is also moved into the closure
    // because it does its own `path.exists()` syscall + spawns an
    // OS-binary child (explorer.exe / open / xdg-open) — both are
    // blocking work that should NOT run on the async worker pool.
    // The closure returns a single `Result<(), String>` so we can
    // uniformly shape both the mkdir failure and the open failure into
    // the same `{"success": false, "error": "<msg>"}` envelope.
    let blocking_result = tauri::async_runtime::spawn_blocking(move || {
        // Capture mkdir failure rather than silently discarding it
        // with `let _ = ...`. If the config_dir is unwritable
        // (permission denied, read-only mount, etc.), returning
        // `Ok(())` based solely on whether `Command::spawn()` later
        // succeeded would let the OS file manager pop a "path not
        // found" dialog to the user while the UI showed "logs opened".
        // We surface the mkdir failure as a structured error string so
        // the renderer can display it.
        if let Err(e) = std::fs::create_dir_all(&log_dir) {
            return Err(format!(
                "create_dir_all({}) failed: {}",
                log_dir.display(),
                e
            ));
        }
        open_path_in_file_manager(&log_dir)
    })
    .await;
    match blocking_result {
        Ok(Ok(())) => Ok(json!({"success": true})),
        Ok(Err(e)) => Ok(json!({"success": false, "error": e})),
        Err(join_err) => Ok(json!({
            "success": false,
            "error": format!("open_logs blocking task failed: {join_err}")
        })),
    }
}

/// Open a native folder picker for importing HuggingFace model cache
/// directories. The user picks a folder; the renderer then passes the
/// path to `dispatch({cmd:'import_model', data:{path}})` to trigger
/// the Python sidecar's import flow.
///
/// Mirrors the Electron `model:import-dialog` IPC handler in
/// `voice_typer/client/src/main/ipc/window-handlers.ts:81-90` which
/// calls `dialog.showOpenDialog({properties: ["openDirectory"]})`.
/// The Tauri path uses `tauri-plugin-dialog`'s folder-picker API.
///
/// Returns `{"canceled": true}` if the user dismissed the dialog, or
/// `{"canceled": false, "path": "<folder>"}` on success. Matches the
/// Electron handler's shape so `Models.tsx`'s import handler is
/// unchanged on both runtimes.
///
/// `window` is auto-injected by Tauri at runtime — the renderer's
/// `invoke('open_model_import_dialog')` call is unchanged.
/// `require_main_window(&window)?` runs FIRST so a compromised bubble
/// renderer cannot trigger a folder-picker dialog.
#[tauri::command]
pub async fn open_model_import_dialog(
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, VoiceTyperError> {
    require_main_window(&window)?;
    // Use the async folder-pick pattern instead of blocking.
    // tauri-plugin-dialog v2.7.2's `pick_folder()` is callback-based
    // (not async), so we bridge it via a oneshot channel.
    let (tx, rx) = oneshot::channel();
    app.dialog()
        .file()
        .set_title("Select Model Folder")
        .pick_folder(move |f| {
            let _ = tx.send(f);
        });
    let file_path = rx.await.unwrap_or(None);
    let path = match file_path {
        Some(fp) => fp.into_path().map_err(|e| format!("invalid path: {e}"))?,
        None => return Ok(json!({"canceled": true})),
    };
    Ok(json!({
        "canceled": false,
        "path": path.to_string_lossy().to_string(),
    }))
}
