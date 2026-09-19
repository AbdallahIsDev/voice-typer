
use crate::sidecar::shutdown::shutdown_sidecar_for_exit_with_budget;
use crate::sidecar::supervisor::clear_restart_counter_for_user_restart;
use crate::sidecar::{send_fire_and_forget_frame, shutdown_sidecar_for_exit};
use crate::state::SidecarState;
use crate::state::WorkerState;
use std::sync::Arc;
use tauri::Manager;

const HOST_SHUTDOWN_GRACE_MS: u64 = 35_000;

pub(super) const PRE_RESTART_SIDECAR_GRACE_MS: u64 = 5_000;

/// Duration suffix for the pre-restart teardown completion log line
/// (C-LOG-2: ` 2.3s` / ` 1m 2.3s`, space-separated, returned WITH the
/// single leading space so the caller splices it with a bare `{}`).
/// Mirrors Python's `voice_typer/server/duration.py::format_duration`
/// (the cross-language convention; no Rust-side helper existed yet, so
/// this local one keeps the formats in lockstep, sub-minute
/// `{:.1}s`, minutes `{}m {:.1}s`).
pub(super) fn format_duration_suffix(d: std::time::Duration) -> String {
    let total_secs = d.as_secs_f64();
    if total_secs < 60.0 {
        format!(" {:.1}s", total_secs)
    } else {
        let minutes = (total_secs / 60.0).floor() as u64;
        let seconds = total_secs - 60.0 * minutes as f64;
        format!(" {}m {:.1}s", minutes, seconds)
    }
}

