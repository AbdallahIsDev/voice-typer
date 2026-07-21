//! Voice Typer — Tauri v2 host (ADR-0020 implementation)
//!
//! This is the Rust shell that replaces the Electron main process. It:
//!
//! 1. Generates a 256-bit bearer token (`secrets.token_bytes(32)`
//!    equivalent — see `Cargo.toml` note: despite the ADR's "HMAC"
//!    wording, the host uses bearer-token auth, not HMAC) and spawns
//!    the Python sidecar via Tauri's `externalBin` mechanism, passing
//!    `VOICE_TYPER_IPC_TOKEN` + `TAURI_SIDECAR=1` env vars.
//! 2. Reads the sidecar's stdout until it sees the
//!    `{"event":"server_started","port":N}` JSON line, then opens a
//!    WebSocket client to `ws://127.0.0.1:N`.
//! 3. Performs the bearer-token auth handshake (`{"type":"auth","token":...}`).
//! 4. Exposes ONE generic `dispatch` command to the webview:
//!    `invoke('dispatch', {cmd, data})` → Rust forwards it as a WS
//!    frame, awaits the per-id response, returns it.
//! 5. Subscribes to server-initiated events (channel 2) and re-emits
//!    them as Tauri events the React UI already subscribes to.
//! 6. Runs the FT-1 supervisor: on unexpected WS-close / sidecar
//!    exit, respawns with backoff (500ms → 1s → 2s, cap 5 retries),
//!    then falls back to full-app relaunch.
//! 7. Coalesces `bubble_level` events from ~60 Hz to ≤30 Hz to
//!    prevent WebView jank (ADR-0020 §9).
//! 8. Single-instance gate at the top of `main` — runs BEFORE any
//!    sidecar init so a second launch doesn't spawn a zombie sidecar.
//!
//! # Cross-platform
//!
//! - Windows: WebView2 (Chromium-based, system-installed on Win10+).
//! - macOS: WKWebView (Safari-based, system).
//! - Linux: webkit2gtk (system; requires `libwebkit2gtk-4.1-0`).
//!
//! The native hotkey binaries (`windows-key-listener`,
//! `macos-key-listener`, `linux-key-listener`) are NOT spawned by
//! this host — they are spawned by the Python sidecar via the
//! existing `hotkeys.py::create_hotkey_backend()` factory, which
//! preserves ADR-0007 + ADR-0008 (key suppression, Fn/Globe key,
//! Wayland support). Tauri's `global-shortcut` plugin is NOT used
//! for the dictation toggle (see ADR-0020 §6.4).
//!
//! # Module layout
//!
//! This file is **wiring-only** (~200 lines): app builder, plugin
//! registration, `.setup` glue, `generate_handler!` list, and the
//! single-instance gate. All real logic lives in focused modules:
//!
//! - `state` — `SidecarState`, `SidecarHandle`, `PendingMap`, `WsWriterTx`
//! - `util` — ADR-0020 constants, `generate_token`, `hex`, `now_timestamp`
//! - `sidecar::spawn` — spawn variants + stdout handshake (§1 + §4.1 + §14)
//! - `sidecar::ft1` — FT-1 supervisor + bubble coalesce predicate (§9 + §10)
//! - `sidecar::ws` — WebSocket reconnect + reader/writer tasks (§1 + §9 + §10)
//! - `commands::sidecar_cmds` — `dispatch`, `paste_text`, `shutdown_sidecar` (§6.2 + §7 + §10)
//! - `commands::export` — `export_history`, `export_vocabulary`, CSV helpers (§6 + MIG-1.1)
//! - `commands::bubble` — bubble window commands (§9 + MIG-1.2)
//! - `platform::paths` — per-platform config-dir resolution (§8)
//! - `platform::logging` — rotating file logger 5 MB × 5 (§11)

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;
mod migrate;
mod platform;
mod sidecar;
mod state;
mod tray;
mod util;

use std::sync::Arc;

use tauri::{Manager, WindowEvent};

use commands::bubble::{
    bubble_emit_state, bubble_hide_complete, bubble_move_by, bubble_resize,
    bubble_set_draggable, bubble_set_position, bubble_show, bubble_signal_ready,
    bubble_toggle_dictation,
};
use commands::export::{export_history, export_vocabulary};
use commands::sidecar_cmds::{dispatch, paste_text, shutdown_sidecar};
// CR-33: system_cmds exposes open_logs / open_model_import_dialog /
// export_templates / export_config — the 4 window_-namespace commands
// the renderer's `window.window_?` bridge expects (porting the Electron
// `window:open-logs`, `model:import-dialog`, `templates:export`,
// `config:export` IPC handlers).
use commands::system_cmds::{export_config, export_templates, open_logs, open_model_import_dialog};
use platform::logging::init_file_logger;
use platform::paths::config_dir;
use sidecar::ft1::ft1_respawn;
use sidecar::spawn::spawn_sidecar_and_get_port;
use sidecar::ws::reconnect_ws;
use state::SidecarState;
use std::sync::Mutex;
use tokio::sync::Mutex as AsyncMutex;
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64};

