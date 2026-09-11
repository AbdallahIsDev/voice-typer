#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::unreachable,
    clippy::todo,
    clippy::unimplemented,
    clippy::cast_possible_truncation
)]

//! Unit tests for `sidecar/lifecycle.rs` (relaunch-path teardown) and
//! the budget-parameterized sidecar teardown it drives
//! (`sidecar/shutdown.rs::shutdown_sidecar_for_exit_with_budget`).
//!
//! Sibling test file per the C-TEST-5 convention (wired via
//! `#[cfg(test)] mod lifecycle_tests;` in `sidecar/mod.rs`, mirroring
//! `supervisor_tests.rs` / `ws_tests.rs`). Items referenced from
//! `lifecycle.rs` are `pub(super)` so this sibling can reach them.
//!
//! Coverage focus: the tray-Restart relaunch contract:
//! 1. The pre-restart teardown arms `shutting_down` (begin_shutdown)
//!    AND sends the `{"type":"shutdown"}` WS frame, the two signals
//!    the old relaunch path never gave the sidecar before
//!    `app.restart()` hard-killed it.
//! 2. The teardown is idempotent. The `RunEvent::Exit` teardown that
//!    fires DURING `app.restart()` short-circuits instead of racing
//!    process exit.
//! 3. The pre-restart grace budget honestly covers the sidecar's
//!    3-4s graceful cleanup window.
//! 4. The completion log line carries the C-LOG-2 duration suffix.
//! 5. `on_relaunch_app`'s source order pins the counter-clear and the
//!    teardown call BEFORE the restart call (AppHandle construction is
//!    not possible in unit tests, tauri's `test` feature is not
//!    enabled in this crate: so the call-order guard mirrors the
//!    established `include_str!` source-inspection pattern used by
//!    `supervisor_tests.rs::test_write_restart_counter_docstring_…`).

use crate::sidecar::lifecycle::{format_duration_suffix, PRE_RESTART_SIDECAR_GRACE_MS};
use crate::sidecar::shutdown::shutdown_sidecar_for_exit_with_budget;
use crate::state::SidecarState;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;
use tokio_tungstenite::tungstenite::Message;

const LIFECYCLE_SRC: &str = include_str!("lifecycle.rs");

/// Build a fresh `SidecarState` with a FAKE WS writer channel wired
/// into `state.ws_tx`, returning the state + the receiving end. The
/// teardown's frame send goes through `ws_tx.try_send`, so the test
/// observes exactly what the sidecar would receive on the socket.
fn state_with_fake_ws_writer() -> (Arc<SidecarState>, tokio::sync::mpsc::Receiver<Message>) {
    let state = Arc::new(SidecarState::new());
    let (tx, rx) = tokio::sync::mpsc::channel::<Message>(4);
    *state.ws_tx.lock().unwrap() = Some(tx);
    (state, rx)
}

// pre-restart teardown: begin_shutdown + shutdown frame ──────────

#[tokio::test]
async fn test_pre_restart_teardown_marks_shutdown_and_sends_shutdown_frame() {
    let (state, mut rx) = state_with_fake_ws_writer();

    // Small budget: with no child installed, the poll arm observes
    // "no child: already gone" and completes immediately; the budget
    // only bounds the pathological case. The production relaunch path
    // uses PRE_RESTART_SIDECAR_GRACE_MS (5s): the call SHAPE is
    // identical, only the budget constant differs.
    shutdown_sidecar_for_exit_with_budget(&state, 250).await;

    // begin_shutdown was attempted: the supervisor-visible flag is
    // armed, so no respawn races the restart window.
    assert!(
        state.shutting_down.load(Ordering::SeqCst),
        "pre-restart teardown must arm shutting_down (begin_shutdown) so the \
         supervisor never races the restart window with a pointless respawn"
    );

    // The {"type":"shutdown"} frame reached the WS writer channel —
    // the sidecar is TOLD the restart is coming instead of being
    // hard-killed mid-cleanup.
    let frame = rx
        .recv()
        .await
        .expect("the shutdown frame must reach the WS writer channel");
    match frame {
        Message::Text(text) => assert_eq!(
            text.to_string(),
            r#"{"type":"shutdown"}"#,
            "the pre-restart frame is the cooperative shutdown frame the Python \
             sidecar's graceful-exit path keys on"
        ),
        other => panic!("expected a Text shutdown frame, got {:?}", other),
    }
    // Exactly one frame: the teardown is not a frame flood.
    assert!(
        rx.try_recv().is_err(),
        "the pre-restart teardown must send exactly one shutdown frame"
    );
}

