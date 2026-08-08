//! Server-initiated event protocol — allowlist + event-name
//! translation (ADR-0020 §9).
//!
//! Extracted from the original 2534-line `ws.rs` monolith
//! (review.md FZ-24 / ZR-86). Holds:
//! - `ALLOWED_EVENT_TYPES` — the source-of-truth slice of every
//!   event name the Python sidecar is known to publish today.
//! - `ALLOWED_EVENT_TYPES_SET` — O(1) lookup set derived from the
//!   slice (lazily initialized, lives for the process lifetime).
//! - `is_allowed_event_type` — gate used by the WS reader's inbound
//!   frame path (`bubble_level` arrives at ~60 Hz, so a linear
//!   `.contains()` scan over the ~40-entry slice would be ~2,400
//!   string comparisons/sec on the hot path).
//! - `translate_event_name` — snake→kebab bubble-lifecycle renames
//!   (kept `pub(crate)` and re-exported from `ws.rs` so external
//!   callers — if any — keep working through `crate::sidecar::ws::
//!   translate_event_name`).
//!
//! Visibility contract:
//! - `ALLOWED_EVENT_TYPES` + `is_allowed_event_type` + `translate
//!   _event_name` are `pub(super)` — visible to the parent `ws`
//!   module (call sites in `spawn_reader_task`) and to this
//!   module's `#[cfg(test)] mod tests`. `translate_event_name` is
//!   `pub(crate)` (re-exported from ws.rs for external callers).
//! - `ALLOWED_EVENT_TYPES_SET` is private (only used by
//!   `is_allowed_event_type`).

use std::collections::HashSet;
use std::sync::OnceLock;

// server-initiated event-type allowlist ──────────────────────
//
// ADR-0020 §9: only known server-initiated event types may be emitted
// to the renderer as Tauri events. An unknown `type` field on an
// inbound WS frame is dropped with a `[WS-READER]` warning — this is
// defense-in-depth against a compromised sidecar process (or a
// protocol regression) trying to inject arbitrary event names that
// the renderer's `usePythonEvent(type, ...)` listeners might be
// tricked into handling.
//
// The first block below is the spec list (verbatim). The
// second block is the set of additional events the Python sidecar
// ACTUALLY publishes today (`rg '"type":\s*"<name>"' voice_typer/server`)
// — without these, the host would silently drop `ready`, `bubble_show`,
// `history_changed`, etc. and break startup / bubble UI / history UI.
// Keep both blocks in sync with the server's `event_bus.publish`
// call sites. Drop the legacy `electron_notification` alias after
// one release cycle with no rolling-upgrade traffic.
pub(super) const ALLOWED_EVENT_TYPES: &[&str] = &[
    // spec list (verbatim) ──
    "status_change",
    "bubble_level",
    "notification",
    "relaunch_app",
    "tray_menu",
    "tray_state",
    "supervisor_relaunching",
    "supervisor_reconnected",
    "crash_recovery",
    "transcription_partial",
    "transcription_final",
    "transcription_interim",
    "recording_state",
    "vocabulary_suggestion",
    "model_download_progress",
    "audio_status",
    "server_started",
    // Additional known server-published events ──
    // Lifecycle / window management:
    "ready",
    "quit_app",
    "show_window",
    "navigate",
    // Bubble UI:
    "bubble_show",
    "bubble_hide",
    "bubble_config",
    "bubble_set_state",
    // Recording (server emits *_started/*_stopped; `recording_state` in
    // the spec list above is the umbrella name some future server may
    // adopt — keep both):
    "recording_started",
    "recording_stopped",
    // Settings / config / history:
    "config_changed",
    "history_changed",
    "consent_required",
    // Hotkey capture:
    "hotkey_capture_cancel",
    // Microphone settings:
    "microphone_test_complete",
    "microphones_changed",
    // Model download (server emits `download_progress`; the spec list
    // above has the umbrella `model_download_progress`):
    "download_progress",
    // Engine fallback:
    "parakeet_cpu_fallback",
    // Paste error:
    "paste_failed",
    // ── Additional server-published events not in the original spec
    // list above. Each is published via `event_bus.publish(...)` in the
    // Python sidecar; keep this slice in sync with the server's publish
    // call sites (see `voice_typer/server/*.py`).
    // - `state_changed`: emitted on every authenticated WS connection
    // (sidecar_ws.py) — the renderer hydrates connection state from
    // it on startup.
    // - `error`: server-initiated error notification (e.g. recording-
    // start failure in recording_controller.py). NOTE: dispatch
    // *responses* with `type:"error"` AND an `id` field take a
    // different branch earlier in the reader (fulfilled via the
    // pending-id map) and never reach this allowlist; this entry
    // covers only the no-id server-event variant.
    // - `mic_level`: continuously published by level_monitor.py while
    // level monitoring is active; drives the Microphone page's live
    // level meter.
    // - `llm_polish_failed`: emitted by dictation_pipeline.py when the
    // LLM polish step fails (typed in TS push_events.ts; latent
    // subscriber today).
    // - `device_lost`: emitted by level_monitor.py when the audio
    // input device disappears (typed in TS; latent subscriber).
    // - `asr_backend_disabled`: emitted by asr_registry.py when an ASR
    // backend is disabled at runtime (typed in TS; latent).
    // - `asr_last_resort_unloaded`: emitted by asr_registry.py when the
    // last-resort ASR backend unloads (typed in TS; latent).
    // - `audio_clip`: registered in `event_bus._KNOWN_EVENTS` (typed in
    // TS push_events.ts; latent subscriber today).
    // - `dictation_lost`: emitted by crash_recovery.py on startup when
    // it detects the previous process crashed mid-dictation (the
    // `.dictation-in-flight` sentinel was left behind). The renderer
    // shows a notification so the user knows their dictation was lost.
    // - `tray_fallback_notification`: emitted by tray_manager.py when
    // the native system-tray icon is unavailable (headless build,
    // Linux without a systray compositor, sandboxed mac App Store
    // build, etc.) and the renderer should surface a fallback
    // in-app notification banner instead. this was
    // published by the Python sidecar but missing from the
    // allowlist, so the WS reader was silently dropping the frame
    // (logged at `[WS-READER] dropping unknown event type:`) and
    // the renderer's fallback listener never fired — users on
    // tray-less systems had NO indication that tray features were
    // degraded. Adding it here lets the frame through to the
    // renderer's `usePythonEvent("tray_fallback_notification", ...)`
    // handler.
    "state_changed",
    "error",
    "mic_level",
    "llm_polish_failed",
    "device_lost",
    "asr_backend_disabled",
    "asr_last_resort_unloaded",
    "audio_clip",
    "dictation_lost",
    "tray_fallback_notification",
    // legacy aliases `relaunch_electron` and
    // `electron_notification` REMOVED. The Python sidecar has published
    // the canonical `relaunch_app` and `notification` event names for
    // more than one release cycle; the rolling-upgrade grace period is
    // over. Old sidecars that still emit the legacy names will now have
    // those frames DROPPED by the `ALLOWED_EVENT_TYPES` allowlist
    // (logged at `[WS-READER] dropping unknown event type:`).
];

