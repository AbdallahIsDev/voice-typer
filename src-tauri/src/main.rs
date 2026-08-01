//! Voice Typer — Tauri v2 host (ADR-0020 implementation).
//!
//! Rust shell replacing the Electron main process. Responsibilities:
//! 1. Spawn the Python sidecar via Tauri's `externalBin` mechanism,
//!    passing `VOICE_TYPER_IPC_TOKEN` + `TAURI_SIDECAR=1` env vars.
//! 2. Open a WebSocket client to `ws://127.0.0.1:N` and perform the
//!    bearer-token auth handshake (`{"type":"auth","token":...}`).
//! 3. Expose ONE generic `dispatch` command to the webview and
//!    re-emit server-initiated events as Tauri events.
//! 4. Run the supervisor (respawn with backoff, then full-app relaunch)
//!    and coalesce `bubble_level` events to ≤30 Hz.
//! 5. Single-instance gate runs BEFORE any sidecar init so a second
//!    launch doesn't spawn a zombie sidecar.
//!
//! # Cross-platform
//!
//! - Windows: WebView2 (Chromium-based, system-installed on Win10+).
//! - macOS: WKWebView (Safari-based, system).
//! - Linux: webkit2gtk (system; requires `libwebkit2gtk-4.1-0`).
//!
//! # Module layout
//!
//! Wiring-only (~280 lines): app builder, plugin registration, `.setup`
//! glue, Tauri command registration, single-instance gate. All real logic
//! lives in focused modules (`state`, `util`, `sidecar::*`,
//! `commands::*`, `platform::*`). See ADR-0020.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// The project-wide clippy lint gate lives in `Cargo.toml`'s
// `[lints.clippy]` block (single source of truth).

mod branding;
mod commands;
mod migrate;
mod platform;
mod sidecar;
mod state;
mod tray;
mod util;

use std::sync::Arc;

// `Listener` for `app.listen("relaunch_app", ...)`, `Manager` for
// `.state()` / `.get_webview_window`, `RunEvent` for the `.run`
// callback, `WindowEvent` for `.on_window_event`.
use tauri::{Listener, Manager, RunEvent, WindowEvent};

use commands::bubble::{
    bubble_dismiss, bubble_hide_complete, bubble_move_by, bubble_resize, bubble_set_draggable,
    bubble_set_position, bubble_show, bubble_signal_ready, bubble_toggle_dictation,
};
use commands::export::{export_history, export_vocabulary};
use commands::sidecar_cmds::{dispatch, on_main_window_close, shutdown_sidecar};
//system_cmds exposes the window_-namespace
// commands (open_logs / open_model_import_dialog / export_templates /
// export_config) and the renderer_log_error sink.
use commands::system_cmds::{
    export_config, export_templates, open_host_logs, open_logs, open_model_import_dialog,
    renderer_log_error,
};
use platform::logging::init_file_logger_or_stderr_fallback;
use platform::paths::config_dir;
use sidecar::spawn::spawn_sidecar_and_get_port;
use sidecar::supervisor::respawn;
use sidecar::ws::reconnect_ws;
use state::SidecarState;
use std::sync::atomic::Ordering;

