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
