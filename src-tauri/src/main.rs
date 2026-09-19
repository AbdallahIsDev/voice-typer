//! Tauri v2 host (ADR-0020 implementation).
//!
//! Rust shell for the sole desktop host: spawns the Python sidecar via
//! `externalBin`, bridges it over a WS bearer-token handshake, exposes
//! ONE generic `dispatch` command to the webview, and runs the
//! supervisor + ≤30 Hz `bubble_level` coalescing. The single-instance
//! gate runs BEFORE any sidecar init so a second launch can't spawn a
//! zombie sidecar.
//!
//! Cross-platform: Windows WebView2, macOS WKWebView, Linux webkit2gtk.
//!
//! Wiring-only (C-ARCH-1): builder, plugin registration, `.setup` glue
//! (window bootstrap → `window_bootstrap`, sidecar cold-start task →
//! `sidecar::spawn::initialize_sidecar_guarded`, tray → `tray`),
//! window-event dispatch (→ `window_events`), command registration.
//! All logic lives in focused modules. See ADR-0020.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// The project-wide clippy lint gate lives in `Cargo.toml`'s
// `[lints.clippy]` block (single source of truth).

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

// C-TEST-5: sibling test files declared here (mirrors
// `commands/bubble/mod.rs` + `migrate/mod.rs`).
#[cfg(test)]
mod state_tests;
#[cfg(test)]
mod theme_icon_tests;
#[cfg(test)]
mod error_tests;
#[cfg(test)]
mod launch_args_tests;
// Shared test-only state (panic-hook serialization lock, see `test_support.rs`).
#[cfg(test)]
mod test_support;

use std::sync::Arc;

// `Listener` for `app.listen("relaunch_app", ...)`; `RunEvent` for the
// `.run` callback (incl. the macOS `Reopen` arm, MO-112).
use tauri::{Listener, Manager, RunEvent};

