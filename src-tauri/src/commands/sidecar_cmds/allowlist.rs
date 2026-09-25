//! `ALLOWED_COMMANDS` defense-in-depth allowlist + shared error-code /
//! pending-map constants.
//!
//! KEEP IN SYNC with the Python `_COMMAND_REGISTRY` (the other half of
//! the two-layer parity; CONTRIBUTING.md §6.4). Parity tests:
//! `tests/test_ipc_command_parity.py` /
//! `tests/test_security_doc_command_count.py`. Host-dispatched commands
//! (`tray_click`, `heartbeat`, `relaunch_ack`, `shutdown`) are
//! intentionally ABSENT — they use `dispatch_inner` / fire-and-forget
//! frames, never renderer `invoke('dispatch')`.

use std::collections::HashSet;
use std::sync::OnceLock;

/// Canonical error-code string for a command not on the allowlist.
pub(crate) const DISALLOWED_COMMAND_CODE: &str = "disallowed_command";
/// Canonical error-code string for the main/bubble window guard
/// (SEC-026). Referenced by `tests/test_error_codes_registry.py`.
pub(crate) const DISALLOWED_WINDOW_CODE: &str = "disallowed_window";

// Pending-map size cap: bounds memory under pathological backpressure
// (unresponsive sidecar + retry storm). Normal traffic is 1-3 in-flight
// dispatches; 1024 is far above that.
pub(crate) const PENDING_MAX: usize = 1024;
/// `dispatch_frame` error when the pending map is full (renderer should
/// back off; distinct from not-connected / shutting-down).
pub(crate) const PENDING_FULL_CODE: &str = "pending_full";

// ALLOWED_COMMANDS (SEC-019 / defense-in-depth): the only webview→sidecar
// path. A compromised renderer must not reach arbitrary server commands.
// Built once into a OnceLock HashSet for O(1) `contains`.
static ALLOWED_COMMANDS: OnceLock<HashSet<&'static str>> = OnceLock::new();

/// Process-global ALLOWED_COMMANDS set (init on first call).
pub(crate) fn allowed_commands() -> &'static HashSet<&'static str> {
    ALLOWED_COMMANDS.get_or_init(|| {
        // Mirrors Python `_COMMAND_REGISTRY` minus the host-dispatched
        // delta. Parity tests pin count + exact entries. Duplicates are
        // caught by `test_allowed_commands_set_contains_no_duplicates`.
        let cmds: &[&str] = &[
            "get_status",
            "toggle_dictation",
            "undo_last",
            "get_config",
            "get_defaults",
            "set_config",
            "get_history",
            "search_history",
            "get_today_stats",
            "delete_history",
            "restore_history",
            "clear_history",
            "toggle_favorite",
            "get_favorites",
            "get_microphones",
            "restart_app",
            "quit_app",
            "get_templates",
            "save_templates",
            "get_volume_backend_status",
            "get_model_status",
            // About-page Cache Status card (user-facing feature).
            "get_prewarm_status",
            "open_prewarm_log",
            // Models storage card + Diagnostics button (config dir in OS file manager).
            "open_data_folder",
            "run_prewarm",
            "get_vocabulary",
            "save_vocabulary",
            "test_vocabulary_correction",
            "get_correction_usage",
            "onboarding_is_first_run",
            "onboarding_start",
            "onboarding_next_step",
            "onboarding_prev_step",
            "onboarding_set_microphone",
            "onboarding_set_backend",
            "onboarding_set_hotkey",
            "onboarding_set_model",
            "onboarding_skip",
            "onboarding_apply",
            // Onboarding Check Permissions / Model Catalog.
            "onboarding_check_permissions",
            "onboarding_get_microphones",
            "onboarding_get_model_options",
            "onboarding_get_hotkey_presets",
            "download_model",
            "cancel_model_download",
            "pause_model_download",
            "resume_model_download",
            "delete_model",
            "get_model_catalog",
            // Pending download queue snapshot (Models page, read-only).
            "get_download_queue",
            "microphone_test_start",
            "microphone_test_stop",
            "microphone_test_read_audio",
            "microphone_test_cancel",
            "microphone_test_get_level",
            "level_monitor_start",
            "level_monitor_stop",
            "set_esc_cancel_paused",
            "set_tray_locale",
            // macOS troubleshooting (Settings → Troubleshooting).
            "reset_macos_accessibility",
            "check_accessibility",
            // Linux troubleshooting (stale polkit grant reset).
            "reset_linux_permissions",
            "import_model",
            // heartbeat + relaunch_ack intentionally ABSENT (Rust-internal
            // dispatch_inner / fire-and-forget; never renderer-invoked).
            "repaste_last",
            "force_cancel_transcription",
            // Lightweight history counters (Dashboard / history detail).
            "get_history_count",
            "get_transcription_text",
            "onboarding_reset",
            "test_cloud_connection",
            // URL-allowlist extension for self-hosted LLM/ASR endpoints.
            "add_trusted_endpoint",
            // Slim-core → worker offline transcription request.
            "transcribe_offline",
            // Auto-update: runtime-pack update check (GitHub API manifest).
            "check_offline_pack_update",
            // ADR-0023 universal media-to-text (URL ladder + local files
            // implemented; PO token / playlists / live capture Phase 2).
            "media_transcribe_start",
            "media_transcribe_cancel",
            "media_transcribe_status",
        ];
        HashSet::from_iter(cmds.iter().copied())
    })
}

/// True iff `cmd` is in `ALLOWED_COMMANDS` (pure, unit-testable).
pub(crate) fn is_command_allowed(cmd: &str) -> bool {
    allowed_commands().contains(cmd)
}