#[tokio::test]
async fn test_pre_restart_teardown_second_call_short_circuits_without_new_frame() {
    let (state, mut rx) = state_with_fake_ws_writer();

    // First call: full sequence (flag + frame + bounded wait).
    shutdown_sidecar_for_exit_with_budget(&state, 100).await;
    // Consume the one frame the first call sent.
    assert!(rx.recv().await.is_some());

    // Second call: `begin_shutdown()` reports a shutdown already in
    // flight → early return, no duplicate frame, no double kill. This
    // is what makes the RunEvent::Exit teardown that fires DURING
    // `app.restart()` a no-op guard instead of a second teardown
    // racing process exit.
    shutdown_sidecar_for_exit_with_budget(&state, 100).await;

    assert!(
        tokio::time::timeout(Duration::from_millis(150), rx.recv())
            .await
            .is_err(),
        "the second teardown call must short-circuit without sending another frame"
    );
    assert!(
        state.shutting_down.load(Ordering::SeqCst),
        "the shutdown flag must stay armed after the short-circuit"
    );
}

// pre-restart grace budget honesty ───────────────────────────────

#[test]
fn test_pre_restart_grace_budget_covers_sidecar_cleanup_window() {
    // The sidecar's own graceful cleanup takes 3-4s (WAL checkpoint,
    // crash-recovery flush, native hotkey binary teardown). A budget
    // below 4s structurally cannot cover it, that was exactly the old
    // 10ms flush-delay defect. The upper bound keeps the user-visible
    // Restart bounded (the force-kill backstop covers the cold-disk
    // tail; the 30s exit-path budget is NOT appropriate here).
    assert!(
        PRE_RESTART_SIDECAR_GRACE_MS >= 4_000,
        "grace budget {}ms is below the sidecar's 3-4s graceful cleanup window",
        PRE_RESTART_SIDECAR_GRACE_MS
    );
    assert!(
        PRE_RESTART_SIDECAR_GRACE_MS <= 10_000,
        "grace budget {}ms would hang a user-visible Restart",
        PRE_RESTART_SIDECAR_GRACE_MS
    );
}

// duration suffix format (C-LOG-2) ───────────────────────────────

#[test]
fn test_format_duration_suffix_sub_minute() {
    // Sub-minute durations render as ` N.Ns` WITH the single leading
    // space (callers splice with a bare `{}`, no extra space).
    assert_eq!(
        format_duration_suffix(Duration::from_millis(2_300)),
        " 2.3s"
    );
    assert_eq!(format_duration_suffix(Duration::from_millis(0)), " 0.0s");
}

#[test]
fn test_format_duration_suffix_minutes() {
    // Anything a minute or longer renders as ` Nm N.Ns`, same shape
    // as Python's `voice_typer/server/duration.py::format_duration`.
    assert_eq!(
        format_duration_suffix(Duration::from_millis(62_300)),
        " 1m 2.3s"
    );
}

#[test]
fn test_pre_restart_completion_line_carries_duration_suffix() {
    // C-LOG-2: the timed-completion line for the pre-restart teardown
    // splices the duration suffix (leading space included) with a bare
    // `{}` placeholder: never a hand-rolled `{:?}`/`{:.1}s` inline.
    assert!(
        LIFECYCLE_SRC.contains("teardown settled{}: proceeding to app.restart()"),
        "the pre-restart teardown completion log line must carry the C-LOG-2 \
         duration suffix"
    );
}

// relaunch-path call order (source wiring guard) ─────────────────

#[test]
fn test_on_relaunch_app_counter_clear_and_teardown_precede_restart() {
    let fn_start = LIFECYCLE_SRC
        .find("pub(crate) fn on_relaunch_app")
        .expect("on_relaunch_app must exist in lifecycle.rs");
    let body = &LIFECYCLE_SRC[fn_start..];
    let body_end = body
        .find("pub(crate) fn on_quit_app")
        .expect("on_quit_app follows on_relaunch_app");
    let body = &body[..body_end];

    let clear_call = body
        .find("clear_restart_counter_for_user_restart(&clear_state)")
        .expect(
            "the production branch must clear the user-restart counter via \
             spawn_blocking before restarting",
        );
    let dev_clear_call = body
        .find("clear_restart_counter_for_user_restart(&dev_clear_state)")
        .expect("the dev branch must also clear the user-restart counter");
    let dev_early_return = body
        .find("return;")
        .expect("the dev branch must have an early return");
    let teardown_call = body
        .find("shutdown_sidecar_for_exit_with_budget(&state_inner")
        .expect(
            "the production branch must run the bounded pre-restart sidecar \
             teardown",
        );
    let restart_call = body
        .find("restart_for_async.restart()")
        .expect("the production branch must call app.restart()");

    assert!(
        clear_call < restart_call,
        "the user-restart counter clear must happen BEFORE app.restart(): \
         otherwise the relaunched process inherits the tripped crash-loop \
         breaker and the next single sidecar failure shows the reinstall prompt"
    );
    assert!(
        teardown_call < restart_call,
        "the cooperative pre-restart teardown (begin_shutdown + shutdown frame \
         + bounded graceful-exit wait + force-kill backstop) must happen BEFORE \
         app.restart(): otherwise the restart hard-kills a still-alive sidecar \
         mid-cleanup (history DB checkpoint, crash-recovery flush, native \
         hotkey teardown)"
    );
    assert!(
        dev_clear_call < dev_early_return,
        "the dev branch must clear the counter BEFORE its early return: the \
         user asked for a fresh attempt budget in dev too"
    );
}
