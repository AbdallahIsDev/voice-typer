//! Tauri v2 host (ADR-0020). Wiring-only (C-ARCH-1): builder, plugins,
//! `.setup` glue, window-event dispatch, command registration. Logic lives
//! in focused modules.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// Clippy lint gate lives in `Cargo.toml` `[lints.clippy]` (single source).

mod branding;
mod commands;
mod error;
mod host_events;
mod launch_args;
mod migrate;
mod notify_aumid;
mod platform;
mod shortcuts;
mod sidecar;
mod startup_timeline;
mod state;
mod theme_icon;
mod tray;
mod util;
mod window_bootstrap;
mod window_events;

// C-TEST-5: sibling test modules.
#[cfg(test)]
mod state_tests;
#[cfg(test)]
mod theme_icon_tests;
#[cfg(test)]
mod error_tests;
#[cfg(test)]
mod launch_args_tests;
// Shared test-only helpers (panic-hook serialization lock).
#[cfg(test)]
mod test_support;

use std::sync::Arc;

// `Listener` for `app.listen`; `RunEvent` for `.run` (incl. macOS Reopen).
use tauri::{Listener, Manager, RunEvent};

use commands::bubble::{
    bubble_dismiss, bubble_hide_complete, bubble_move_by, bubble_resize, bubble_set_draggable,
    bubble_set_position, bubble_show, bubble_signal_ready, bubble_toggle_dictation,
};
use commands::export::{export_history, export_vocabulary};
use commands::sidecar_cmds::{dispatch, restart_sidecar, shutdown_sidecar};
// Window-namespace commands + export dialogs + renderer_log_error sink.
use commands::system_cmds::{
    export_config, export_templates, open_external_url_command, open_logs,
    open_model_import_dialog, renderer_heartbeat, renderer_log_error, reveal_path_command,
    save_stats_image, set_host_locale,
};
use platform::logging::init_file_logger_or_stderr_fallback;
use platform::paths::config_dir;
use state::SidecarState;
use state::WorkerState;

