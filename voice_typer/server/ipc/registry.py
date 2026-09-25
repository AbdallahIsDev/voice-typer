"""IPC command registry — single source of truth for dispatch tables.

- ``_COMMAND_REGISTRY``: ``{command: handler_method}`` for ``IPCServer._dispatch``.
- ``_READONLY_COMMANDS``: pure-read handlers bypass ``_dispatch_lock`` (WS/stdin
  still consult this; not TCP-only).
- ``_INSTANT_CONTROL_COMMANDS``: pause/resume/cancel bypass the lock (single
  atomic flag writes, no I/O).
- ``_SELF_SERIALIZED_COMMANDS``: long-running handlers that bypass the lock
  because they carry their own serialization (service single-flight +
  FIFO queue + per-download cancel events + transfer gate).
- ``_PYTHON_ONLY_COMMANDS``: host-only commands absent from the Rust renderer
  allowlist (``shutdown``, ``tray_click``). ``heartbeat`` / ``relaunch_ack`` are
  also host-dispatched; see ``tests/test_ipc_command_parity.py``.

Parity contract: CONTRIBUTING.md §6.4 — Python registry and Rust
``allowed_commands()`` stay in lockstep. History + pinned counts:
docs/code-notes/ipc.md#command-registry-contracts.
"""

from __future__ import annotations

# Registry history: removed/restored command notes live in

# Pure-read commands: dispatcher bypasses ``_dispatch_lock`` so a long-running
# mutating handler (model download, vocabulary save) cannot block status / list
# polls from another connection. Membership requires that the handler and every
# call it makes is free of shared-state mutation and persistence; the membership
# audit (every ``get_*`` command must be classified here or as a documented
# mutator) lives in ``tests/test_readonly_commands_audit.py``.
_READONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "get_status",
        "get_config",
        "get_model_catalog",
        "heartbeat",
        # Audited pure reads: each delegates to a service getter only.
        "get_defaults",
        "get_history",
        "get_history_count",
        "get_today_stats",
        "get_favorites",
        "get_transcription_text",
        "get_microphones",
        "get_volume_backend_status",
        "get_model_status",
        "get_prewarm_status",
        "get_vocabulary",
        "get_correction_usage",
        "get_templates",
        "get_download_queue",
        "microphone_test_get_level",
        "onboarding_is_first_run",
        "onboarding_get_microphones",
        "onboarding_get_model_options",
        "onboarding_get_hotkey_presets",
        "onboarding_check_permissions",
    }
)

# Instant download-control: also bypass dispatch lock. Mutation is a single
# atomic flag write under the download locks: no network/disk I/O,
# idempotent, never starts a long operation.
_INSTANT_CONTROL_COMMANDS: frozenset[str] = frozenset(
    {
        "pause_model_download",
        "resume_model_download",
        "cancel_model_download",
    }
)

# Self-serialized long-running commands: bypass ``_dispatch_lock`` because
# holding it for a multi-GB transfer wedges every later command behind the
# give-up budget (a duplicate/retry ``download_model`` then fails with a
# spurious ``server.busy`` while the real transfer is still running).
# Safety case (NOT instant: this set owns real I/O):
# - ``ModelMixin.download_model`` single-flights gateable transfers: a
#   second concurrent request is enqueued (FIFO) or answered
#   ``download_already_active``, never a second live transfer.
# - Pause/cancel/resume already bypass and act on the same per-download
#   events + transfer gate; they never needed this lock.
# - Queue-drain threads already invoke ``service.download_model`` with no
#   dispatch lock held, so the lock never actually serialized this path.
# Membership requires the handler's own concurrency guards; do NOT add
# commands that rely on the dispatch lock for correctness.
_SELF_SERIALIZED_COMMANDS: frozenset[str] = frozenset(
    {
        "download_model",
    }
)

# Host-only commands: registered here, never invoked by the renderer
_PYTHON_ONLY_COMMANDS: frozenset[str] = frozenset({"shutdown", "tray_click"})

