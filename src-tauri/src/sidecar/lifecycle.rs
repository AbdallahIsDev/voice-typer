//! Host lifecycle callbacks: relaunch / quit / exit teardown.
//!
//! Relocated verbatim from `state.rs` (pure move, no behavior change) so
//! the shared-state module stays data-only. `state.rs` re-exports these
//! names, so existing `crate::state::on_relaunch_app` /
//! `crate::state::on_quit_app` / `crate::state::on_host_exit` call sites
//! (e.g. `main.rs`'s listener registrations + `RunEvent::Exit` handler)
//! keep resolving unchanged.

use crate::sidecar::shutdown::shutdown_sidecar_for_exit_with_budget;
use crate::sidecar::supervisor::clear_restart_counter_for_user_restart;
use crate::sidecar::{send_fire_and_forget_frame, shutdown_sidecar_for_exit};
use crate::state::SidecarState;
use crate::state::WorkerState;
use std::sync::Arc;
use tauri::Manager;

/// Local override for the host's `RunEvent::Exit` shutdown budget.
///
/// `util::SHUTDOWN_ACK_TIMEOUT_MS` is 2000ms (2s): but the sidecar's
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
/// 2s budget (`SHUTDOWN_ACK_TIMEOUT_MS`): there a tight budget is
/// appropriate because the UI is still alive and a long block freezes
/// it. The `RunEvent::Exit` path is when the host is going away, it
/// should err on the side of giving the sidecar more time.
const HOST_SHUTDOWN_GRACE_MS: u64 = 35_000;

/// Wait budget for the COOPERATIVE pre-restart sidecar teardown on the
/// tray-Restart relaunch path (`on_relaunch_app`).
///
/// The sidecar's own graceful cleanup (WAL checkpoint, crash-recovery
/// flush, native hotkey binary teardown) takes 3-4s, so the old
/// `PRE_RESTART_FLUSH_DELAY_MS` (10ms) could never cover it, the
/// restart structurally hard-killed a still-alive backend mid-flush.
/// 5s = the 3-4s cleanup plus headroom for the WS round-trip; it is
/// deliberately FAR below the exit path's 30s
/// `EXIT_SHUTDOWN_ACK_TIMEOUT_MS` / 35s `HOST_SHUTDOWN_GRACE_MS`
/// because a user-visible Restart must not hang the app on a
/// cold-disk worst case: if the sidecar hasn't exited within this
/// budget, the teardown's force-kill backstop reaps the process tree
/// the same way the OS-level exit path would (and `app.restart()`'s
/// `RunEvent::Exit` teardown then short-circuits on the already-set
/// `shutting_down` flag).
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
        // A user-initiated restart resets the crash-loop counter in dev
        // too (fresh attempt budget for the supervisor respawn). The
        // write is fire-and-forget `spawn_blocking` here: no host
        // restart follows this branch, so nothing needs to be ordered
        // after the write, and it must not stall the event-loop thread
        // this listener runs on (the counter write is an atomic
        // temp-file write + fsync + rename).
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
        // 1. User-restart counter reset: OFF the async worker
        //    (spawn_blocking: atomic temp-file write + fsync + rename)
        //    and AWAITED so it is ordered before `app.restart()`. If
        //    the join fails, the relaunched process inherits the prior
        //    count: logged, best-effort (same semantics as the write
        //    itself failing).
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

        // 2. Cooperative pre-restart teardown (bounded). Arms
        //    `shutting_down` (begin_shutdown), tells the sidecar via
        //    the shutdown frame, waits up to the grace budget for a
        //    graceful exit, and force-kills the tree as backstop.
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
///    teardown is idempotent: it short-circuits on the already-set
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
    // the notify is a benign spurious wakeup, the supervisor re-checks
    // the flag and goes back to sleep, and `shutdown_sidecar_for_exit`
    // also fires its own (idempotent) notify on the `RunEvent::Exit` path.
    if sidecar_state.begin_shutdown() {
        log::info!("[QUIT] quit_app event received, shutdown already in progress; exiting host");
    } else {
        log::info!(
            "[QUIT] quit_app event received: setting shutting_down + exiting host (tray Quit → app.exit)"
        );
    }
    app_handle.exit(0);
}

/// `RunEvent::Exit` / `ExitRequested` callback body, extracted from
/// `main.rs`'s inline `.run(callback)` closure so the host entrypoint
/// stays wiring-only.
///
/// Spawns the sidecar teardown on a dedicated std thread (NOT a tokio
/// task) so the Tauri event loop returns immediately, `block_on` can
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
    // BP-33 (Phase 2c): the worker is host-managed too, mark it
    // quitting (blocks a concurrent verified-trigger (re)start) and
    // take its child for the force-kill below. Missing state would
    // panic, but main.rs always manages WorkerState unconditionally.
    let worker_state = app_handle.state::<Arc<WorkerState>>().inner().clone();
    worker_state.shutting_down.store(true, std::sync::atomic::Ordering::SeqCst);
    std::thread::spawn(move || {
        tauri::async_runtime::block_on(async move {
            let _ = tokio::time::timeout(
                Duration::from_millis(HOST_SHUTDOWN_GRACE_MS + 1000),
                shutdown_sidecar_for_exit(&sidecar_state),
            )
            .await;
            // Worker teardown AFTER the sidecar (the sidecar owns the
            // worker WS client: it must go down first). Force-kill,
            // best-effort: no graceful worker protocol exists yet
            // (plan §7.3 graceful close is TBD with the WS bridge).
            crate::sidecar::spawn::worker::stop_worker_child(&worker_state).await;
        });
    });
}