fn main() {
    // Host-boot marker: first statement so the measured host-boot phase
    // stays honest (`startup_timeline.rs` + server twin).
    crate::startup_timeline::record_boot_epoch();

    // Direct-binary autostart flags (`--hidden` / `--delay N`) honored
    // BEFORE the builder/sidecar spawn. VT_START_HIDDEN reuses the
    // existing window_bootstrap hide path (C-BG-1).
    let launch = crate::launch_args::parse_current();
    if launch.hidden {
        std::env::set_var("VT_START_HIDDEN", "1");
    }
    if launch.delay_secs > 0.0 {
        eprintln!(
            "[STARTUP] autostart delay {:.1}s before host init",
            launch.delay_secs
        );
    }

    // Logger bootstrap order: EarlyLogger → panic hook → file logger
    // (OnceLock upgrade; never re-calls `log::set_logger`).
    crate::platform::logging::install_early_logger();
    crate::platform::logging::install_panic_hook();
    init_file_logger_or_stderr_fallback(&platform::paths::config_dir());

    // Logon I/O-settle delay. Plain sync sleep in `main`, never on a
    // runtime worker (C-TOKIO-1).
    if launch.delay_secs > 0.0 {
        log::info!(
            "[STARTUP] autostart delay {:.1}s before host init",
            launch.delay_secs
        );
        std::thread::sleep(std::time::Duration::from_secs_f32(launch.delay_secs));
    }

    tauri::Builder::default()
        // Single-instance MUST be FIRST so its duplicate check runs
        // before any sidecar spawn (else a double launch leaves a zombie
        // python). Callback focuses the existing window; second instance
        // exits immediately.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            crate::host_events::show_main_window(app);
        }))
        // C-TAURI-2: unit-config plugins take `null` (not `{}`); shell
        // takes only `{open}`. Sidecar scoping is via
        // `app.shell().sidecar(...)`, not config.
        // see docs/code-notes/tauri-host.md#plugin-config-contract-c-tauri-2
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        // dialog plugin for Rust-side save-file dialogs.
        .plugin(tauri_plugin_dialog::init())
        // Global-shortcut runtime for the bubble-dismiss accelerator;
        // renderer never touches it, so no capability grant is needed.
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(Arc::new(SidecarState::new()))
        // Renderer liveness tracker: `renderer_heartbeat` timestamps into
        // it; the watchdog reads it.
        .manage(Arc::new(platform::renderer_watchdog::HeartbeatState::new()))
        // ML worker lifecycle state: (re)starts on `offline_pack_verified`.
        .manage(Arc::new(WorkerState::new()))
        .invoke_handler(tauri::generate_handler![
            dispatch,
            shutdown_sidecar,
            // renderer-initiated sidecar restart
            restart_sidecar,
            export_history,
            export_vocabulary,
            bubble_show,
            bubble_signal_ready,
            bubble_set_position,
            bubble_set_draggable,
            bubble_move_by,
            bubble_hide_complete,
            // dismiss command (mirror of `bubble_hide_complete`)
            bubble_dismiss,
            // bubble window extensions (resize / toggle)
            bubble_resize,
            bubble_toggle_dictation,
            // system-level window_ commands
            open_logs,
            // external https links + reveal-in-file-manager
            open_external_url_command,
            reveal_path_command,
            open_model_import_dialog,
            export_templates,
            export_config,
            // Share-image Downloads-save + localized Save-As dialog
            save_stats_image,
            // renderer_log_error sink
            renderer_log_error,
            // renderer-pushed locale for host native surfaces
            set_host_locale,
            // renderer liveness heartbeat for the webview watchdog
            renderer_heartbeat,
        ])
        .setup(|app| {
            // Windows toast identity (AUMID), idempotent.
            crate::notify_aumid::register(app.handle());

            // Build `main` from its `tauri.conf.json` entry
            // (`window_bootstrap`); takes `&AppHandle` so the same
            // builder can rebuild later (macOS Dock activation).
            crate::window_bootstrap::bootstrap_main_window(app.handle());

            let app_handle = app.handle().clone();
            // Basename only: the absolute path can leak home dir / username.
            log::info!(
                "[SETUP] config_dir resolved to: <redacted>/{}",
                config_dir()
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_else(|| platform::paths::APP_SLUG.into())
            );
            // `relaunch_app` → full restart; `quit_app` (tray Quit) →
            // shutdown flag + `app.exit(0)`. Bodies in `sidecar::lifecycle`.
            let restart_handle = app.handle().clone();
            app.listen("relaunch_app", move |event| {
                crate::state::on_relaunch_app(&restart_handle, event);
            });
            let quit_handle = app.handle().clone();
            app.listen("quit_app", move |_event| {
                crate::state::on_quit_app(&quit_handle);
            });
            // System tray (non-fatal). On success `tray_available` is
            // marked so the close handler picks hide-to-tray vs real close.
            crate::tray::create_tray_and_mark_state(app.handle());
            // `show_window` (tray Open App) + `notification` (toast).
            crate::host_events::setup(app.handle());
            // Bubble-dismiss accelerator (best-effort: refusal only costs
            // the keyboard path; the '×' button still works).
            crate::shortcuts::register_bubble_dismiss(app.handle());
            // Renderer liveness watchdog: wry exposes no host crash event,
            // so the host watches the renderer heartbeat.
            crate::platform::renderer_watchdog::spawn_watchdog(app.handle());
            // OS suspend/resume monitor (best-effort; body in
            // `platform/power.rs`, sidecar actions via
            // `async_runtime::spawn`, C-TOKIO-1).
            let _power = crate::platform::power::spawn_power_monitor(
                app.handle().clone(),
                app.state::<Arc<SidecarState>>().inner().clone(),
            );
            // Critical: no unconditional `write_restart_counter(0)` here —
            // it defeated the circuit breaker; reset happens ONLY on
            // successful `reconnect_ws` (supervisor).
            //
            // Sidecar + WS bridge in a background tokio task (C-ARCH-1,
            // body `sidecar::spawn::initialize_sidecar_guarded`).
            //
            // C-TOKIO-1: task runs ON the runtime, so panic capture is
            // `AssertUnwindSafe(fut).catch_unwind().await` (inside the
            // module), NEVER `block_on` on a runtime worker.
            tauri::async_runtime::spawn(sidecar::spawn::initialize_sidecar_guarded(app_handle));
            Ok(())
        })
        .on_window_event(crate::window_events::handle)
        // Split `.run(ctx)` into `.build(ctx)?.run(cb)` so `RunEvent::Exit`
        // / `ExitRequested` can tear down the sidecar (else it leaks on
        // `app.exit()` / tray-quit). Build failure logs [FATAL] then exit(1).
        .build(tauri::generate_context!())
        .unwrap_or_else(|e| {
            eprintln!("[FATAL] tauri build failed: {e:?}");
            log::error!("[FATAL] tauri build failed: {e:?}");
            std::process::exit(1);
        })
        .run(|app_handle, event| match event {
            RunEvent::ExitRequested { .. } | RunEvent::Exit => {
                // `state::on_host_exit`: dedicated thread + bounded-time
                // `block_on` (see `sidecar::lifecycle`).
                crate::state::on_host_exit(app_handle);
            }
            // macOS Dock activation: process outlives the last window, so
            // a Dock click brings the dashboard back.
            #[cfg(target_os = "macos")]
            RunEvent::Reopen { .. } => {
                crate::host_events::show_main_window(app_handle);
            }
            _ => {}
        });
}
