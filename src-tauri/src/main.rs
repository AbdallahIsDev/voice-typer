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
//! This file is **wiring-only** (~280 lines): app builder, plugin
//! registration, `.setup` glue, `generate_handler!` list, and the
//! single-instance gate. All real logic lives in focused modules:
//!
//! - `state` — `SidecarState`, `SidecarHandle`, `PendingMap`, `WsWriterTx`,
//!   `shutdown_sidecar_for_exit` (PVT-G5-007)
//! - `util` — ADR-0020 constants, `generate_token`, `hex`, `now_timestamp`
//! - `sidecar::spawn` — spawn variants + stdout handshake (§1 + §4.1 + §14)
//! - `sidecar::supervisor` — FT-1 supervisor + bubble coalesce predicate (§9 + §10)
//! - `sidecar::ws` — WebSocket reconnect + reader/writer tasks + heartbeat (§1 + §9 + §10)
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

// PVT-2 (session 1) + PVT-G5-007 (session 5): need `Listener` for
// `app.listen("relaunch_app", ...)` and `RunEvent` for the
// `.run(callback)` exit handler.
use tauri::{Listener, Manager, RunEvent, WindowEvent};
// PVT-2 completion: the relaunch_app listener sends a `relaunch_ack`
// WS frame back to Python so `_wait_for_relaunch_ack` short-circuits
// cleanly. `Message` is the WS frame type; `json!` builds the frame.
use tokio_tungstenite::tungstenite::Message as WsMessage;
use serde_json::json;

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
use sidecar::supervisor::ft1_respawn;
use sidecar::spawn::spawn_sidecar_and_get_port;
use sidecar::ws::reconnect_ws;
use state::SidecarState;
use std::sync::Mutex;
// PVT-G5-007: SHUTDOWN_ACK_TIMEOUT_MS bounds the force-kill backstop
// wait in the RunEvent::Exit handler. PVT-G5-034: Ordering is used
// for the post-spawn shutting_down re-check.
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Duration;
// PVT-G5-007: SHUTDOWN_ACK_TIMEOUT_MS bounds the force-kill backstop
// wait in the RunEvent::Exit handler.
use util::SHUTDOWN_ACK_TIMEOUT_MS;
use tokio::sync::Mutex as AsyncMutex;
use std::collections::HashMap;

