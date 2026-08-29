//! Host lifecycle callbacks — relaunch / quit / exit teardown.
//!
//! Relocated verbatim from `state.rs` (pure move, no behavior change) so
//! the shared-state module stays data-only. `state.rs` re-exports these
//! names, so existing `crate::state::on_relaunch_app` /
//! `crate::state::on_quit_app` / `crate::state::on_host_exit` call sites
//! (e.g. `main.rs`'s listener registrations + `RunEvent::Exit` handler)
//! keep resolving unchanged.

use crate::sidecar::{send_fire_and_forget_frame, shutdown_sidecar_for_exit};
use crate::state::SidecarState;
use std::sync::Arc;
use tauri::Manager;

/// Local override for the host's `RunEvent::Exit` shutdown budget.
///
/// `util::SHUTDOWN_ACK_TIMEOUT_MS` is 2000ms (2s) — but the sidecar's
/// graceful shutdown path can legitimately take 3-4s on a cold disk
/// (WAL checkpoint, native hotkey binary teardown). The 2s budget
/// force-kills the sidecar mid-flush, which can corrupt `history.db`
/// and leak the native hotkey binary child.
///
/// This local constant is the HARD ceiling on the exit-path teardown.
/// It MUST be >= `EXIT_SHUTDOWN_ACK_TIMEOUT_MS` (30s) so that the
/// cooperative shutdown wait inside `shutdown_sidecar_for_exit` is
/// never cut short by the outer `tokio::time::timeout` in
/// `on_host_exit`. The 5s headroom (30s + 5s) covers the
/// force-kill + zombie-reap phase that runs after the cooperative
/// wait expires.
///
/// The renderer-invoked `shutdown_sidecar` command keeps the tighter
/// 2s budget (`SHUTDOWN_ACK_TIMEOUT_MS`) — there a tight budget is
/// appropriate because the UI is still alive and a long block freezes
/// it. The `RunEvent::Exit` path is when the host is going away — it
/// should err on the side of giving the sidecar more time.
const HOST_SHUTDOWN_GRACE_MS: u64 = 35_000;

/// `relaunch_app` Tauri event listener body, extracted from
/// `main.rs`'s inline closure so the host entrypoint stays wiring-only.
///
/// Sends a fire-and-forget `relaunch_ack` WS frame back to the Python
/// sidecar (so its `_wait_for_relaunch_ack` short-circuits cleanly
/// instead of blocking for the full 2s timeout), then schedules a
/// delayed `app.restart()` on the async runtime. The 10ms delay (sourced
/// from `util::PRE_RESTART_FLUSH_DELAY_MS`) gives the WS writer task
/// time to flush the ack frame to the socket before `app.restart()`
/// tears down the process.
///
/// The delay is spawned on the async runtime (NOT `tokio::time::sleep`
/// on the event-loop thread) so the Tauri event loop is not blocked.
pub(crate) fn on_relaunch_app(app_handle: &tauri::AppHandle, _event: tauri::Event) {
    use crate::util::PRE_RESTART_FLUSH_DELAY_MS;

    log::info!(
        "[RESTART] relaunch_app event received — sending relaunch_ack + calling app.restart()"
    );
    let state: tauri::State<'_, Arc<SidecarState>> = app_handle.state();
    let state_inner = state.inner().clone();
    let ack_sent = send_fire_and_forget_frame(&state_inner, "relaunch_ack");
    if ack_sent.is_none() {
        log::warn!(
            "[RESTART] ws_tx is None — cannot send relaunch_ack; Python will wait 2s timeout"
        );
    }
    let restart_for_async = app_handle.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_millis(PRE_RESTART_FLUSH_DELAY_MS)).await;
        log::info!("[RESTART] calling app.restart()");
        restart_for_async.restart();
    });
}

