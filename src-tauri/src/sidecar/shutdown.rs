//! Sidecar app-exit teardown + fire-and-forget WS frame send
//! (ADR-0020 §1 + §10 + §14).
//!
//! Extracted from `state.rs` so the shared-state module stays focused
//! on `SidecarState`/`WorkerState` data, while the sidecar-shutdown
//! machinery (cooperative `{"type":"shutdown"}` handshake + force-kill
//! backstop + bounded sleep poll for dev-mode) lives next to the other
//! sidecar-lifecycle modules. `state.rs` re-exports both functions so
//! existing `crate::state::shutdown_sidecar_for_exit` /
//! `crate::state::send_fire_and_forget_frame` imports keep resolving.

use crate::state::{lock, SidecarState};
use crate::util::EXIT_SHUTDOWN_ACK_TIMEOUT_MS;
use std::sync::Arc;
use tauri_plugin_shell::process::CommandEvent;
use tokio_tungstenite::tungstenite::Message;

//app-exit sidecar teardown ────────────────────────────────

/// Best-effort sidecar teardown for app-exit paths that can't run the
/// full cooperative `shutdown_sidecar` Tauri command (e.g.
/// `RunEvent::Exit` from the Tauri event loop, which fires on
/// `app.exit()` / quit-tray / Ctrl-C / SIGTERM).
///
/// **Idempotent** via `SidecarState::begin_shutdown()` (the canonical
/// `shutting_down.swap(true, SeqCst)` + supervisor-wakeup
/// `notify_one()` pair) — returns immediately if a shutdown is already
/// in flight (either the renderer's `shutdown_sidecar` command, a prior
/// `ExitRequested`, or tray Quit's `on_quit_app`). This makes it safe
/// to call from both `ExitRequested` AND `Exit` (which can fire
/// back-to-back) without double-killing.
///
/// Sequence:
/// 1. Set `shutting_down` (idempotency guard) + wake the supervisor
///    (`begin_shutdown()`).
/// 2. Send the `{"type":"shutdown"}` WS frame (best-effort — skipped
///    if the WS is already torn down).
/// 3. Wait up to `EXIT_SHUTDOWN_ACK_TIMEOUT_MS` (30s) for the sidecar
///    to exit gracefully (polling the `CommandEvent` receiver if
///    present; bounded sleep for dev-mode). The exit path uses the
///    longer 30s budget (vs the renderer-invoked `shutdown_sidecar`
///    command's 2s `SHUTDOWN_ACK_TIMEOUT_MS`) because the host is
///    already going away and the sidecar's audited worst-case
///    cooperative cleanup (history_db.flush, crash_recovery.flush,
///    native hotkey binary teardown, WAL checkpoint) can legitimately
///    take ~30s on a cold disk. Force-killing mid-cleanup risks WAL
///    corruption + native-binary orphan.
/// 4. Force-kill the process tree via `SidecarHandle::kill_tree` so
///    grandchildren (native hotkey binary, model subprocesses) are
///    reaped too.
///
/// The caller (in `main.rs`'s `RunEvent` callback) wraps this in
/// `tauri::async_runtime::block_on` + `tokio::time::timeout` so the
/// run loop never hangs on a misbehaving sidecar.
pub(crate) async fn shutdown_sidecar_for_exit(state: &Arc<SidecarState>) {
    use std::time::Duration;

    // Idempotency guard + supervisor wakeup, as the atomic adjacent pair
    // (`shutting_down.swap(true, SeqCst)` immediately followed by
    // `shutdown_notify.notify_one()`). `begin_shutdown()` returns the
    // PREVIOUS flag value, so `true` here means a teardown is already in
    // flight. On that path `begin_shutdown()` has already fired a
    // best-effort `notify_one()` before we return — a benign spurious
    // wakeup: the supervisor re-checks `shutting_down` when it wakes and
    // goes back to sleep.
    if state.begin_shutdown() {
        log::info!("[EXIT-SHUTDOWN] shutting_down already set — skipping duplicate teardown");
        return;
    }

    //abort the heartbeat task so it doesn't keep
    // dispatching `heartbeat` frames into the dead WS.
    {
        let mut hb_guard = state.heartbeat_handle.lock().await;
        if let Some(handle) = hb_guard.take() {
            handle.abort();
            log::info!("[EXIT-SHUTDOWN] aborted in-flight heartbeat task");
        }
    }

    // Send the shutdown frame (best-effort).
    //log on failure so a stuck writer task isn't silent.
    let frame = serde_json::json!({"type": "shutdown"});
    if let Some(ws_tx) = lock(&state.ws_tx).clone() {
        if let Err(e) = ws_tx.try_send(Message::Text(frame.to_string().into())) {
            log::warn!(
                "[EXIT-SHUTDOWN] try_send of shutdown frame failed (best-effort): {}",
                e
            );
        }
    } else {
        log::info!("[EXIT-SHUTDOWN] no ws_tx — skipping cooperative shutdown frame");
    }

    // Wait up to EXIT_SHUTDOWN_ACK_TIMEOUT_MS (30s) for graceful exit.
    // The exit path uses a longer budget than the renderer-invoked
    // `shutdown_sidecar` command (2s) because the host is going away
    // and the sidecar's cleanup (WAL checkpoint, native hotkey binary
    // teardown) can legitimately take ~30s on a cold disk.
    //mirror the `shutdown_sidecar` Tauri command's logging.
    let deadline = Duration::from_millis(EXIT_SHUTDOWN_ACK_TIMEOUT_MS);
    let mut graceful = false;
    // Take the receiver OUT of the shared slot under a brief lock, then
    // DROP the lock guard before awaiting `rx.recv()`. Holding the
    // `AsyncMutex` guard across the up-to-30s `tokio::time::timeout`
    // await blocked any other code path that needed `child_exit_rx`
    // (e.g. a concurrent `Exit` + `ExitRequested` callback pair, or a
    // late renderer `shutdown_sidecar` command) for the entire grace
    // window — even though the idempotency guard already short-circuits
    // duplicate teardowns, the lock itself was still contended. This
    // path is the app's terminal exit, so leaving the slot `None` after
    // `take()` is fine (no later code needs the receiver back; the
    // process is going away).
    let rx_opt = {
        let mut rx_guard = state.child_exit_rx.lock().await;
        rx_guard.take()
    };
    if let Some(mut rx) = rx_opt {
        match tokio::time::timeout(deadline, rx.recv()).await {
            Ok(Some(CommandEvent::Terminated(payload))) => {
                log::info!(
                    "[EXIT-SHUTDOWN] sidecar exited gracefully (code={:?}, signal={:?})",
                    payload.code,
                    payload.signal
                );
                graceful = true;
            }
            Ok(Some(other)) => {
                log::warn!(
                    "[EXIT-SHUTDOWN] unexpected event while waiting for termination: {:?}",
                    other
                );
            }
            Ok(None) => {
                log::warn!("[EXIT-SHUTDOWN] sidecar event stream closed without Terminated");
            }
            Err(_) => {
                log::warn!(
                    "[EXIT-SHUTDOWN] sidecar did not exit within {}ms — force-killing",
                    EXIT_SHUTDOWN_ACK_TIMEOUT_MS
                );
            }
        }
    } else {
        log::info!(
            "[EXIT-SHUTDOWN] dev-mode sidecar — polling for exit (up to {}ms, 100ms interval) before force-kill",
            EXIT_SHUTDOWN_ACK_TIMEOUT_MS
        );
        // Poll `SidecarHandle::try_wait()` in a bounded loop with a
        // 100ms sleep, breaking early when the dev-mode child has been
        // reaped by the OS. Cuts the dev-mode teardown from 30s →
        // ~100ms on a cooperative dev sidecar (the typical case: the
        // `{"type":"shutdown"}` frame is delivered, the dev sidecar
        // exits within milliseconds, and the next poll observes the
        // reaped pid). The sync `Mutex` lock on `state.child` is held
        // only for the duration of the `try_wait()` syscall
        // (microseconds — `waitpid(WNOHANG)` is a non-blocking kernel
        // call), never across the `tokio::time::sleep` await — so
        // other code paths needing `state.child` (e.g. supervisor
        // respawn, which takes + kills the slot) are not blocked.
        // On ShellPlugin (release builds) this arm is unreachable:
        // `child_exit_rx` is always `Some` for ShellPlugin, so the
        // `if let Some(mut rx) = rx_opt` branch above is taken
        // instead. `try_wait()` returns `Ok(None)` for ShellPlugin
        // anyway, so the poll loop would just wait out the deadline
        // (matching the prior behavior of `tokio::time::sleep(deadline)`).
        let poll_step = Duration::from_millis(100);
        let poll_deadline = tokio::time::Instant::now() + deadline;
        loop {
            let reaped = {
                let mut guard = lock(&state.child);
                match guard.as_mut() {
                    Some(handle) => match handle.try_wait() {
                        Ok(Some(exited)) => exited,
                        Ok(None) => false, // ShellPlugin — no poll, wait for deadline
                        Err(_) => false,   // best-effort, don't fail the shutdown
                    },
                    None => true, // No child — already gone
                }
            };
            if reaped {
                graceful = true;
                log::info!(
                    "[EXIT-SHUTDOWN] dev-mode sidecar exited gracefully (reaped within {}ms budget)",
                    EXIT_SHUTDOWN_ACK_TIMEOUT_MS
                );
                break;
            }
            if tokio::time::Instant::now() >= poll_deadline {
                break;
            }
            let now = tokio::time::Instant::now();
            let remaining = if now < poll_deadline {
                poll_deadline - now
            } else {
                Duration::ZERO
            };
            tokio::time::sleep(std::cmp::min(remaining, poll_step)).await;
        }
    }

    // Force-kill backstop. Gate on `!graceful` — if the sidecar exited
    // cooperatively, the grandchildren (native hotkey binary, model
    // subprocesses) were already reaped by the sidecar itself; we still
    // `take()` the child handle (dropping it cleanly) but skip the
    // recursive `kill_tree`. If `graceful` is false (timeout /
    // unexpected exit), `kill_tree` is the force-kill backstop that
    // also walks the process tree to reap grandchildren the sidecar
    // didn't clean up.
    let child_opt = lock(&state.child).take();
    if let Some(child) = child_opt {
        if !graceful {
            if let Err(e) = child.kill_tree().await {
                log::warn!("[EXIT-SHUTDOWN] kill_tree failed (best-effort): {}", e);
            }
        }
    }

    //final summary line.
    log::info!(
        "[EXIT-SHUTDOWN] sidecar teardown complete graceful={}",
        graceful
    );
}

