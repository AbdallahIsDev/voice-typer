//! Unit tests for the `restart_sidecar` command's pure gate
//! ([`crate::commands::sidecar_cmds::restart`]).
//!
//! The command itself needs a live `tauri::AppHandle` + `tauri::Window`
//! (auto-injected params), so the decision surface is pinned through the
//! pure gate: the renderer must be told the truth (`ok: false`) when the
//! host is shutting down instead of receiving `ok: true` for a no-op.

#![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

use super::{adopted_blocked_envelope, restart_blocked_envelope};
use serde_json::json;

/// A restart attempted while the host is shutting down is refused with
/// the predecessor-compatible envelope (so `useConnection.ts` shows the
/// relaunch hint), never a silent success.
#[test]
fn test_restart_blocked_while_shutting_down() {
    let envelope = restart_blocked_envelope(true).expect("shutting_down must block the restart");
    assert_eq!(envelope, json!({"ok": false, "reason": "shutting-down"}));
}

/// A restart while the host is running is not gated (the supervisor owns
/// the outcome).
#[test]
fn test_restart_allowed_while_running() {
    assert!(restart_blocked_envelope(false).is_none());
}

/// A restart in adopted-backend mode is refused with the same
/// `adopted` reason the predecessor's `restart-backend.ts` returns (the backend
/// is our parent; respawning would double-spawn next to it).
#[test]
fn test_restart_blocked_in_adopted_mode() {
    let envelope = adopted_blocked_envelope(true).expect("adopted mode must block the restart");
    assert_eq!(envelope, json!({"ok": false, "reason": "adopted"}));
}

/// A restart outside adopted mode is not gated by the adopted check.
#[test]
fn test_restart_allowed_when_not_adopted() {
    assert!(adopted_blocked_envelope(false).is_none());
}
