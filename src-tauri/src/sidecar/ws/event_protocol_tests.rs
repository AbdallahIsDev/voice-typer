//! Unit tests for `sidecar::ws::event_protocol`.
//!
//! Extracted from the inline `#[cfg(test)] mod tests { ... }` block that
//! previously lived at the bottom of `event_protocol.rs`. The split
//! satisfies C-TEST-5 (no inline test code in production source files).
//!
//! Tests are wired via `#[cfg(test)] #[path = "event_protocol_tests.rs"]
//! mod event_protocol_tests;` declared in `event_protocol.rs` (same
//! convention as `sidecar/spawn.rs` → `spawn_tests.rs`), so
//! `use super::*` below resolves to the `event_protocol` module.

use super::*;
// Explicit (not relied on via the parent glob): envelope-shape tests.
use serde_json::json;

// translate_event_name ────────────────────────────

#[test]
fn test_translate_event_name_relaunch_app_passes_through() {
    // cleanup the `relaunch_electron` →
    // `relaunch_app` rename arm was REMOVED. The Python sidecar
    // publishes `relaunch_app` directly (see `app.py::restart_app`),
    // and `main.rs::setup` listens for `relaunch_app` via
    // `app.listen(...)`. Both `relaunch_app` and the legacy
    // `relaunch_electron` (kept in the ALLOWED_EVENT_TYPES block-list
    // for one release cycle so old Python sidecars don't get
    // silently dropped) must pass through `translate_event_name`
    // UNCHANGED — re-adding the rename arm would break the
    // `test_ws_reader_does_not_rename_relaunch_app` parity test in
    // `tests/tauri/mig19/test_wire_swap_recovery.py`.
    assert_eq!(translate_event_name("relaunch_app"), "relaunch_app");
    assert_eq!(
        translate_event_name("relaunch_electron"),
        "relaunch_electron"
    );
}

#[test]
fn test_translate_event_name_bubble_lifecycle_kebab() {
    // snake_case bubble events from the Python sidecar
    // must be translated to the kebab-case `bubble:*` names the
    // renderer's preload + capability file expect.
    assert_eq!(translate_event_name("bubble_set_state"), "bubble:set-state");
    assert_eq!(translate_event_name("bubble_show"), "bubble:show");
    assert_eq!(translate_event_name("bubble_hide"), "bubble:hide");
    assert_eq!(translate_event_name("bubble_config"), "bubble:config");
}

#[test]
fn test_translate_event_name_unknown_passes_through() {
    // Forward-compat: unknown event names must pass through unchanged
    // so new sidecar events don't require a host-side release.
    assert_eq!(translate_event_name("bubble_level"), "bubble_level");
    assert_eq!(translate_event_name("notification"), "notification");
    assert_eq!(
        translate_event_name("electron_notification"),
        "electron_notification"
    );
    assert_eq!(
        translate_event_name("some_brand_new_event"),
        "some_brand_new_event"
    );
    assert_eq!(translate_event_name(""), "");
}

#[test]
fn test_translate_event_name_bubble_level_not_renamed() {
    // `bubble_level` is the high-frequency coalesced event — it must
    // NOT be translated (it's matched literally in the reader task's
    // coalesce branch above). A regression that mapped `bubble_level`
    // to `bubble:level` would break the coalesce path silently.
    assert_eq!(translate_event_name("bubble_level"), "bubble_level");
}

// legacy event aliases removed from ALLOWED_EVENT_TYPES ─

#[test]
fn test_gt_e3_6_legacy_aliases_not_in_allowlist() {
    // `relaunch_electron` and `electron_notification` were
    // removed from `ALLOWED_EVENT_TYPES`. Old Python sidecars that
    // still emit these legacy names will have their frames DROPPED
    // by the WS reader's allowlist check.
    assert!(
        !ALLOWED_EVENT_TYPES.contains(&"relaunch_electron"),
        "GT-E3-6: legacy `relaunch_electron` must NOT be in the allowlist"
    );
    assert!(
        !ALLOWED_EVENT_TYPES.contains(&"electron_notification"),
        "GT-E3-6: legacy `electron_notification` must NOT be in the allowlist"
    );
    // Canonical names must still be present.
    assert!(
        ALLOWED_EVENT_TYPES.contains(&"relaunch_app"),
        "canonical `relaunch_app` must remain in the allowlist"
    );
    assert!(
        ALLOWED_EVENT_TYPES.contains(&"notification"),
        "canonical `notification` must remain in the allowlist"
    );
}

// ── tray_fallback_notification allowlist ───────────────

/// `tray_fallback_notification` must be in the
/// server-event allowlist. The Python sidecar publishes this event
/// when the native system-tray icon is unavailable; without it in
/// `ALLOWED_EVENT_TYPES` the WS reader's `is_allowed_event_type`
/// gate drops the frame, leaving tray-less users with no
/// indication that tray features are degraded.
#[test]
fn test_si14_tray_fallback_notification_is_allowed() {
    assert!(
        is_allowed_event_type("tray_fallback_notification"),
        "tray_fallback_notification must be in ALLOWED_EVENT_TYPES ()"
    );
    // Sanity: the slice itself must list the entry (defends against
    // a future HashSet-only addition that would diverge from the
    // commented source-of-truth list).
    assert!(
        ALLOWED_EVENT_TYPES.contains(&"tray_fallback_notification"),
        "ALLOWED_EVENT_TYPES slice must contain tray_fallback_notification"
    );
}

// ── transcription_partial allowlist + passthrough ─────────────