/// `quit_app` Tauri event listener body (mirror of `on_relaunch_app`),
/// wired in `main.rs`'s `.setup` next to the `relaunch_app` listener.
///
/// The Python sidecar publishes `quit_app` when the user picks the tray
/// "Quit" item (see `voice_typer/server/app_lifecycle.py::quit_app` —
/// it pushes the event, then runs its own cleanup and exits).
/// Electron's main process handles the same event by calling
/// `app.quit()` (`client/src/main/python/handle-message.ts`); the Tauri
/// host has no main process, so this listener is the equivalent:
///
/// 1. Set `shutting_down` IMMEDIATELY so the WS-reader cleanup (which
///    fires when the sidecar exits moments later) does NOT trigger a
///    supervisor respawn. Without this flag, tray Quit would just
///    restart the backend instead of quitting the app.
/// 2. Call `app.exit(0)` so the host process terminates. The
///    `RunEvent::Exit` / `ExitRequested` callback (`on_host_exit` →
///    `shutdown_sidecar_for_exit`) then runs the sidecar teardown. That
///    teardown is idempotent — it short-circuits on the already-set
///    `shutting_down` flag; the sidecar exits itself as part of its own
///    quit path, and `SidecarHandle::Drop` is the best-effort kill
///    backstop for the (rare) hung-cleanup case.
///
/// The listener registration lives in `main.rs`'s `.setup`
/// (wiring-only); this function is the body.
pub(crate) fn on_quit_app(app_handle: &tauri::AppHandle) {
    let sidecar_state = app_handle.state::<Arc<SidecarState>>().inner().clone();
    // `begin_shutdown()` performs the canonical adjacent pair —
    // `shutting_down.swap(true, SeqCst)` (idempotency guard) immediately
    // followed by `shutdown_notify.notify_one()` (wake a supervisor that
    // may be mid-backoff-sleep so it observes `shutting_down` immediately
    // instead of after its next 100ms poll). It returns the PREVIOUS flag
    // value: `true` means a shutdown is already in flight. On that path
    // the notify is a benign spurious wakeup — the supervisor re-checks
    // the flag and goes back to sleep — and `shutdown_sidecar_for_exit`
    // also fires its own (idempotent) notify on the `RunEvent::Exit` path.
    if sidecar_state.begin_shutdown() {
        log::info!("[QUIT] quit_app event received — shutdown already in progress; exiting host");
    } else {
        log::info!(
            "[QUIT] quit_app event received — setting shutting_down + exiting host (tray Quit → app.exit)"
        );
    }
    app_handle.exit(0);
}

/// `RunEvent::Exit` / `ExitRequested` callback body, extracted from
/// `main.rs`'s inline `.run(callback)` closure so the host entrypoint
/// stays wiring-only.
///
/// Spawns the sidecar teardown on a dedicated std thread (NOT a tokio
/// task) so the Tauri event loop returns immediately — `block_on` can
/// block for up to ~35s on dev-mode shutdowns (the dev-mode sidecar
/// has no `CommandEvent` stream, so `shutdown_sidecar_for_exit`
/// always sleeps the full `EXIT_SHUTDOWN_ACK_TIMEOUT_MS`=30s). The
/// user would otherwise see a non-responsive window / lingering Dock
/// icon during the sleep. The process tears down naturally once the
/// spawned thread completes (Tauri keeps the runtime alive until all
/// spawned tasks / threads resolve on exit paths).
///
/// The teardown is wrapped in `tokio::time::timeout(HOST_SHUTDOWN_GRACE_MS + 1000)`
/// so the run loop never hangs on a misbehaving sidecar.
pub(crate) fn on_host_exit(app_handle: &tauri::AppHandle) {
    use std::time::Duration;

    let sidecar_state = app_handle.state::<Arc<SidecarState>>().inner().clone();
    std::thread::spawn(move || {
        tauri::async_runtime::block_on(async move {
            let _ = tokio::time::timeout(
                Duration::from_millis(HOST_SHUTDOWN_GRACE_MS + 1000),
                shutdown_sidecar_for_exit(&sidecar_state),
            )
            .await;
        });
    });
}
