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
//! Wiring-only (C-ARCH-1): app builder, plugin registration, `.setup`
//! glue (window bootstrap → `window_bootstrap`, sidecar cold-start
//! task → `sidecar::spawn::initialize_sidecar_guarded`, tray init →
//! `tray`), window-event arm dispatch (→ `window_events`), Tauri
//! command registration, single-instance gate. All real logic lives in
//! focused modules (`state`, `util`, `sidecar::*`, `commands::*`,
//! `platform::*`, `window_bootstrap`, `startup_timeline`). See ADR-0020.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// The project-wide clippy lint gate lives in `Cargo.toml`'s
// `[lints.clippy]` block (single source of truth).

mod branding;
mod commands;
mod error;
mod host_events;
mod migrate;
mod notify_aumid;
mod platform;
mod sidecar;
mod startup_timeline;
mod state;
mod theme_icon;
mod tray;
mod util;
mod window_bootstrap;
mod window_events;

// C-TEST-5: sibling `#[cfg(test)]` test files, declared here (not
// inside their production modules) so the sibling-file paths resolve
// without `#[path]` attributes — mirrors the pattern at
// `commands/bubble/mod.rs` and `migrate/mod.rs`.
#[cfg(test)]
mod state_tests;

// Sibling test home for the theme-reactive window icon
// (`theme_icon.rs`), wired the same way as `state_tests` above.
#[cfg(test)]
mod theme_icon_tests;

// Sibling test home for the unified command error enum (`error.rs`),
// wired the same way as `state_tests` above.
#[cfg(test)]
mod error_tests;

// Shared test-only state (e.g. the panic-hook serialization lock used
// by every test that fires a real panic through the process-global
// hook — see `test_support.rs` for the race rationale).
#[cfg(test)]
mod test_support;

use std::sync::Arc;

// `Listener` for `app.listen("relaunch_app", ...)`, `Manager` for the
// single-instance callback's `.get_webview_window`, `RunEvent` for the
// `.run` callback.
use tauri::{Listener, Manager, RunEvent};

use commands::bubble::{
    bubble_dismiss, bubble_hide_complete, bubble_move_by, bubble_resize, bubble_set_draggable,
    bubble_set_position, bubble_show, bubble_signal_ready, bubble_toggle_dictation,
};
use commands::export::{export_history, export_vocabulary};
use commands::sidecar_cmds::{dispatch, shutdown_sidecar};
// system_cmds exposes the window_-namespace commands (open_logs /
// open_model_import_dialog / export_templates / export_config) and the
// renderer_log_error sink.
use commands::system_cmds::{
    export_config, export_templates, open_logs, open_model_import_dialog, renderer_log_error,
    set_host_locale,
};
use platform::logging::init_file_logger_or_stderr_fallback;
use platform::paths::config_dir;
use state::SidecarState;