/// `transcription_partial` must be in the server-event allowlist and
/// must pass through `translate_event_name` UNCHANGED. The Python
/// sidecar publishes it from the hidden streaming session's coalescing
/// broadcaster (live partial text during dictation) and ONCE per
/// recording with `supported:false` when the active engine lacks
/// word-level timestamps. Without the allowlist entry the WS reader's
/// `is_allowed_event_type` gate would silently drop every frame.
#[test]
fn test_transcription_partial_is_allowed_and_passthrough() {
    assert!(
        ALLOWED_EVENT_TYPES.contains(&"transcription_partial"),
        "ALLOWED_EVENT_TYPES slice must contain transcription_partial"
    );
    assert!(
        is_allowed_event_type("transcription_partial"),
        "is_allowed_event_type(`transcription_partial`) must return true — \
         the WS reader would otherwise drop the frame"
    );
    // Passthrough: no kebab-case rename arm — the renderer subscribes
    // via usePythonEvent("transcription_partial", ...).
    assert_eq!(
        translate_event_name("transcription_partial"),
        "transcription_partial"
    );
}

// ── High-rate carve-out + generic envelope builder ────────────

/// `HIGH_RATE_EVENT_TYPES` must contain EXACTLY `bubble_level` — no
/// more, no less. The carve-out skips the generic `python-event`
/// envelope for its members, so an over-broad list silently starves
/// catch-all consumers (a previous version listed `mic_level`, which
/// killed the Microphone meter — see the ws_tests regression guard).
#[test]
fn test_high_rate_event_types_is_exactly_bubble_level() {
    assert_eq!(
        HIGH_RATE_EVENT_TYPES,
        &["bubble_level"],
        "the typed-only carve-out must cover exactly bubble_level"
    );
    assert!(
        ALLOWED_EVENT_TYPES.contains(&"bubble_level"),
        "bubble_level must stay allowlisted (carve-out only skips the envelope)"
    );
}

/// Truth table for the gate the WS reader consults before deciding
/// between the typed-only fast path and the default dual emission.
#[test]
fn test_is_high_rate_event_type_truth_table() {
    assert!(is_high_rate_event_type("bubble_level"));
    // Regression guard: mic_level consumers ride the GENERIC
    // python-event envelope (usePythonEvent → api.onEvent →
    // listen("python-event")) — it must never be high-rate again.
    assert!(!is_high_rate_event_type("mic_level"));
    assert!(!is_high_rate_event_type("status_change"));
    assert!(!is_high_rate_event_type(""));
    assert!(!is_high_rate_event_type("not_an_event"));
}

/// The generic envelope builder must produce exactly
/// `{"type": <name>, "data": <payload>}` — the shape the renderer's
/// usePythonEvent dispatcher consumes via `api.onEvent`.
#[test]
fn test_python_event_envelope_shape() {
    let env = python_event_envelope("transcription_final", json!({"text": "done", "cycle": 7}));
    assert_eq!(
        env,
        json!({"type": "transcription_final", "data": {"text": "done", "cycle": 7}})
    );
    // Empty payload stays `{}` (never null).
    assert_eq!(
        python_event_envelope("ready", json!({})),
        json!({"type": "ready", "data": {}})
    );
}

// ── Pack + worker IPC event types (master plan §7.4 — 13 new) ───────
//
// The runtime-pack split introduces 13 new server-initiated event types:
// pack download lifecycle (4), pack integrity (4), worker process
// lifecycle (3), and offline transcription (2 — request + result push).
// Each is registered in `ALLOWED_EVENT_TYPES` so the WS reader's
// `is_allowed_event_type` gate does NOT silently drop the frame once
// the worker comes online. This test pins the full 13-entry set so a
// future rename or accidental removal fails `cargo test` BEFORE the
// cross-layer Python parity test in
// `tests/test_event_types_parity.py` (`TestRustAllowlistContainsAllNewEvents`
// + `TestEventAllowlistCrossLayerParity`) runs in CI.

/// The 13 new pack/worker event types must all be in
/// `ALLOWED_EVENT_TYPES`. Each event is asserted both via the slice
/// `.contains()` (defends against a HashSet-only addition that would
/// diverge from the commented source-of-truth list) AND via
/// `is_allowed_event_type()` (defends against the lookup-set being
/// constructed from a different source than the slice).
#[test]
fn test_offline_pack_worker_event_types_are_allowed() {
    let pack_worker_events: &[&str] = &[
        // Offline-pack download lifecycle (push):
        "offline_pack_download_started",
        "offline_pack_download_progress",
        "offline_pack_download_completed",
        "offline_pack_download_failed",
        // Offline-pack integrity (push):
        "offline_pack_verified",
        "offline_pack_missing",
        "offline_pack_corrupt",
        "offline_pack_ready",
        // Worker process lifecycle (push):
        "worker_started",
        "worker_crashed",
        "worker_unloaded",
        // Offline transcription (request + result push):
        "transcribe_offline",
        "transcribe_offline_result",
    ];
    assert_eq!(
        pack_worker_events.len(),
        13,
        "sanity: the §7.4 pack/worker event set is exactly 13 entries — \
         if the plan adds/removes one, update this test in lockstep"
    );
    for &evt in pack_worker_events {
        assert!(
            ALLOWED_EVENT_TYPES.contains(&evt),
            "ALLOWED_EVENT_TYPES slice must contain `{evt}` (master plan §7.4)"
        );
        assert!(
            is_allowed_event_type(evt),
            "is_allowed_event_type(`{evt}`) must return true — the WS reader \
             would otherwise drop the frame"
        );
    }
}
