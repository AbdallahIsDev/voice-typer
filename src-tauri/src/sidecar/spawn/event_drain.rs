
use tauri_plugin_shell::process::CommandEvent;
use tokio::sync::mpsc;

use crate::sidecar::child_log::{should_tee, tee_child_output, ChildStream};

fn tee(log_tag: &'static str, stream: ChildStream, bytes: &[u8]) {
    if should_tee(log_tag) {
        tee_child_output(stream, bytes);
    }
}

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

pub(crate) async fn drain_child_events(
    log_tag: &'static str,
    mut rx: mpsc::Receiver<CommandEvent>,
    exit_tx: mpsc::Sender<CommandEvent>,
) {
    while let Some(event) = rx.recv().await {
        match event {
            CommandEvent::Stdout(bytes) => {
                tee(log_tag, ChildStream::Stdout, &bytes);
                log::debug!(
                    "{} stdout: {}",
                    log_tag,
                    String::from_utf8_lossy(&bytes).trim()
                );
            }
            CommandEvent::Stderr(bytes) => {
                tee(log_tag, ChildStream::Stderr, &bytes);
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