/// `relaunch_app` Tauri event listener body, extracted from
/// `main.rs`'s inline closure so the host entrypoint stays wiring-only.
///
/// Sends a fire-and-forget `relaunch_ack` WS frame back to the Python
/// sidecar (so its `_wait_for_relaunch_ack` short-circuits cleanly
/// instead of blocking for the full 2s timeout), then schedules the
/// restart sequence on the async runtime:
///
/// 1. Clear the persisted sidecar crash-loop counter (user-initiated
///    restart = a fresh 3-attempt budget for the relaunched process;
///    see `clear_restart_counter_for_user_restart`). The write runs on
///    `spawn_blocking` (atomic temp-file write + fsync + rename —
///    never on an async worker) and is AWAITED so it is ordered
///    before the restart: a fire-and-forget write could lose the race
///    with the exiting process and leave the relaunched instance a
///    tripped breaker.
/// 2. Run the cooperative pre-restart sidecar teardown —
///    `begin_shutdown()` + the `{"type":"shutdown"}` frame + a
///    bounded graceful-exit wait (`PRE_RESTART_SIDECAR_GRACE_MS`) +
///    force-kill backstop: so the backend is TOLD the restart is
///    coming and gets an honest moment to flush (WAL checkpoint,
///    crash-recovery entries, native hotkey teardown) instead of being
///    hard-killed mid-cleanup. This also arms `shutting_down` so the
///    supervisor never races the restart window with a pointless
///    respawn, and it makes the `RunEvent::Exit` teardown that fires
///    DURING `app.restart()` short-circuit (idempotency guard), the
///    detached `on_host_exit` thread cannot be joined BEFORE the
///    restart because it only spawns on the Exit event the restart
///    itself emits; running the teardown inline first turns that
///    thread into a no-op guard instead of a process-exit race.
/// 3. `tokio::time::sleep(PRE_RESTART_FLUSH_DELAY_MS)` (10ms, from
///    `util`) gives the WS writer task time to flush the frames to
///    the socket before `app.restart()` tears down the process.
///
/// The sequence is spawned on the async runtime (NOT `tokio::time::sleep`
/// on the event-loop thread) so the Tauri event loop is not blocked;
/// inside it, every wait is `.await`ed (C-TOKIO-1, no `block_on`
/// inside a runtime worker) and the disk write is `spawn_blocking`.
pub(crate) fn on_relaunch_app(app_handle: &tauri::AppHandle, _event: tauri::Event) {
    use crate::util::PRE_RESTART_FLUSH_DELAY_MS;

    log::info!(
        "[RESTART] relaunch_app event received: sending relaunch_ack + calling app.restart()"
    );
    let state: tauri::State<'_, Arc<SidecarState>> = app_handle.state();
    let state_inner = state.inner().clone();
    let ack_sent = send_fire_and_forget_frame(&state_inner, "relaunch_ack");
    if ack_sent.is_none() {
        log::warn!(
            "[RESTART] ws_tx is None: cannot send relaunch_ack; Python will wait 2s timeout"
        );
    }

    // DEV GUARD (2026-08-30 tray-Restart postmortem): under `tauri dev`
    // this host process is the CLI's child. `app.restart()` exits the
    // process, which (1) ends the CLI dev session (no more Rust rebuild
    // watching) and (2) the relaunched exe is reaped when the CLI's
    // Windows job object closes: the whole app just dies. In dev the
    // CONTRACT is: the host must survive; only the SIDECAR restarts.
    // The sidecar exits itself right after publishing `relaunch_app`
    // (tray Restart → restart_app()), so the normal supervisor path
    // (WS close → generation-gated respawn → fresh sidecar → re-auth →
    // UI re-hydrates from the state snapshot) performs the restart
    // while host + CLI + Vite stay up. Binding rule: AGENTS.md
    // C-TDEV-2.
    if crate::sidecar::spawn::dev_mode::is_dev_mode() {
        let dev_clear_state = state_inner.clone();
        let _ = tauri::async_runtime::spawn_blocking(move || {
            clear_restart_counter_for_user_restart(&dev_clear_state);
        });
        log::info!(
            "[RESTART] dev-mode sidecar (VOICE_TYPER_SIDECAR_DEV=1): skipping \
             app.restart(); the supervisor will respawn the exiting sidecar \
             (host + `tauri dev` session stay alive)"
        );
        return;
    }

    let restart_for_async = app_handle.clone();
    let clear_state = state_inner.clone();
    tauri::async_runtime::spawn(async move {
        if let Err(join_err) = tauri::async_runtime::spawn_blocking(move || {
            clear_restart_counter_for_user_restart(&clear_state);
        })
        .await
        {
            log::warn!(
                "[RESTART] spawn_blocking(clear_restart_counter) join failed: {}, \
                 relaunched process may inherit the prior crash-loop count",
                join_err
            );
        }

        let teardown_started = std::time::Instant::now();
        log::info!(
            "[RESTART] beginning cooperative sidecar teardown before app.restart() \
             (budget {}ms)",
            PRE_RESTART_SIDECAR_GRACE_MS
        );
        shutdown_sidecar_for_exit_with_budget(&state_inner, PRE_RESTART_SIDECAR_GRACE_MS).await;
        log::info!(
            "[RESTART] cooperative sidecar teardown settled{}: proceeding to app.restart()",
            format_duration_suffix(teardown_started.elapsed())
        );

        // 3. Flush delay for the relaunch_ack + shutdown frames before
        //    the process goes away.
        tokio::time::sleep(std::time::Duration::from_millis(PRE_RESTART_FLUSH_DELAY_MS)).await;
        log::info!("[RESTART] calling app.restart()");
        restart_for_async.restart();
    });
}

pub(crate) fn on_quit_app(app_handle: &tauri::AppHandle) {
    let sidecar_state = app_handle.state::<Arc<SidecarState>>().inner().clone();
    if sidecar_state.begin_shutdown() {
        log::info!("[QUIT] quit_app event received, shutdown already in progress; exiting host");
    } else {
        log::info!(
            "[QUIT] quit_app event received: setting shutting_down + exiting host (tray Quit → app.exit)"
        );
    }
    app_handle.exit(0);
}

pub(crate) fn on_host_exit(app_handle: &tauri::AppHandle) {
    use std::time::Duration;

    let sidecar_state = app_handle.state::<Arc<SidecarState>>().inner().clone();
    let worker_state = app_handle.state::<Arc<WorkerState>>().inner().clone();
    worker_state
        .shutting_down
        .store(true, std::sync::atomic::Ordering::SeqCst);
    std::thread::spawn(move || {
        tauri::async_runtime::block_on(async move {
            let _ = tokio::time::timeout(
                Duration::from_millis(HOST_SHUTDOWN_GRACE_MS + 1000),
                shutdown_sidecar_for_exit(&sidecar_state),
            )
            .await;
            crate::sidecar::spawn::worker::stop_worker_child(&worker_state).await;
        });
    });
}