// Re-export for the `tauri::generate_handler!` macro. The macro expects
// the command fn names to be in scope at the call site — the `use` above
// handles that. The state struct + atomics are needed for the `.manage()`
// call below.
fn main() {
    // ADR-0020 §11: init the rotating file logger BEFORE the Tauri
    // builder runs so early startup errors are captured. Falls back to
    // `env_logger` (stderr-only) if file init fails — non-fatal.
    let config_dir_path = platform::paths::config_dir_from_env(
        std::env::var("HOME").ok().as_deref(),
        std::env::var("APPDATA").ok().as_deref(),
        std::env::var("XDG_DATA_HOME").ok().as_deref(),
        std::env::var("VOICE_TYPER_CONFIG_DIR").ok().as_deref(),
    );
    if let Err(e) = init_file_logger(&config_dir_path) {
        eprintln!(
            "[MAIN] file logger init failed (falling back to stderr-only env_logger): {}",
            e
        );
        // Best-effort: env_logger for stderr only (no file sink).
        // `try_init` avoids panic if `log::set_logger` was already called.
        let _ = env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info"))
            .format_timestamp_millis()
            .try_init();
    }

    tauri::Builder::default()
        // ADR-0020 §12: single-instance MUST be the FIRST plugin so its
        // duplicate-instance check runs before any sidecar spawn (which
        // would otherwise leave a zombie python process on a double-
        // launch). The plugin's callback focuses the existing main
        // window; the second instance exits immediately after the
        // callback returns. The Python side's `VoiceTyperSingleInstance`
        // Win32 mutex is disabled when `TAURI_SIDECAR=1` is set (see
        // `platform::paths::config_dir` doc comment) so the two gates
        // don't collide.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            // ADR-0020 §12: second launch — focus the existing main window.
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        // MIG-1.1: dialog plugin for export_history / export_vocabulary
        // save-file dialogs (invoked from Rust, not TS).
        .plugin(tauri_plugin_dialog::init())
        .manage(Arc::new(SidecarState {
            child: Mutex::new(None),
            token: Mutex::new(String::new()),
            ws_tx: Mutex::new(None),
            pending: Arc::new(AsyncMutex::new(HashMap::new())),
            next_id: AtomicU64::new(1),
            shutting_down: AtomicBool::new(false),
            respawn_in_progress: AtomicBool::new(false),
            child_exit_rx: AsyncMutex::new(None),
        }))
        // MIG-1.1 + MIG-1.2: register export + bubble commands alongside
        // the existing dispatch/paste_text/shutdown_sidecar.
        .invoke_handler(tauri::generate_handler![
            dispatch,
            paste_text,
            shutdown_sidecar,
            export_history,
            export_vocabulary,
            bubble_show,
            bubble_signal_ready,
            bubble_set_position,
            bubble_set_draggable,
            bubble_move_by,
            bubble_hide_complete,
            // CR-33: bubble window extensions (resize / state / toggle).
            bubble_resize,
            bubble_emit_state,
            bubble_toggle_dictation,
            // CR-33: system-level window_ commands (port of Electron
            // window:open-logs / model:import-dialog / templates:export
            // / config:export IPC handlers).
            open_logs,
            open_model_import_dialog,
            export_templates,
            export_config,
        ])
        .setup(|app| {
            let app_handle = app.handle().clone();
            // ADR-0020 §8: log the resolved config_dir so users/devs
            // can find their logs / history.db / models without reading
            // code. (The same path is used by the Python sidecar via
            // `voice_typer/server/_paths.py`.)
            log::info!(
                "[SETUP] config_dir resolved to: {}",
                config_dir(&app_handle).display()
            );
            // ADR-0020 §8: one-time Electron userData → Tauri config_dir
            // migration, BEFORE the sidecar spawns (so the sidecar boots
            // against already-migrated data and never hits a write
            // conflict / fresh-empty init). Idempotent + non-destructive.
            migrate::migrate_electron_userdata(&app_handle);
            // ADR-0020 §6.5: create the system tray (rendered from the
            // Python sidecar's `tray_menu` events). Failure is
            // non-fatal — the app still runs without a tray.
            if let Err(e) = crate::tray::create_tray(app.handle()) {
                log::error!("[TRAY] init failed: {}", e);
            }
            // Spawn the sidecar + WS bridge in a background tokio task.
            tauri::async_runtime::spawn(async move {
                let state: tauri::State<'_, Arc<SidecarState>> = app_handle.state();
                let state = state.inner().clone();

                let token = util::generate_token();
                *state.token.lock().unwrap() = token.clone();

                match spawn_sidecar_and_get_port(&app_handle, &token).await {
                    Ok((port, child, exit_rx)) => {
                        *state.child.lock().unwrap() = Some(child);
                        // CR-2: store the sidecar's event receiver so
                        // shutdown_sidecar can poll for graceful exit.
                        *state.child_exit_rx.lock().await = exit_rx;
                        if let Err(e) = reconnect_ws(&app_handle, &state, port, &token).await {
                            log::error!("[SETUP] initial WS connect failed: {}", e);
                            let _ = ft1_respawn(&app_handle, &state).await;
                        }
                    }
                    Err(e) => {
                        log::error!("[SETUP] sidecar spawn failed: {}", e);
                        let _ = ft1_respawn(&app_handle, &state).await;
                    }
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            // ADR-0020 §10: on main window close, shutdown the sidecar.
            // CR-24: also handle the bubble window's close so a user
            // dismissing the bubble doesn't leave the sidecar running
            // against a closed webview.
            if let WindowEvent::CloseRequested { .. } = event {
                match window.label() {
                    "main" => {
                        let app = window.app_handle().clone();
                        tauri::async_runtime::spawn(async move {
                            let state: tauri::State<'_, Arc<SidecarState>> = app.state();
                            let _ = shutdown_sidecar(app.clone(), state).await;
                        });
                    }
                    "bubble" => {
                        // Bubble window close — no sidecar shutdown, just log.
                        log::info!("[WINDOW] bubble window closed by user");
                    }
                    _ => {}
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
