//! Permanent child-event drain for release-mode children.
//!
//! tauri-plugin-shell (2.3.5) spawns each child with a bounded
//! `channel(1)` event stream and backpressure: TWO pipe-reader
//! threads (stdout + stderr) and the exit-wait thread park inside
//! `tx.send(...).await` while the channel is full. The host drains
//! the receiver during the stdout handshake, then, without this
//! module: parks it in `state.child_exit_rx` untouched until app
//! exit. Post-handshake child stderr beyond the OS pipe buffer
//! (~64 KB Linux / ≤65,535 B Windows) therefore blocks the child's
//! writer threads: engine device dumps on model load, Python warning
//! frames, crash tracebacks, idle-unload reload cycles. The same
//! applies to the ML worker child, whose onnxruntime/ctranslate2
//! startup banners are the single largest stderr producer.
//!
//! Dev-mode children are immune (stderr is inherited by the terminal,
//! no event channel) and spawn without a receiver, nothing here
//! touches them.
//!
//! # Design
//!
//! [`spawn_child_event_drain`] takes ownership of the REAL event
//! receiver and returns a FORWARDED receiver in its place, so every
//! existing consumer of `state.child_exit_rx` (spawn install,
//! supervisor respawn install, the exit-wait in
//! `sidecar/shutdown.rs`) keeps working unchanged:
//!
//! - The drain task `recv()`s continuously, the bounded channel
//!   never stays full, so the plugin's pipe-reader threads never
//!   park and the child's stderr/stdout writes never block. This is
//!   the root fix.
//! - `Stdout`/`Stderr` lines are logged at `debug!` (the child's own
//!   log file already carries its warnings; the host copies are for
//!   `VOICE_TYPER_DEBUG` triage). `Error` events are logged at
//!   `warn!`.
//! - `Terminated` is forwarded to the returned receiver via
//!   `try_send` on a capacity-1 channel, never blocking, so the
//!   drain itself cannot be back-pressured by an absent waiter. The
//!   30 s exit wait in `shutdown.rs` therefore observes `Terminated`
//!   (instead of a stale `Stderr` line as its first event, which used
//!   to trip the "unexpected event" arm and force-kill immediately —
//!   squandering the cooperative WAL-checkpoint window).
//! - When the child dies and its event channel closes, the drain task
//!   exits; the forwarded channel then reads `None` (stream closed) —
//!   the same closed-without-Terminated signal the exit wait already
//!   handles.
//!
//! # What the drain deliberately does NOT do
//!
//! It does NOT route `Terminated` into the supervisor's respawn path.
//! A process-exit event carries no WebSocket generation, and the
//! respawn scheduler's dequeue-time staleness re-check only skips
//! requests that CARRY a generation, a generation-blind respawn
//! request landing after a newer reconnect went live would kill the
//! healthy connection, the exact kill/restart ping-pong the WS
//! generation contract exists to prevent. Crash detection stays owned
//! by the WS reader (socket close) and heartbeat-liveness paths,
//! which carry proper generation semantics; the drain's job is
//! back-pressure relief and exit-event forwarding, not respawn
//! policy.
//!
//! Per-generation cleanup is automatic: each respawn installs a NEW
//! forwarded receiver into `state.child_exit_rx`, dropping the old
//! one: the old drain's next `try_send` fails (receiver gone), the
//! old child's channel closes after its `Terminated`, and the old
//! drain task exits. No handles to track, nothing to abort.

use tauri_plugin_shell::process::CommandEvent;
use tokio::sync::mpsc;

/// Take ownership of a release-mode child's event receiver and return
/// a forwarded receiver that yields the child's `Terminated` exit
/// event.
///
/// Called by the release spawn paths (`release_mode.rs` for the
/// sidecar, `spawn.rs::initialize_worker` for the ML worker) right
/// after the stdout handshake completes, the point at which the
/// handshake loop stops draining and the back-pressure window would
/// otherwise open.
///
/// The returned receiver yields AT MOST one `Terminated` event, then
/// closes when the child's event stream closes, the exact contract
/// `shutdown_sidecar_for_exit`'s exit wait polls.
pub(super) fn spawn_child_event_drain(
    log_tag: &'static str,
    rx: mpsc::Receiver<CommandEvent>,
) -> mpsc::Receiver<CommandEvent> {
    let (exit_tx, exit_rx) = mpsc::channel(1);
    tauri::async_runtime::spawn(async move {
        drain_child_events(log_tag, rx, exit_tx).await;
    });
    exit_rx
}

/// Drain task body: continuously `recv()` from the child's real event
/// channel, logging non-exit events and forwarding `Terminated`.
///
/// Split from [`spawn_child_event_drain`] as a directly-awaitable
/// `pub(crate)` function so the sibling tests can drive the loop with
/// plain channels: no child process, no Tauri runtime.
pub(crate) async fn drain_child_events(
    log_tag: &'static str,
    mut rx: mpsc::Receiver<CommandEvent>,
    exit_tx: mpsc::Sender<CommandEvent>,
) {
    while let Some(event) = rx.recv().await {
        match event {
            CommandEvent::Stdout(bytes) => {
                // Post-handshake stdout is abnormal (the handshake
                // line was already consumed) but harmless, log at
                // debug and keep draining.
                log::debug!(
                    "{} stdout: {}",
                    log_tag,
                    String::from_utf8_lossy(&bytes).trim()
                );
            }
            CommandEvent::Stderr(bytes) => {
                // The child's stderr can be extremely chatty (engine
                // device dumps, Python warning frames), debug level
                // only; the child's own rotating log already carries
                // its warnings/errors.
                log::debug!(
                    "{} stderr: {}",
                    log_tag,
                    String::from_utf8_lossy(&bytes).trim()
                );
            }
            CommandEvent::Error(err) => {
                log::warn!("{} event stream error: {}", log_tag, err);
            }
            CommandEvent::Terminated(payload) => {
                log::info!(
                    "{} process terminated (code={:?}, signal={:?})",
                    log_tag,
                    payload.code,
                    payload.signal
                );
                // Best-effort, NEVER-blocking forward: with no waiter
                // (normal runtime: the exit wait has not started) the
                // event stays buffered in the capacity-1 channel; with
                // the receiver already replaced by a respawn (stale
                // generation) or dropped, the send fails and is
                // discarded: the respawn path owns crash detection.
                if let Err(send_err) = exit_tx.try_send(CommandEvent::Terminated(payload)) {
                    log::debug!(
                        "{} exit-event forward dropped (no active waiter): {}",
                        log_tag,
                        send_err
                    );
                }
                // Keep draining: the event stream closes right after
                // Terminated, which ends this task.
            }
            // Future CommandEvent variants: drain and ignore.
            _ => {}
        }
    }
}

// Sibling test module: tests live in `event_drain_tests.rs` (per
// C-TEST-5: no inline `#[cfg(test)] mod tests` blocks in production
// source).
#[cfg(test)]
#[path = "event_drain_tests.rs"]
mod event_drain_tests;