# Maps IPC command name → handler method on IPCServer. Count pinned at 79
_COMMAND_REGISTRY: dict[str, str] = {
    "get_status": "_handle_get_status",
    "toggle_dictation": "_handle_toggle_dictation",
    "undo_last": "_handle_undo_last",
    "repaste_last": "_handle_repaste_last",
    "get_config": "_handle_get_config",
    "get_defaults": "_handle_get_defaults",
    "set_config": "_handle_set_config",
    "get_history": "_handle_get_history",
    "get_today_stats": "_handle_get_today_stats",
    "delete_history": "_handle_delete_history",
    "restore_history": "_handle_restore_history",
    "clear_history": "_handle_clear_history",
    "toggle_favorite": "_handle_toggle_favorite",
    "get_favorites": "_handle_get_favorites",
    "search_history": "_handle_search_history",
    "get_history_count": "_handle_get_history_count",
    "get_transcription_text": "_handle_get_transcription_text",
    "get_microphones": "_handle_get_microphones",
    "get_volume_backend_status": "_handle_get_volume_backend_status",
    "get_model_status": "_handle_get_model_status",
    # About-page Cache Status card (worker warm phase; not subprocess spawn).
    "get_prewarm_status": "_handle_get_prewarm_status",
    "open_prewarm_log": "_handle_open_prewarm_log",
    "open_data_folder": "_handle_open_data_folder",
    "run_prewarm": "_handle_run_prewarm",
    "get_vocabulary": "_handle_get_vocabulary",
    "save_vocabulary": "_handle_save_vocabulary",
    "get_correction_usage": "_handle_get_correction_usage",
    "test_vocabulary_correction": "_handle_test_vocabulary_correction",
    "get_templates": "_handle_get_templates",
    "save_templates": "_handle_save_templates",
    "restart_app": "_handle_restart_app",
    "quit_app": "_handle_quit_app",
    # Host cooperative shutdown (service.quit, not app.quit).
    "shutdown": "_handle_shutdown",
    "onboarding_is_first_run": "_handle_onboarding_is_first_run",
    "onboarding_start": "_handle_onboarding_start",
    "onboarding_next_step": "_handle_onboarding_next_step",
    "onboarding_prev_step": "_handle_onboarding_prev_step",
    "onboarding_set_microphone": "_handle_onboarding_set_microphone",
    "onboarding_set_hotkey": "_handle_onboarding_set_hotkey",
    "onboarding_set_model": "_handle_onboarding_set_model",
    "onboarding_set_backend": "_handle_onboarding_set_backend",
    "onboarding_skip": "_handle_onboarding_skip",
    "onboarding_apply": "_handle_onboarding_apply",
    "onboarding_get_microphones": "_handle_onboarding_get_microphones",
    "onboarding_get_model_options": "_handle_onboarding_get_model_options",
    "onboarding_get_hotkey_presets": "_handle_onboarding_get_hotkey_presets",
    "onboarding_check_permissions": "_handle_onboarding_check_permissions",
    "onboarding_reset": "_handle_onboarding_reset",
    "microphone_test_start": "_handle_microphone_test_start",
    "microphone_test_stop": "_handle_microphone_test_stop",
    "microphone_test_read_audio": "_handle_microphone_test_read_audio",
    "microphone_test_cancel": "_handle_microphone_test_cancel",
    "microphone_test_get_level": "_handle_microphone_test_get_level",
    "level_monitor_start": "_handle_level_monitor_start",
    "level_monitor_stop": "_handle_level_monitor_stop",
    "import_model": "_handle_import_model",
    "download_model": "_handle_download_model",
    "cancel_model_download": "_handle_cancel_model_download",
    "pause_model_download": "_handle_pause_model_download",
    "resume_model_download": "_handle_resume_model_download",
    "get_model_catalog": "_handle_get_model_catalog",
    "get_download_queue": "_handle_get_download_queue",
    "delete_model": "_handle_delete_model",
    "set_tray_locale": "_handle_set_tray_locale",
    # macOS Accessibility: reset stale TCC entry + reopen System Settings.
    "reset_macos_accessibility": "_handle_reset_macos_accessibility",
    # macOS Accessibility grant probe (Settings → Troubleshooting); Rust allowlist lockstep.
    "check_accessibility": "_handle_check_accessibility",
    "reset_linux_permissions": "_handle_reset_linux_permissions",
    # Cloud Test Connection probe — network stays out of the renderer path (C-DATA-1).
    "test_cloud_connection": "_handle_test_cloud_connection",
    "add_trusted_endpoint": "_handle_add_trusted_endpoint",
    "set_esc_cancel_paused": "_handle_set_esc_cancel_paused",
    "force_cancel_transcription": "_handle_force_cancel_transcription",
    # Host-alive heartbeat (ADR-0018 watchdog); not renderer-facing.
    "heartbeat": "_handle_heartbeat",
    # PERF-005: host ack so restart_app can wait on an event, not a sleep.
    "relaunch_ack": "_handle_relaunch_ack",
    # Host tray click dispatch; unknown ids → unknown_tray_item.
    "tray_click": "_handle_tray_click",
    # Master plan §7.4 offline worker request; push pair is event_bus (not a command).
    "transcribe_offline": "_handle_transcribe_offline",
    "check_offline_pack_update": "_handle_check_offline_pack_update",
    # ADR-0023 universal media-to-text (URL ladder + local files implemented;
    # PO token / playlists / live capture remain Phase 2).
    "media_transcribe_start": "_handle_media_transcribe_start",
    "media_transcribe_cancel": "_handle_media_transcribe_cancel",
    "media_transcribe_status": "_handle_media_transcribe_status",
}


__all__ = [
    "_COMMAND_REGISTRY",
    "_PYTHON_ONLY_COMMANDS",
    "_READONLY_COMMANDS",
]