fn main() {
    // Launch-timeline host-boot marker — must stay the first statement
    // so the measured host-boot phase stays honest (derivation:
    // `startup_timeline.rs` + `voice_typer/server/startup_timeline.py`).
    crate::startup_timeline::record_boot_epoch();

    // Logger bootstrap (order matters — details in `platform::logging`):
    // EarlyLogger first so pre-init `log::*!` lands on stderr; the panic
    // hook next so panics during logger init are captured; the file
    // logger then upgrades the EarlyLogger via the OnceLock swap (it
    // never re-calls `log::set_logger`). The `config_dir()` call also
    // warms its OnceLock for the later `.setup` lookup.
    crate::platform::logging::install_early_logger();
    crate::platform::logging::install_panic_hook();
    init_file_logger_or_stderr_fallback(&platform::paths::config_dir());

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
        // PLUGIN CONFIG CONTRACT (src-tauri/tauri.conf.json `plugins`
        // block) — verified against the plugins-workspace v2 sources +
        // tauri#8769 on the first successful Windows host run
        // (2026-08-21). DO NOT "restore" the old shapes; they crash the
        // app AT STARTUP and CI cannot catch it (CI builds but never
        // launches the app):
        //   - single-instance / notification / dialog register NO
        //     config type (plain `Builder::new(...)` init), so their
        //     entries must be `null` or absent — an empty map `{}`
        //     fails with PluginInitialization("...", "invalid type:
        //     map, expected unit").
        //   - shell: the ONLY plugin with a real config struct; it
        //     accepts exactly ONE key, `open`. The v1-style
        //     `{sidecar: true, scope: [...]}` block fails with
        //     "unknown field `scope`, expected `open`". Sidecar scoping
        //     is NOT done via plugins.shell in v2 — the Rust host
        //     spawns via `app.shell().sidecar(...)`.
        // Regression guards: tests/tauri/mig19/test_final_glue.py::
        // test_tauri_conf_unit_config_plugins_are_null and the
        // test_tauri_conf_shell_config_is_v2_valid tests in
        // tests/tauri/mig15|16|17|18.
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        // dialog plugin for export_history / export_vocabulary
        // save-file dialogs (invoked from Rust, not TS).
        .plugin(tauri_plugin_dialog::init())
        .manage(Arc::new(SidecarState::new()))
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
            open_model_import_dialog,
            export_templates,
            export_config,
            //renderer_log_error sink.
            renderer_log_error,
            //renderer-pushed locale for host-side native-surface
            // localization (Electron `i18n:set-locale` parity).
            set_host_locale,
        ])
        .setup(|app| {
            // Windows toast identity (AUMID) registration — idempotent,
            // body in `notify_aumid.rs`.
            crate::notify_aumid::register(app.handle());

            // Build the `main` window from its `tauri.conf.json` config
            // (body in `window_bootstrap.rs`).
            crate::window_bootstrap::bootstrap_main_window(app);

            let app_handle = app.handle().clone();
            //log only the basename — the absolute path can leak the
            // user's home directory / username (PII in shared logs).
            log::info!(
                "[SETUP] config_dir resolved to: <redacted>/{}",
                config_dir()
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_else(|| platform::paths::APP_SLUG.into())
            );
            // Electron-parity lifecycle events, bodies in
            // `sidecar::lifecycle` (re-exported via `crate::state`):
            // `relaunch_app` → full app restart; `quit_app` (tray Quit,
            // published by the Python sidecar) → shutdown flag +
            // `app.exit(0)`. The Electron→Tauri userData migration that
            // must precede the sidecar spawn runs inside the spawned
            // task below (ADR-0020 §8 — see `sidecar::spawn`).
            let restart_handle = app.handle().clone();
            app.listen("relaunch_app", move |event| {
                crate::state::on_relaunch_app(&restart_handle, event);
            });
            let quit_handle = app.handle().clone();
            app.listen("quit_app", move |_event| {
                crate::state::on_quit_app(&quit_handle);
            });
            // ADR-0020 §6.5: create the system tray. Failure is
            // non-fatal — the app still runs without a tray; the
            // `tray_menu` / `tray_state` listener wiring lives in
            // `tray.rs`. On success, `tray_available` is marked so the
            // main-window close handler can pick hide-to-tray vs. a
            // real close (rationale in `state.rs`).
            crate::tray::create_tray_and_mark_state(app.handle());
            // Electron-parity host events: `show_window` (tray "Open
            // App") + `notification` (native toast) — bodies in
            // `host_events.rs`.
            crate::host_events::setup(app.handle());
            //(Critical): the unconditional `write_restart_counter(0)`
            // that used to live here DEFEATED the circuit breaker — the
            // reset is now ONLY done on successful `reconnect_ws`
            // (supervisor.rs; see git history).
            //
            // Spawn the sidecar + WS bridge in a background tokio task
            // so this `.setup` closure returns quickly (body in
            // `sidecar::spawn::initialize_sidecar_guarded`, C-ARCH-1).
            //
            // C-TOKIO-1 guard: the task runs ON the tokio runtime —
            // panic capture is `AssertUnwindSafe(fut).catch_unwind()
            // .await` (inside the module), NEVER `block_on` from
            // within a runtime worker ("Cannot start a runtime from
            // within a runtime").
            tauri::async_runtime::spawn(sidecar::spawn::initialize_sidecar_guarded(app_handle));
            Ok(())
        })
        .on_window_event(crate::window_events::handle)
        //split `.run(ctx)` into `.build(ctx)?.run(callback)` so we get
        // a callback for `RunEvent::Exit` / `ExitRequested`. Without it
        // the sidecar is leaked when the host exits via `app.exit()` /
        // quit-tray / Ctrl-C / SIGTERM — the close handler only fires on
        // user-initiated window close. A `build` failure logs a [FATAL]
        // line to the file logger AND stderr before exit(1).
        .build(tauri::generate_context!())
        .unwrap_or_else(|e| {
            eprintln!("[FATAL] tauri build failed: {e:?}");
            log::error!("[FATAL] tauri build failed: {e:?}");
            std::process::exit(1);
        })
        .run(|app_handle, event| match event {
            RunEvent::ExitRequested { .. } | RunEvent::Exit => {
                // Teardown body: `state::on_host_exit` — dedicated thread
                // + bounded-time `block_on` (see `sidecar::lifecycle`).
                crate::state::on_host_exit(app_handle);
            }
            _ => {}
        });
}
