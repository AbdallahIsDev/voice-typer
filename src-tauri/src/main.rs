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
mod error;
mod host_events;
mod migrate;
mod platform;
mod sidecar;
mod state;
mod tray;
mod util;

// C-TEST-5: inline `#[cfg(test)] mod tests` block in `state.rs` moved
// to the sibling `state_tests.rs` file, mirroring the existing pattern
// at `commands/bubble/mod.rs` and `migrate/mod.rs`. Declared here (not
// inside `state.rs`) so the sibling-file path resolves without a
// `#[path]` attribute.
#[cfg(test)]
mod state_tests;

// Sibling test home for the unified command error enum (`error.rs`),
// wired the same way as `state_tests` above (no `#[path]` attribute
// needed — the sibling file resolves by module-name convention).
#[cfg(test)]
mod error_tests;

// Shared test-only state (e.g. the panic-hook serialization lock used
// by every test that fires a real panic through the process-global
// hook — see `test_support.rs` for the race rationale).
#[cfg(test)]
mod test_support;

use std::sync::Arc;

// `Listener` for `app.listen("relaunch_app", ...)`, `Manager` for
// `.state()` / `.get_webview_window`, `RunEvent` for the `.run`
// callback, `WindowEvent` for `.on_window_event`.
use tauri::{Listener, Manager, RunEvent, WindowEvent};

