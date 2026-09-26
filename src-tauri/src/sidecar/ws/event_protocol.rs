//! Event protocol: ALLOWED_EVENT_TYPES + name translation + envelope.
//! Source-inspection tests pin the slice literal — do not reformat it.

use serde_json::{json, Value};
use std::collections::HashSet;
use std::sync::OnceLock;

// Server-initiated event-type allowlist. Dispatch responses take the
// pending-id branch and never reach here.
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
    "gpu_cpu_fallback",
    // Paste error:
    "paste_failed",
    // Additional server-published events (sync with Python event_bus + TS).
    "state_changed",
    "error", // no-id server-event variant (id responses use the pending map)
    "mic_level",
    "llm_polish_failed",
    "text_enhancement_failed",
    "device_lost",
    "asr_backend_disabled",
    "asr_last_resort_unloaded",
    "audio_clip",
    "dictation_lost",
    "tray_fallback_notification",
    // Backend model-load lifecycle (background load ended).
    "asr_backend_ready",
    "asr_backend_load_failed",
    // Mid-recording device/permission events (distinct from level-monitor
    // `device_lost`).
    "microphone_permission_revoked",
    "microphone_disconnected",
    // Engine / pipeline degradation.
    "cloud_fallback_used",
    "dictation_suppressed",
    // ADR-0023 media job events (progress + completion/failure pushes).
    "media_transcribe_progress",
    "media_transcribe_complete",
    "media_transcribe_error",
    // History-store integrity.
    "history_corrupted",
    "history_fts5_rebuild_failed",
    // Clipboard paste safety (paste keystroke dropped / deferred).
    "paste_deferred",
    // Pack + worker IPC events (runtime-pack split). Parity pinned by
    // `tests/test_event_types_parity.py`.
    "offline_pack_download_started",
    "offline_pack_download_progress",
    "offline_pack_download_completed",
    "offline_pack_download_failed",
    "offline_pack_verified",
    "offline_pack_missing",
    "offline_pack_corrupt",
    "offline_pack_ready",
    "worker_started",
    "worker_crashed",
    "worker_unloaded",
    // Also a command-allowlist name (`transcribe_offline`); the result
    // is a push event.
    "transcribe_offline",
    "transcribe_offline_result",
];

// O(1) lookup set derived from `ALLOWED_EVENT_TYPES` (source of truth).
// `bubble_level` arrives at ~60 Hz — a linear scan would be hot-path cost.
static ALLOWED_EVENT_TYPES_SET: OnceLock<HashSet<&'static str>> = OnceLock::new();

/// True iff `event_type` is in the allowlist. `pub(crate)` for the ws.rs
/// test re-export.
pub(crate) fn is_allowed_event_type(event_type: &str) -> bool {
    ALLOWED_EVENT_TYPES_SET
        .get_or_init(|| ALLOWED_EVENT_TYPES.iter().copied().collect())
        .contains(event_type)
}

const HIGH_RATE_EVENT_TYPES: &[&str] = &["bubble_level"];

/// True iff the generic catch-all re-emit is skipped for this event type.
pub(crate) fn is_high_rate_event_type(event_type: &str) -> bool {
    HIGH_RATE_EVENT_TYPES.contains(&event_type)
}

/// Generic `python-event` envelope: `{"type": <name>, "data": <payload>}`.
/// Shared by the `ready` re-emit and the reader's low-rate branch.
pub(crate) fn python_event_envelope(emit_name: &str, payload: Value) -> Value {
    json!({"type": emit_name, "data": payload})
}

pub(crate) fn translate_event_name(event_type: &str) -> &str {
    match event_type {
        // Bubble lifecycle: Python snake_case → renderer kebab-case.
        "bubble_set_state" => "bubble:set-state",
        "bubble_show" => "bubble:show",
        "bubble_hide" => "bubble:hide",
        "bubble_config" => "bubble:config",
        other => other,
    }
}

// C-TEST-5: sibling test file.
#[cfg(test)]
#[path = "event_protocol_tests.rs"]
mod event_protocol_tests;