// Re-export for the `tauri::generate_handler!` macro. The macro expects
// the command fn names to be in scope at the call site — the `use` above
// handles that. The state struct + atomics are needed for the `.manage()`
// call below.
fn main() {
    // FA8-retry / PVT-G5-007 / PVT-G5-083: install the panic hook BEFORE
    // the file logger so panics during logger init are still captured
    // (the hook falls back to eprintln if the global logger isn't set
    // yet). The hook is exported by `platform::logging` (added by
    // FA8-retry).
    crate::platform::logging::install_panic_hook();

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
            // PVT-G5-085: log only the basename — the absolute path can
            // contain the user's home directory / username (PII leak in
            // shared logs / crash reports). The full path is still
            // resolved + used by the rest of the setup; we just don't
            // emit it to the log sink.
            log::info!(
                "[SETUP] config_dir resolved to: <redacted>/{}",
                config_dir(&app_handle)
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_else(|| "voice-typer".into())
            );
            // ADR-0020 §8: one-time Electron userData → Tauri config_dir
            // migration, BEFORE the sidecar spawns (so the sidecar boots
            // against already-migrated data and never hits a write
            // conflict / fresh-empty init). Idempotent + non-destructive.
            migrate::migrate_electron_userdata(&app_handle);
            // PVT-2 (session 1): Listen for the `relaunch_app` event
            // (renamed from Python's `relaunch_electron` in
            // sidecar/ws.rs) and trigger a full app restart. Previously
            // this event was emitted into the void — the user's
            // "Restart" click was silently demoted to "respawn the
            // Python backend only" because no Rust listener subscribed
            // to the renamed event.
            //
            // PVT-2 completion (this change): the listener now ALSO
            // sends a `relaunch_ack` WS frame back to the Python
            // sidecar BEFORE calling `app.restart()`. Without this ack,
            // Python's `restart_app` blocks for the full 2s timeout in
            // `_wait_for_relaunch_ack` before calling `sys.exit(0)` —
            // delaying the actual restart by 2s and leaving the React
            // UI in a half-torn-down state (in-memory state survives
            // what should be a full restart). The ack is fire-and-
            // forget: we enqueue the WS frame directly via `try_send`
            // (no pending-entry insert) because Python's
            // `_handle_relaunch_ack` returns None — no response is
            // sent back. A short sleep gives the WS writer task time
            // to flush the frame to the socket before `app.restart()`
            // tears down the process.
            let restart_handle = app.handle().clone();
            app.listen("relaunch_app", move |_event| {
                log::info!(
                    "[RESTART] relaunch_app event received — sending relaunch_ack + calling app.restart()"
                );
                // Best-effort relaunch_ack: lock ws_tx, clone the
                // Sender, drop the guard, then try_send the frame.
                // `try_send` is non-blocking (bounded channel) and
                // safe to call from the Tauri event-loop thread.
                let state: tauri::State<'_, Arc<SidecarState>> =
                    restart_handle.state();
                let state_inner = state.inner().clone();
                let ws_tx_opt = crate::state::lock(&state_inner.ws_tx).clone();
                if let Some(ws_tx) = ws_tx_opt {
                    let id = state_inner.next_id.fetch_add(1, Ordering::SeqCst);
                    let frame = json!({
                        "type": "relaunch_ack",
                        "data": {},
                        "id": id,
                    });
                    match ws_tx.try_send(WsMessage::Text(frame.to_string())) {
                        Ok(_) => log::info!(
                            "[RESTART] relaunch_ack WS frame sent (id={})",
                            id
                        ),
                        Err(e) => log::warn!(
                            "[RESTART] failed to send relaunch_ack WS frame (id={}): {} — Python will wait 2s timeout",
                            id,
                            e
                        ),
                    }
                } else {
                    log::warn!(
                        "[RESTART] ws_tx is None — cannot send relaunch_ack; Python will wait 2s timeout"
                    );
                }
                // Brief yield to let the WS writer task flush the ack
                // frame before app.restart() tears down the process.
                // try_send enqueues on the bounded channel instantly;
                // the writer task (tokio::spawn'd in reconnect_ws)
                // needs only a few microseconds to send it on the
                // socket. 10ms is generous; even on a loaded host the
                // writer task schedules within 1ms.
                std::thread::sleep(std::time::Duration::from_millis(10));
                log::info!("[RESTART] calling app.restart()");
                restart_handle.restart();
            });
            // ADR-0020 §6.5: create the system tray (rendered from the
            // Python sidecar's `tray_menu` events). Failure is
            // non-fatal — the app still runs without a tray.
            if let Err(e) = crate::tray::create_tray(app.handle()) {
                log::error!("[TRAY] init failed: {}", e);
            }
            // PVT-3 (session 1): reset the FT-1 restart counter to 0 at
            // the start of every fresh app launch, BEFORE
            // `spawn_sidecar_and_get_port` (which is called inside the
            // `tauri::async_runtime::spawn` block below). The counter
            // is incremented by `ft1_respawn` on each sidecar-restart
            // attempt and reset to 0 only on successful `reconnect_ws`.
            // Without this reset, 3 consecutive bad cold-starts
            // (transient AV quarantine, slow disk, missing binary on a
            // re-install) brick the install permanently: the 4th launch
            // reads `count: 3`, trips the breaker in `ft1_respawn`,
            // emits `ft1_failed`, and the user has no recovery path
            // short of manually deleting `ft1_restart_counter.json`.
            // Resetting here means the counter only ever counts FT-1
            // retries WITHIN a single session — which is the original
            // CR-29 intent.
            //
            // G4-H-28 (session 4): the counter now also carries a `ts`
            // field so stale counts from prior sessions don't trip the
            // breaker — but we still reset here so the in-session
            // counter starts at 0 (matching the original CR-29 intent).
            crate::sidecar::supervisor::write_ft1_restart_counter(0);
            // Spawn the sidecar + WS bridge in a background tokio task.
            tauri::async_runtime::spawn(async move {
                let state: tauri::State<'_, Arc<SidecarState>> = app_handle.state();
                let state = state.inner().clone();

                let token = util::generate_token();

                match spawn_sidecar_and_get_port(&app_handle, &token).await {
                    Ok((port, child, exit_rx)) => {
                        // PVT-G5-034: re-check shutting_down AFTER spawn
                        // returns — if the user quit the app while we
                        // were waiting for server_started (up to 30s on
                        // a cold start), the `RunEvent::Exit` handler
                        // already set the flag but found no child to
                        // kill (state.child was still None). Kill the
                        // freshly-spawned sidecar here so it doesn't
                        // outlive the host, then bail before installing
                        // it into state (which would fool FT-1 into
                        // thinking the sidecar is healthy).
                        if state.shutting_down.load(Ordering::SeqCst) {
                            log::info!(
                                "[SETUP] shutting_down set during sidecar spawn — \
                                 killing freshly-spawned sidecar"
                            );
                            let _ = child.kill_tree().await;
                            return;
                        }
                        *crate::state::lock(&state.child) = Some(child);
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
                        // PVT-G5-032: on macOS the app stays alive when
                        // the last window closes (standard macOS app
                        // lifecycle — the tray / Dock keeps the process
                        // running). Killing the sidecar here would
                        // orphan the dictation engine while the app is
                        // still alive in the menu bar. Only kill the
                        // sidecar on Windows/Linux where app exit is
                        // bound to last-window-close.
                        if cfg!(target_os = "macos") {
                            return;
                        }
                        let app = window.app_handle().clone();
                        // G4-H-01 (session 4): `shutdown_sidecar` now
                        // takes a `window: tauri::Window` parameter
                        // (CR-5-style main-window guard). Clone the
                        // main window handle here so the spawned task
                        // can pass it through the guard (the label is
                        // "main" so the check passes).
                        let main_window = window.clone();
                        tauri::async_runtime::spawn(async move {
                            let state: tauri::State<'_, Arc<SidecarState>> = app.state();
                            let _ = shutdown_sidecar(app.clone(), state, main_window).await;
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
        // PVT-G5-007 (session 5): split `.run(ctx)` into
        // `.build(ctx)?.run(callback)` so we get a callback for
        // `RunEvent::Exit` / `ExitRequested`. Without this, the sidecar
        // is leaked when the host exits via `app.exit()` / quit-tray /
        // Ctrl-C / SIGTERM — the `on_window_event` close handler only
        // fires on user-initiated window close, NOT on these other
        // exit paths.
        //
        // G4-M-65 (session 4): replace `.expect("error while running
        // tauri application")` with a structured error handler so a
        // tauri::Builder::build failure logs a [FATAL] line to the
        // rotating file logger (already initialized above) AND stderr
        // before exiting with code 1. The prior `.expect()` produced
        // an unstructured panic message that bypassed `log::error!`
        // entirely.
        .build(tauri::generate_context!())
        .unwrap_or_else(|e| {
            eprintln!("[FATAL] tauri build failed: {e:?}");
            log::error!("[FATAL] tauri build failed: {e:?}");
            std::process::exit(1);
        })
        .run(|app_handle, event| match event {
            RunEvent::ExitRequested { .. } | RunEvent::Exit => {
                // Best-effort sidecar teardown. `shutdown_sidecar_for_exit`
                // is idempotent (shutting_down swap) so it's safe to
                // call from both ExitRequested and Exit, and also safe
                // if the renderer's `shutdown_sidecar` command already
                // ran. Wrapped in `block_on` + `tokio::time::timeout`
                // so the run loop never hangs on a misbehaving sidecar
                // (the helper self-limits to ~2s internally; the outer
                // timeout is a safety backstop for the force-kill phase).
                let sidecar_state = app_handle
                    .state::<Arc<SidecarState>>()
                    .inner()
                    .clone();
                tauri::async_runtime::block_on(async move {
                    let _ = tokio::time::timeout(
                        Duration::from_millis(SHUTDOWN_ACK_TIMEOUT_MS + 1000),
                        crate::state::shutdown_sidecar_for_exit(&sidecar_state),
                    )
                    .await;
                });
            }
            _ => {}
        });
}