fn main() {
    //install the EarlyLogger (stderr-only fallback) as the very
    // first line so any pre-init `log::*!` call lands on stderr. Then
    // install the panic hook BEFORE the file logger so panics during
    // logger init are still captured. `init_file_logger_or_stderr_fallback`
    // UPGRADES the EarlyLogger to the combined file+stderr sink via the
    // `OnceLock::set` swap pattern (it does NOT call `log::set_logger`
    // again — that's process-global one-shot).
    crate::platform::logging::install_early_logger();
    crate::platform::logging::install_panic_hook();

    // ADR-0020 §11: init the rotating file logger BEFORE the Tauri
    // builder runs so early startup errors are captured. The helper
    // falls back to `env_logger` (stderr-only) if file init fails.
    let config_dir_path = platform::paths::config_dir_from_env(
        std::env::var("HOME").ok().as_deref(),
        std::env::var("APPDATA").ok().as_deref(),
        std::env::var("XDG_DATA_HOME").ok().as_deref(),
        std::env::var("VOICE_TYPER_CONFIG_DIR").ok().as_deref(),
    );
    init_file_logger_or_stderr_fallback(&config_dir_path);

    tauri::Builder::default()
        // ADR-0020 §12: single-instance MUST be the FIRST plugin so its
        // duplicate-instance check runs before any sidecar spawn (which
        // would otherwise leave a zombie python process on a double
        // launch). The plugin's callback focuses the existing main
        // window; the second instance exits immediately after.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                if let Err(e) = window.show() {
                    log::warn!(
                        "[MAIN] single-instance: window.show() failed (best-effort): {}",
                        e
                    );
                }
                if let Err(e) = window.set_focus() {
                    log::warn!(
                        "[MAIN] single-instance: window.set_focus() failed (best-effort): {}",
                        e
                    );
                }
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        //dialog plugin for export_history / export_vocabulary
        // save-file dialogs (invoked from Rust, not TS).
        .plugin(tauri_plugin_dialog::init())
        .manage(Arc::new(SidecarState::new()))
        //+ : register export + bubble commands
        // alongside the existing dispatch/shutdown_sidecar.
        //the dead paste Tauri command was removed
        // (Python sidecar owns the paste path).
        .invoke_handler(tauri::generate_handler![
            dispatch,
            shutdown_sidecar,
            export_history,
            export_vocabulary,
            bubble_show,
            bubble_signal_ready,
            bubble_set_position,
            bubble_set_draggable,
            bubble_move_by,
            bubble_hide_complete,
            //dismiss command (mirror of `bubble_hide_complete`).
            bubble_dismiss,
            //bubble window extensions (resize / toggle).
            bubble_resize,
            bubble_toggle_dictation,
            //system-level window_ commands.
            open_logs,
            open_host_logs,
            open_model_import_dialog,
            export_templates,
            export_config,
            //renderer_log_error sink.
            renderer_log_error,
        ])
        .setup(|app| {
            let app_handle = app.handle().clone();
            //log only the basename — the absolute path can
            // contain the user's home directory / username (PII leak in
            // shared logs / crash reports).
            log::info!(
                "[SETUP] config_dir resolved to: <redacted>/{}",
                config_dir()
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_else(|| "voice-typer".into())
            );
            // ADR-0020 §8: one-time Electron userData → Tauri config_dir
            // migration, BEFORE the sidecar spawns (so the sidecar boots
            // against already-migrated data). Idempotent + non-destructive.
            migrate::migrate_electron_userdata(&app_handle);
            //Listen for the `relaunch_app` event (renamed from
            // Python's `relaunch_electron`) and trigger a full app
            // restart. The listener body (relaunch_ack + delayed
            // app.restart()) lives in `state::on_relaunch_app` so this
            // file stays wiring-only.
            let restart_handle = app.handle().clone();
            app.listen("relaunch_app", move |event| {
                crate::state::on_relaunch_app(&restart_handle, event);
            });
            // ADR-0020 §6.5: create the system tray. Failure is
            // non-fatal — the app still runs without a tray. The tray
            // is built from the sidecar's `tray_menu` event (tray.rs
            // listens for it and rebuilds the native menu on demand).
            if let Err(e) = crate::tray::create_tray(app.handle()) {
                log::error!("[TRAY] init failed: {}", e);
            }
            //(Critical): the unconditional `write_restart_counter(0)`
            // that used to live here DEFEATED the circuit breaker — see
            // git history. The reset is now ONLY done on successful
            // `reconnect_ws` (supervisor.rs).
            // Spawn the sidecar + WS bridge in a background tokio task.
            tauri::async_runtime::spawn(async move {
                let state: tauri::State<'_, Arc<SidecarState>> = app_handle.state();
                let state = state.inner().clone();

                let token = util::generate_token();

                match spawn_sidecar_and_get_port(&app_handle, &token).await {
                    Ok((port, child, exit_rx)) => {
                        //re-check shutting_down AFTER spawn
                        // returns — if the user quit the app while we
                        // were waiting for server_started (up to 30s on
                        // a cold start), the `RunEvent::Exit` handler
                        // already set the flag but found no child to
                        // kill. Kill the freshly-spawned sidecar here
                        // so it doesn't outlive the host, then bail
                        // before installing it into state.
                        if state.shutting_down.load(Ordering::SeqCst) {
                            log::info!(
                                "[SETUP] shutting_down set during sidecar spawn — \
                                 killing freshly-spawned sidecar"
                            );
                            if let Err(e) = child.kill_tree().await {
                                log::warn!(
                                    "[SETUP] kill_tree on freshly-spawned sidecar failed (best-effort): {}",
                                    e
                                );
                            }
                            return;
                        }
                        *crate::state::lock(&state.child) = Some(child);
                        //store the sidecar's event receiver so
                        // shutdown_sidecar can poll for graceful exit.
                        *state.child_exit_rx.lock().await = exit_rx;
                        if let Err(e) = reconnect_ws(&app_handle, &state, port, &token).await {
                            log::error!("[SETUP] initial WS connect failed: {}", e);
                            let _ = respawn(&app_handle, &state).await;
                        }
                    }
                    Err(e) => {
                        log::error!("[SETUP] sidecar spawn failed: {}", e);
                        let _ = respawn(&app_handle, &state).await;
                    }
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            // ADR-0020 §10: on main window close, shutdown the sidecar.
            // The branch logic (macOS skip, bubble logging, spawned
            // shutdown_sidecar task) lives in
            // `commands::sidecar_cmds::on_main_window_close`.
            if let WindowEvent::CloseRequested { .. } = event {
                on_main_window_close(window.app_handle(), window);
            }
        })
        //split `.run(ctx)` into `.build(ctx)?.run(callback)`
        // so we get a callback for `RunEvent::Exit` / `ExitRequested`.
        // Without this, the sidecar is leaked when the host exits via
        // `app.exit()` / quit-tray / Ctrl-C / SIGTERM — the
        // `on_window_event` close handler only fires on user-initiated
        // window close, NOT on these other exit paths.
        //
        //replace `.expect(...)` with a structured error
        // handler so a `tauri::Builder::build` failure logs a [FATAL]
        // line to the rotating file logger AND stderr before exiting
        // with code 1.
        .build(tauri::generate_context!())
        .unwrap_or_else(|e| {
            eprintln!("[FATAL] tauri build failed: {e:?}");
            log::error!("[FATAL] tauri build failed: {e:?}");
            std::process::exit(1);
        })
        .run(|app_handle, event| match event {
            RunEvent::ExitRequested { .. } | RunEvent::Exit => {
                //spawn the sidecar teardown on a
                // dedicated thread so the event loop returns
                // immediately. The teardown body lives in
                // `state::on_host_exit` (wraps
                // `shutdown_sidecar_for_exit` in a bounded-time
                // `block_on`).
                crate::state::on_host_exit(app_handle);
            }
            _ => {}
        });
}