use commands::bubble::{
    bubble_dismiss, bubble_hide_complete, bubble_move_by, bubble_resize, bubble_set_draggable,
    bubble_set_position, bubble_show, bubble_signal_ready, bubble_toggle_dictation,
};
use commands::export::{export_history, export_vocabulary};
use commands::sidecar_cmds::{dispatch, restart_sidecar, shutdown_sidecar};
// Window_-namespace commands + export dialogs + renderer_log_error sink.
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
    // Host-boot marker: stays the first statement so the measured
    // host-boot phase stays honest (`startup_timeline.rs` + server twin).
    crate::startup_timeline::record_boot_epoch();

    // Direct-binary autostart flags (`--hidden` / `--delay N`): OS
    // entries spawn this binary directly (no launcher), so the host
    // honors them here, BEFORE the builder/sidecar spawn. Setting
    // VT_START_HIDDEN reuses the existing `window_bootstrap` hide +
    // sidecar `vt_start_hidden_env` forwarding unchanged (C-BG-1).
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

    // Logger bootstrap (order matters, see `platform::logging`):
    // EarlyLogger → panic hook → file logger (OnceLock upgrade, never
    // re-calls `log::set_logger`). `config_dir()` warms its OnceLock.
    crate::platform::logging::install_early_logger();
    crate::platform::logging::install_panic_hook();
    init_file_logger_or_stderr_fallback(&platform::paths::config_dir());

    // Logon I/O-settle delay, before the Builder (eprintln above covers
    // pre-logger output, this covers the file log). Plain sync sleep in
    // `main`, never on a runtime worker (C-TOKIO-1).
    if launch.delay_secs > 0.0 {
        log::info!(
            "[STARTUP] autostart delay {:.1}s before host init",
            launch.delay_secs
        );
        std::thread::sleep(std::time::Duration::from_secs_f32(launch.delay_secs));
    }

    tauri::Builder::default()
        // Single-instance MUST be FIRST so its duplicate check runs
        // before any sidecar spawn (else a double launch leaves a
        // zombie python). The callback focuses the existing window via
        // the shared raise-to-front routine (MO-109: bare show+focus
        // only flashed the taskbar when minimized/buried) and recreates
        // it when gone. The second instance exits immediately after.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            crate::host_events::show_main_window(app);
        }))
        // PLUGIN CONFIG CONTRACT (`tauri.conf.json` `plugins` block,
        // verified against the plugins-workspace v2 sources + tauri#8769
        // on the first Windows host run, 2026-08-21; CI builds but never
        // launches, so it can't catch these): single-instance /
        // notification / dialog take NO config (entries must be `null`,
        // `{}` fails deserialization); shell takes exactly ONE key
        // (`open`), v1 `{sidecar, scope}` fails. Sidecar scoping is via
        // `app.shell().sidecar(...)`, not config. Guards:
        // mig19 `test_tauri_conf_unit_config_plugins_are_null` +
        // mig15|16|17|18 `test_tauri_conf_shell_config_is_v2_valid`.
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        // dialog plugin for the Rust-side save-file dialogs.
        .plugin(tauri_plugin_dialog::init())
        // Global-shortcut runtime for the bubble-dismiss accelerator
        // (MO-125, registered in `setup`); renderer never touches it,
        // so no capability grant is needed.
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(Arc::new(SidecarState::new()))
        // Renderer liveness tracker (MO-113): `renderer_heartbeat`
        // timestamps into it, the watchdog reads it.
        .manage(Arc::new(platform::renderer_watchdog::HeartbeatState::new()))
        // ML worker lifecycle state (BP-33 Phase 2c): (re)starts on the
        // `offline_pack_verified` event (see `sidecar::spawn::worker`).
        .manage(Arc::new(WorkerState::new()))
        .invoke_handler(tauri::generate_handler![
            dispatch,
            shutdown_sidecar,
            // renderer-initiated sidecar restart (MO-120).
            restart_sidecar,
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
            // external https links (MO-118) + reveal-in-file-manager (MO-120b).
            open_external_url_command,
            reveal_path_command,
            open_model_import_dialog,
            export_templates,
            export_config,
            // Share-image Downloads-save + localized Save-As dialog (MO-121).
            save_stats_image,
            //renderer_log_error sink.
            renderer_log_error,
            //renderer-pushed locale for host native surfaces.
            set_host_locale,
            //renderer liveness heartbeat for the webview watchdog (MO-113).
            renderer_heartbeat,
        ])
        .setup(|app| {
            // Windows toast identity (AUMID), idempotent.
            crate::notify_aumid::register(app.handle());

            // Build `main` from its `tauri.conf.json` entry (body in
            // `window_bootstrap.rs`); takes `&AppHandle` so it can also
            // rebuild the window later (macOS Dock activation).
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
            // `relaunch_app` → full restart; `quit_app` (tray Quit from
            // the sidecar) → shutdown flag + `app.exit(0)` (bodies in
            // `sidecar::lifecycle` via `crate::state`). The
            // predecessor→Tauri userData migration runs inside the
            // spawned task below (ADR-0020 §8, see `sidecar::spawn`).
            let restart_handle = app.handle().clone();
            app.listen("relaunch_app", move |event| {
                crate::state::on_relaunch_app(&restart_handle, event);
            });
            let quit_handle = app.handle().clone();
            app.listen("quit_app", move |_event| {
                crate::state::on_quit_app(&quit_handle);
            });
            // System tray (ADR-0020 §6.5, non-fatal; menu/state wiring in
            // `tray.rs`). On success `tray_available` is marked so the
            // close handler picks hide-to-tray vs. real close.
            crate::tray::create_tray_and_mark_state(app.handle());
            // `show_window` (tray Open App) + `notification` (toast).
            crate::host_events::setup(app.handle());
            // Bubble-dismiss accelerator (MO-125, best-effort: refusal
            // only costs the keyboard path, the '×' button still works).
            crate::shortcuts::register_bubble_dismiss(app.handle());
            // Renderer liveness watchdog (MO-113): wry exposes no host
            // crash event, so the host watches the renderer heartbeat.
            crate::platform::renderer_watchdog::spawn_watchdog(app.handle());
            // OS suspend/resume (MO-126, body in `platform/power.rs`,
            // sidecar actions via `async_runtime::spawn`, C-TOKIO-1).
            // Best-effort: failure only costs the suspend-finalize path.
            let _power = crate::platform::power::spawn_power_monitor(
                app.handle().clone(),
                app.state::<Arc<SidecarState>>().inner().clone(),
            );
            // (Critical): no unconditional `write_restart_counter(0)`
            // here, it defeated the circuit breaker; the reset happens
            // ONLY on successful `reconnect_ws` (supervisor.rs).
            //
            // Sidecar + WS bridge in a background tokio task so `.setup`
            // returns quickly (body in
            // `sidecar::spawn::initialize_sidecar_guarded`, C-ARCH-1).
            //
            // C-TOKIO-1: the task runs ON the runtime, so panic capture
            // is `AssertUnwindSafe(fut).catch_unwind().await` (inside the
            // module), NEVER `block_on` on a runtime worker.
            tauri::async_runtime::spawn(sidecar::spawn::initialize_sidecar_guarded(app_handle));
            Ok(())
        })
        .on_window_event(crate::window_events::handle)
        // Split `.run(ctx)` into `.build(ctx)?.run(cb)` for
        // `RunEvent::Exit` / `ExitRequested`: without it the sidecar
        // leaks on `app.exit()` / tray-quit / Ctrl-C / SIGTERM. A `build`
        // failure logs [FATAL] to file + stderr before exit(1).
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
            // macOS Dock activation (MO-112): process outlives the last
            // window, so a Dock click brings the dashboard back.
            #[cfg(target_os = "macos")]
            RunEvent::Reopen { .. } => {
                crate::host_events::show_main_window(app_handle);
            }
            _ => {}
        });
}