// O(1) lookup set for the inbound-frame hot path. `bubble_level`
// arrives at ~60 Hz, so a linear `.contains()` scan over the ~40-entry
// slice above would mean ~2,400 string comparisons/sec on the WS reader
// task. This `OnceLock<HashSet>` is initialized lazily from
// `ALLOWED_EVENT_TYPES` on the first inbound frame and stays live for
// the process lifetime; `ALLOWED_EVENT_TYPES` remains the single
// source-of-truth list above (the set is derived from it, not the other
// way around) so the visible, commented list stays the canonical one.
static ALLOWED_EVENT_TYPES_SET: OnceLock<HashSet<&'static str>> = OnceLock::new();

/// Returns `true` iff `event_type` is in the server-initiated event
/// allowlist. O(1) after the first call (the `HashSet` is built once
/// from `ALLOWED_EVENT_TYPES` and cached in a `OnceLock`).
pub(super) fn is_allowed_event_type(event_type: &str) -> bool {
    ALLOWED_EVENT_TYPES_SET
        .get_or_init(|| ALLOWED_EVENT_TYPES.iter().copied().collect())
        .contains(event_type)
}

// translate Python-sidecar event names to the renderer's
/// canonical event names. The Python sidecar publishes some events
/// under snake_case names inherited from the Electron era (e.g.
/// `bubble_set_state`) that the renderer expects as kebab-case
/// `bubble:*` (matching the `bubble:show`, `bubble:hide` events
/// already documented in ADR-0020 §6.3). Unknown event names pass
/// through unchanged so this function is forward-compatible with new
/// sidecar events without requiring a host-side code change.
///
/// Extracted from the WS reader's inline `match event_type { ... }`
/// so the translation table is unit-testable without a Tauri runtime
/// and so future renames are localized to one place.
///
/// `pub(crate)` + re-exported from `ws.rs` so external callers using
/// `crate::sidecar::ws::translate_event_name` keep working after the
/// FZ-24 module split.
pub(crate) fn translate_event_name(event_type: &str) -> &str {
    match event_type {
        // cleanup the `relaunch_electron` →
        // `relaunch_app` rename arm was REMOVED here — the Python
        // sidecar now publishes the event under the canonical
        // `relaunch_app` name directly (see `app.py::restart_app`),
        // so it passes through unchanged. `main.rs::setup` registers
        // `app.listen("relaunch_app", ...)` which calls
        // `app.restart()`. The renderer-side parity tests in
        // `tests/tauri/mig19/test_wire_swap_recovery.py`
        // (`test_ws_reader_does_not_rename_relaunch_app`) lock this
        // in: re-adding the arm will fail that test.
        //
        // bubble lifecycle events. The Python sidecar
        // still publishes these under the snake_case names that the
        // Electron bridge used; the Tauri renderer's `bubble.ts`
        // preload + `bubble-runtime.json` capability file use the
        // kebab-case `bubble:*` names. Without this translation the
        // events would be emitted under names the renderer never
        // listens for, silently dropping the bubble state changes.
        "bubble_set_state" => "bubble:set-state",
        "bubble_show" => "bubble:show",
        "bubble_hide" => "bubble:hide",
        "bubble_config" => "bubble:config",
        // Forward-compatible: unknown events pass through unchanged
        // so new sidecar events don't require a host-side release.
        other => other,
    }
}

#[cfg(test)]
mod tests {
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
}
