#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::unreachable,
    clippy::todo,
    clippy::unimplemented,
    clippy::cast_possible_truncation,
    clippy::assertions_on_constants
)] // const invariant pins with descriptive runtime messages

//! Unit tests for the shared constants that remain in `util.rs`.
//!
//! The token/session-id tests moved with their code to
//! `util/crypto_tests.rs`, the timestamp tests to `util/time_tests.rs`,
//! and the atomic-fs tests to `util/atomic_fs_tests.rs` when those
//! concerns were split into submodules (tests move with their code).
//! The constants block stayed in `util.rs` itself (the Python
//! source-inspection tests in `tests/tauri/mig15|16|17` regex these
//! `pub(crate) const` declarations against this file's raw source), so
//! the constants' Rust-side pins stay here. No test logic changed;
//! `use super::*;` still resolves to the parent module (`util`)
//! because the parent file declares this module via
//! `#[cfg(test)] mod util_tests;`.

use super::*;

// `SUPERVISOR_MAX_RETRIES` lives at module scope in `util.rs` (not here)
// so the Python source-inspection regex in tests/tauri/mig*/test_*.py
// keeps matching against `util.rs`. It's imported here via `use super::*;`
// (it's `pub(crate)`), so the test fns below reference it unqualified.

//supervisor backoff constants (ADR-0020 §10) ─────────────────

#[test]
fn test_supervisor_backoff_constants() {
    // ADR-0020 §10: supervisor backoff schedule + retry cap.
    // The schedule doubles each step (500ms → 1s → 2s → 4s → 8s)
    // and the cap is 5 retries before full-app relaunch.
    assert_eq!(
        SUPERVISOR_BACKOFF_MS,
        &[500, 1000, 2000, 4000, 8000],
        "SUPERVISOR_BACKOFF_MS must be [500, 1000, 2000, 4000, 8000] (doubling schedule)"
    );
    assert_eq!(
        SUPERVISOR_MAX_RETRIES, 5,
        "SUPERVISOR_MAX_RETRIES must be 5 (then fall back to full-app relaunch)"
    );
    // The schedule length must match the retry cap so the loop in
    // `respawn_inner` actually iterates SUPERVISOR_MAX_RETRIES times
    // (each iteration sleeps delay_ms[attempt] before retrying)
    // before falling back to `app.restart()`.
    assert_eq!(
        SUPERVISOR_BACKOFF_MS.len() as u32,
        SUPERVISOR_MAX_RETRIES,
        "SUPERVISOR_BACKOFF_MS.len() must equal SUPERVISOR_MAX_RETRIES so the loop iterates exactly N times"
    );
    // Verify the doubling property explicitly — guards against an
    // accidental edit that breaks the geometric progression.
    for i in 1..SUPERVISOR_BACKOFF_MS.len() {
        assert_eq!(
            SUPERVISOR_BACKOFF_MS[i],
            SUPERVISOR_BACKOFF_MS[i - 1] * 2,
            "backoff step {} must be 2x step {} (got {} vs {})",
            i,
            i - 1,
            SUPERVISOR_BACKOFF_MS[i],
            SUPERVISOR_BACKOFF_MS[i - 1]
        );
    }
}

#[test]
fn test_shutdown_ack_timeout_constant() {
    // ADR-0020 §10: cooperative shutdown hard timeout. The sidecar
    // must ack `{"type":"shutdown"}` and exit within this window;
    // if it doesn't, the host force-kills the process tree.
    //polls `CommandEvent::Terminated` against this same
    // deadline via `tokio::time::timeout`.
    assert_eq!(
        SHUTDOWN_ACK_TIMEOUT_MS, 2000,
        "SHUTDOWN_ACK_TIMEOUT_MS must be 2000 (2s graceful window - UI-active path only)"
    );
}

/// The exit-path cooperative timeout must be 30s. This is the
/// budget for `shutdown_sidecar_for_exit` (the `RunEvent::Exit`
/// last-resort teardown), NOT the UI-active `shutdown_sidecar`
/// command (which keeps the 2s `SHUTDOWN_ACK_TIMEOUT_MS`). The
/// 30s budget gives the sidecar time to run its full audited
/// cleanup (history_db.flush, crash_recovery.flush, native
/// hotkey binary teardown, WAL checkpoint) before the host's
/// force-kill backstop fires — preventing WAL corruption + native
/// binary orphan that the prior 2s budget caused.
#[test]
fn test_exit_shutdown_ack_timeout_constant() {
    assert_eq!(
        EXIT_SHUTDOWN_ACK_TIMEOUT_MS, 30_000,
        "EXIT_SHUTDOWN_ACK_TIMEOUT_MS must be 30000 (30s exit-path cooperative window)"
    );
    // Invariant: the exit-path budget must be STRICTLY GREATER
    // than the UI-active budget. If they ever become equal, the
    // exit path regresses the UI-freeze protection OR the UI path
    // undercuts the sidecar's full cleanup window. Either is a bug.
    assert!(
        EXIT_SHUTDOWN_ACK_TIMEOUT_MS > SHUTDOWN_ACK_TIMEOUT_MS,
        "EXIT_SHUTDOWN_ACK_TIMEOUT_MS ({}) must be > SHUTDOWN_ACK_TIMEOUT_MS ({}) — the exit path needs a longer budget than the UI-active path",
        EXIT_SHUTDOWN_ACK_TIMEOUT_MS,
        SHUTDOWN_ACK_TIMEOUT_MS
    );
}