// `FutureExt::catch_unwind` + `AssertUnwindSafe` — panic capture for the
// spawned initialize_sidecar task WITHOUT bridging through `block_on`
// (calling block_on inside a runtime worker panics with "Cannot start a
// runtime from within a runtime").
use futures_util::future::FutureExt;
use std::panic::AssertUnwindSafe;

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
    export_config, export_templates, open_logs, open_model_import_dialog, renderer_log_error,
    set_host_locale,
};
use platform::logging::init_file_logger_or_stderr_fallback;
use platform::paths::config_dir;
use state::SidecarState;

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
    // falls back to the EarlyLogger (stderr-only) if file init fails.
    // Use the cached config_dir() so the OnceLock is populated here
    // and the later config_dir() call inside .setup is a zero-cost lookup.
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
        // PLUGIN CONFIG CONTRACT (src-tauri/tauri.conf.json `plugins` block)
        // — verified against the plugins-workspace v2 sources + tauri#8769
        // on the first successful Windows host run (2026-08-21). DO NOT
        // "restore" the old shapes; they crash the app AT STARTUP and CI
        // cannot catch it (CI builds but never launches the app):
        //   - single-instance / notification / dialog: these plugins
        //     register NO config type (plain `Builder::new(...)` init), so
        //     Tauri deserializes their config entry into the UNIT type.
        //     The entry must be `null` (or absent) — an empty map `{}`
        //     fails with PluginInitialization("...", "invalid type: map,
        //     expected unit").
        //   - shell: the ONLY plugin with a real config struct, and it
        //     accepts exactly ONE key: `open`. The v1-style
        //     `{sidecar: true, scope: [...]}` block fails with
        //     "unknown field `scope`, expected `open`". Sidecar scoping
        //     is NOT done via plugins.shell in v2 — the Rust host spawns
        //     via `app.shell().sidecar(...)` (not ACL-gated) and the
        //     JS-facing `shell:allow-spawn` capability grant keeps its
        //     deny-all default scope.
        // Regression guards: tests/tauri/mig19/test_final_glue.py::
        // test_tauri_conf_unit_config_plugins_are_null and the
        // test_tauri_conf_shell_config_is_v2_valid tests in
        // tests/tauri/mig15|16|17|18.
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
            // Custom-title-bar parity with Electron's main window (see
            // `main-window.ts` for the Electron side). The window is NOT
            // auto-created — `tauri.conf.json` declares it with
            // `"create": false` and it is built here from that config so
            // the FRAME can be platform-conditional:
            //   - macOS: keep native decorations + the traffic lights
            //     (`titleBarStyle: Overlay` + `trafficLightPosition` from
            //     config) — Electron's `hiddenInset` equivalent. The
            //     renderer omits its custom window buttons on macOS and
            //     reserves the traffic-light gutter.
            //   - Windows/Linux: fully frameless (`decorations: false`) —
            //     the renderer draws the custom title bar + window
            //     controls, mirroring Electron's `frame: false`.
            let main_window_config = app
                .config()
                .app
                .windows
                .iter()
                .find(|w| w.label == "main")
                .unwrap_or_else(|| panic!("[SETUP] main window missing from tauri.conf.json"));
            let main_window_builder =
                tauri::WebviewWindowBuilder::from_config(app, main_window_config)
                    .expect("[SETUP] main window config is valid");
            #[cfg(not(target_os = "macos"))]
            let main_window_builder = main_window_builder.decorations(false);
            main_window_builder
                .build()
                .expect("[SETUP] main window build failed");

            let app_handle = app.handle().clone();
            //log only the basename — the absolute path can
            // contain the user's home directory / username (PII leak in
            // shared logs / crash reports).
            log::info!(
                "[SETUP] config_dir resolved to: <redacted>/{}",
                config_dir()
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_else(|| platform::paths::APP_SLUG.into())
            );
            // ADR-0020 §8: one-time Electron userData → Tauri config_dir
            // migration, BEFORE the sidecar spawns (so the sidecar boots
            // against already-migrated data). Idempotent + non-destructive.
            // Runs on the async runtime's blocking thread pool via
            // `migrate_electron_userdata_async` (called inside the spawn
            // block below) so the Tauri event loop is not stalled for
            // 5-30s on first launch.
            //Listen for the `relaunch_app` event (renamed from
            // Python's `relaunch_electron`) and trigger a full app
            // restart. The listener body (relaunch_ack + delayed
            // app.restart()) lives in `state::on_relaunch_app` so this
            // file stays wiring-only.
            let restart_handle = app.handle().clone();
            app.listen("relaunch_app", move |event| {
                crate::state::on_relaunch_app(&restart_handle, event);
            });
            // The `quit_app` listener mirrors Electron's
            // `handle-message.ts` quit_app → app.quit() handling: on tray
            // "Quit" the Python sidecar publishes `quit_app` and exits;
            // this listener sets `shutting_down` (so the supervisor
            // doesn't respawn the sidecar) and calls `app.exit(0)` (host
            // teardown via RunEvent::Exit → on_host_exit). The body lives
            // in `state::on_quit_app` so this file stays wiring-only.
            let quit_handle = app.handle().clone();
            app.listen("quit_app", move |_event| {
                crate::state::on_quit_app(&quit_handle);
            });
            // ADR-0020 §6.5: create the system tray. Failure is
            // non-fatal — the app still runs without a tray. The tray
            // is built from the sidecar's `tray_menu` event (tray.rs
            // listens for it and rebuilds the native menu on demand).
            //
            // Record whether the tray exists so the main-window close
            // handler can decide between hide-to-tray (only safe when
            // there IS a tray to bring the window back) and letting the
            // close flow through to app exit (no tray → hiding would
            // strand the user — Electron's `isLinuxWaylandWithoutSni()`
            // guard covers the same case).
            if let Err(e) = crate::tray::create_tray(app.handle()) {
                log::error!("[TRAY] init failed: {}", e);
            } else {
                let state: tauri::State<'_, Arc<SidecarState>> = app.state();
                state
                    .tray_available
                    .store(true, std::sync::atomic::Ordering::SeqCst);
            }
            // Electron-parity host events: `show_window` (tray "Open App" /
            // focus redirect) and `notification` (native toast). Both are
            // consumed by the Electron main process today
            // (`handle-message.ts`) and had no Tauri listener — the bodies
            // live in `host_events.rs` so this file stays wiring-only.
            crate::host_events::setup(app.handle());
            //(Critical): the unconditional `write_restart_counter(0)`
            // that used to live here DEFEATED the circuit breaker — see
            // git history. The reset is now ONLY done on successful
            // `reconnect_ws` (supervisor.rs).
            // Spawn the sidecar + WS bridge in a background tokio task.
            // The orchestration (spawn, install child handle, reconnect
            // WS, respawn fallback) lives in
            // `sidecar::spawn::initialize_sidecar` so this file stays
            // wiring-only (C-ARCH-1).
            tauri::async_runtime::spawn(async move {
                // ADR-0020 §8: run the one-time Electron→Tauri migration
                // on the blocking pool (fs-heavy, 5-30s on first launch)
                // so the async task is not stalled. MUST run before
                // initialize_sidecar so the sidecar boots against
                // already-migrated data.
                migrate::migrate_electron_userdata_async(&app_handle).await;
                let state: tauri::State<'_, Arc<SidecarState>> = app_handle.state();
                let state = state.inner().clone();
                // Capture panics from initialize_sidecar (e.g. a future
                // invariant violation) via FutureExt::catch_unwind so they
                // are logged with the actual message instead of being
                // silently lost (Tauri's runtime does not surface
                // spawned-task panics). This task ALREADY runs on the
                // tokio runtime (the async_runtime::spawn above) — the
                // previous `std::panic::catch_unwind(|| ... block_on ...)`
                // wrapper panicked at startup with "Cannot start a runtime
                // from within a runtime": a future awaited inside a
                // runtime worker must never call block_on.
                // DO NOT bridge this back through `tauri::async_runtime::
                // block_on` / std::thread + block_on — see AGENTS.md
                // constraint C-TOKIO-1.
                let result =
                    AssertUnwindSafe(sidecar::spawn::initialize_sidecar(&app_handle, state))
                        .catch_unwind()
                        .await;
                if let Err(payload) = result {
                    let msg = payload
                        .downcast_ref::<&'static str>()
                        .copied()
                        .or_else(|| payload.downcast_ref::<String>().map(|s| s.as_str()))
                        .unwrap_or("<non-string panic>");
                    log::error!("[MAIN] initialize_sidecar task panicked: {}", msg);
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            // Close-to-tray (ADR-0020 §10 + Electron parity): the main
            // window's X button hides the window (prevent_close + hide)
            // unless a deliberate shutdown is in flight; the bubble
            // window closes normally. The branch logic lives in
            // `commands::sidecar_cmds::on_main_window_close`.
            if let WindowEvent::CloseRequested { api, .. } = event {
                on_main_window_close(window.app_handle(), window, api);
            }
            // Durable bubble drag-position persistence: observe USER drags
            // of the bubble window and write them back to the Python config
            // (debounced, fire-and-forget — see
            // `commands::bubble::persisted_position`). Programmatic moves
            // arm a suppression window so they never persist themselves.
            if let WindowEvent::Moved(position) = event {
                if window.label() == "bubble" {
                    let sidecar_state =
                        window.app_handle().state::<std::sync::Arc<crate::state::SidecarState>>();
                    crate::commands::bubble::schedule_persist(
                        sidecar_state.inner(),
                        position.x,
                        position.y,
                    );
                }
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
