//! Sibling tests for `sidecar::spawn::event_drain` (per C-TEST-5 —
//! sibling test file, no inline tests in production source).
//!
//! Drives [`drain_child_events`] directly with plain tokio channels —
//! no child process, no Tauri runtime (the task-spawning wrapper
//! `spawn_child_event_drain` is a three-line `tauri::async_runtime::
//! spawn` around this body).
//!
//! Pins the contract:
//!
//! - **Back-pressure relief (the root fix)**: the drain continuously
//!   `recv()`s, so the child's bounded `channel(1)` event stream never
//!   stays full: verified by draining a multi-event burst without the
//!   sender ever blocking.
//! - **Exit-event forwarding**: exactly the `Terminated` event is
//!   forwarded (best-effort `try_send`) to the returned receiver; the
//!   stream-then-closed shape matches what the shutdown exit wait
//!   polls.
//! - **Never blocks on an absent waiter**: a second `Terminated`
//!   (stale generation whose forwarded receiver was already replaced)
//!   fails `try_send` and is DISCARDED, and the drain keeps running —
//!   the drain itself can never be back-pressured by a gone receiver.
//! - **Survives `Error` events**: a pipe error is logged, not fatal —
//!   the drain continues until the channel closes.

use super::drain_child_events;
use tauri_plugin_shell::process::{CommandEvent, TerminatedPayload};
use tokio::sync::mpsc;

fn stderr_event(line: &str) -> CommandEvent {
    CommandEvent::Stderr(line.as_bytes().to_vec())
}

fn stdout_event(line: &str) -> CommandEvent {
    CommandEvent::Stdout(line.as_bytes().to_vec())
}

fn terminated_event(code: i32) -> CommandEvent {
    CommandEvent::Terminated(TerminatedPayload {
        code: Some(code),
        signal: None,
    })
}

#[tokio::test]
async fn test_drain_child_events_forwards_terminated_and_closes_with_stream() {
    let (tx, rx) = mpsc::channel(1);
    let (exit_tx, mut exit_rx) = mpsc::channel(1);
    // Feed a realistic post-handshake burst: stderr noise (the pipe
    // back-pressure class), an error event, then the exit event. The
    // capacity-1 REAL channel means the sender must wait for the
    // drain's recv()s: the drain relieves back-pressure by
    // construction.
    let feeder = tokio::spawn(async move {
        tx.send(stderr_event("ctranslate2 device=cuda")).await.unwrap();
        tx.send(stdout_event("stray stdout line")).await.unwrap();
        tx.send(stderr_event("second stderr line")).await.unwrap();
        tx.send(CommandEvent::Error("pipe read failed".to_string()))
            .await
            .unwrap();
        tx.send(terminated_event(0)).await.unwrap();
        // Drop the sender AFTER the Terminated, the plugin's exit
        // watcher closes the stream right after the exit event.
        drop(tx);
    });
    drain_child_events("[TEST]", rx, exit_tx).await;
    feeder.await.unwrap();
    // The forwarded receiver yields exactly the Terminated event.
    match exit_rx.recv().await {
        Some(CommandEvent::Terminated(payload)) => {
            assert_eq!(payload.code, Some(0), "forwarded exit code must match");
            assert_eq!(payload.signal, None);
        }
        other => panic!("expected forwarded Terminated, got {other:?}"),
    }
    // ... and then closes (stream closed after Terminated), the
    // closed-without-Terminated signal the shutdown exit wait
    // handles.
    assert!(
        exit_rx.recv().await.is_none(),
        "forwarded channel must close after the child's stream closes"
    );
}

#[tokio::test]
async fn test_drain_child_events_never_blocks_when_no_waiter() {
    // A stale generation's drain: the forwarded receiver was already
    // replaced (dropped) by a respawn, so the Terminated forward finds
    // NO waiter. The drain must discard the event and keep draining —
    // never park on a dead receiver (a parked drain would stop
    // relieving the real channel's back-pressure, re-introducing the
    // deadlock class this module exists to fix).
    let (tx, rx) = mpsc::channel(1);
    let (exit_tx, mut exit_rx) = mpsc::channel(1);
    // Consume + drop the forwarded receiver up front, simulate the
    // respawn replacing the slot before the exit event lands.
    drop(exit_rx);
    let feeder = tokio::spawn(async move {
        // First Terminated: try_send fills the capacity-1 channel
        // (receiver dropped → Closed error, discarded).
        tx.send(terminated_event(1)).await.unwrap();
        // A second event must still be drained, proving the drain
        // did not park on the failed forward.
        tx.send(stderr_event("post-exit noise")).await.unwrap();
        drop(tx);
    });
    // Bounded: if the drain parks (regression), this timeout elapses
    // and the test fails instead of hanging forever.
    let drained = tokio::time::timeout(
        std::time::Duration::from_secs(5),
        drain_child_events("[TEST]", rx, exit_tx),
    )
    .await;
    assert!(
        drained.is_ok(),
        "drain must complete (never park) when the forwarded receiver is gone"
    );
    feeder.await.unwrap();
}

#[tokio::test]
async fn test_drain_child_events_exits_promptly_on_stream_close() {
    // Child died without a Terminated reaching us (stream dropped):
    // the drain exits and the forwarded channel reads closed, the
    // "event stream closed without Terminated" leg of the exit wait.
    let (tx, rx) = mpsc::channel(1);
    let (exit_tx, mut exit_rx) = mpsc::channel(1);
    drop(tx);
    let started = std::time::Instant::now();
    drain_child_events("[TEST]", rx, exit_tx).await;
    assert!(
        started.elapsed() < std::time::Duration::from_secs(5),
        "drain must exit promptly on stream close"
    );
    assert!(
        exit_rx.recv().await.is_none(),
        "no Terminated forwarded: channel must read closed"
    );
}

#[tokio::test]
async fn test_drain_child_events_relieves_backpressure_on_bounded_channel() {
    // The real channel is capacity 1 with backpressure (the plugin's
    // exact shape). A child emitting more stderr than one event's
    // worth must not have its writer blocked: the drain's continuous
    // recv() keeps the channel empty. Verified by sending 64 events
    // (a burst larger than any buffer) to completion while the drain
    // runs concurrently, then observing the forwarded Terminated.
    let (tx, rx) = mpsc::channel(1);
    let (exit_tx, mut exit_rx) = mpsc::channel(1);
    let feeder = tokio::spawn(async move {
        for i in 0..64 {
            tx.send(stderr_event(&format!("noise line {i}"))).await.unwrap();
        }
        tx.send(terminated_event(0)).await.unwrap();
        drop(tx);
    });
    let drained =
        tokio::time::timeout(std::time::Duration::from_secs(5), drain_child_events("[TEST]", rx, exit_tx))
            .await;
    assert!(drained.is_ok(), "drain must keep up with a 64-event burst");
    feeder.await.unwrap();
    assert!(
        matches!(exit_rx.recv().await, Some(CommandEvent::Terminated(_))),
        "the burst's exit event must still be forwarded"
    );
}