//ADR-0020 module-layout gate: fire-and-forget WS frame send
/// used by ``main.rs``'s ``relaunch_app`` listener. Extracted from the
/// host entrypoint so ``main.rs`` stays wiring-only (no direct
/// ``tungstenite::`` reference — see the
/// ``test_main_rs_has_no_business_logic_patterns`` gate in
/// ``tests/tauri/mig19/test_final_glue.py``).
///
/// Assigns the next monotonic id, builds a ``{"type":<frame_type>,
/// "data":{}, "id":<id>}`` frame, and enqueues it on the WS writer
/// channel via ``try_send`` (non-blocking).
///
/////# Return value semantics ()
///
/// The returned `Option<u64>` is for **tracing only** — it does NOT
/// indicate whether the frame was successfully delivered to the WS
/// writer task. Specifically:
///
/// - `None`: there is no `ws_tx` (the WS was already torn down — the
///   caller should treat this as "no sidecar connected").
/// - `Some(id)`: an id was assigned for the frame. **The frame may or
///   may not have been enqueued.** If `try_send` failed (e.g. WS
///   channel full or closed), the failure is logged at `warn` level
///   and `Some(id)` is still returned — the id is purely a tracing
///   artifact and the peer will time out waiting for a response.
///
/// Callers that need to know whether the frame was actually sent
/// should use the full `dispatch_frame` path (which returns a
/// `Result`). This helper exists for fire-and-forget frames where the
/// caller cannot react to a send failure anyway (e.g. `relaunch_app`
/// — the process is about to exit regardless).
pub(crate) fn send_fire_and_forget_frame(
    state: &Arc<SidecarState>,
    frame_type: &str,
) -> Option<u64> {
    use std::sync::atomic::Ordering;
    let ws_tx = lock(&state.ws_tx).clone()?;
    let id = state.next_id.fetch_add(1, Ordering::Relaxed);
    let frame = serde_json::json!({
        "type": frame_type,
        "data": {},
        "id": id,
    });
    match ws_tx.try_send(Message::Text(frame.to_string().into())) {
        Ok(_) => log::info!("[WS] {} frame sent (id={})", frame_type, id),
        Err(e) => log::warn!(
            "[WS] failed to send {} frame (id={}): {} — peer will wait for its timeout",
            frame_type,
            id,
            e
        ),
    }
    Some(id)
}
