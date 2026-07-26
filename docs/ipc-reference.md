# IPC Reference

Auto-generated reference for the Voice Typer IPC protocol.

**Source of truth:**

- [_COMMAND_REGISTRY](../voice_typer/server/ipc_server.py) — server-side handler map (canonical command list)
- [ALLOWED_COMMANDS](../voice_typer/client/src/main/allowed-commands.ts) — Electron main-process allowlist (renderer-reachable subset)
- [types/ipc.ts](../voice_typer/client/src/renderer/src/types/ipc.ts) — TypeScript types for requests + push events

> This file is a human-readable summary. The three sources above are the
> authoritative references; if this doc disagrees with any of them, the
> source files win. Regenerate by re-running the script that produced this
> file (see `archive/` for the generator script).

## Three-allowlist contract (NH-33 / NH-34)

Every IPC command must exist in **all three** places to be reachable from
the renderer:

1. **Server registry** (`_COMMAND_REGISTRY`): maps `command_name` to
   `_handler_method` on the `IpcServer` class. Without an entry here,
   the server returns `{type:"error", data:{code:"unknown_command"}}`.
2. **Electron allowlist** (`ALLOWED_COMMANDS`): the canonical set of
   commands the renderer may invoke via `window.python.call(...)`. The
   Electron main process rejects anything not in this set BEFORE the
   command reaches the Python backend (SEC-019).
3. **Renderer types** (`PythonRequest` / `PythonPushEvent`): TypeScript
   unions that give the renderer compile-time type safety for the
   request/response shapes.

The parity test
`tests/test_electron_ipc_and_build.py::test_allowlist_matches_server_commands`
asserts that `ALLOWED_COMMANDS` (sliced as a literal substring from
`allowed-commands.ts`) matches the renderer-reachable subset of
`_COMMAND_REGISTRY`. Commands in `_COMMAND_REGISTRY` but NOT in
`ALLOWED_COMMANDS` are server-only (invoked internally by the backend or
via the Rust tray host's `dispatch_inner`, which bypasses the allowlist).

## Commands (63 total)

Grouped by namespace. "✓" in the Allowlist column means the command is
in `ALLOWED_COMMANDS` (renderer-reachable); "—" means server-only.

### System / config / heartbeat

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `get_config` | `_handle_get_config` | ✓ |  |
| `get_defaults` | `_handle_get_defaults` | ✓ |  |
| `get_status` | `_handle_get_status` | ✓ |  |
| `heartbeat` | `_handle_heartbeat` | ✓ | doesn't strand the backend with the mic open + mutex held. |
| `relaunch_ack` | `_handle_relaunch_ack` | ✓ | event-driven wait (bounded by a 2s timeout). |
| `set_config` | `_handle_set_config` | ✓ |  |

### App control (toggle, undo, repaste, tray, force-cancel, restart/quit)

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `force_cancel_transcription` | `_handle_force_cancel_transcription` | ✓ | 3×90s watchdog timeout. |
| `quit_app` | `_handle_quit_app` | ✓ |  |
| `repaste_last` | `_handle_repaste_last` | ✓ | UX-23: re-paste the last transcription (repaste_handlers mixin). |
| `restart_app` | `_handle_restart_app` | ✓ |  |
| `set_tray_locale` | `_handle_set_tray_locale` | ✓ | TRAY-008: allow set_tray_locale so tray labels update when the user changes t... |
| `shutdown` | `_handle_shutdown` | — | layer runs identically on TCP / stdin / WS. |
| `toggle_dictation` | `_handle_toggle_dictation` | ✓ |  |
| `tray_click` | `_handle_tray_click` | — | "unknown command" differently. |
| `undo_last` | `_handle_undo_last` | ✓ |  |

### Onboarding wizard

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `onboarding_apply` | `_handle_onboarding_apply` | ✓ |  |
| `onboarding_check_permissions` | `_handle_onboarding_check_permissions` | ✓ | the Permissions step. |
| `onboarding_get_hotkey_presets` | `_handle_onboarding_get_hotkey_presets` | ✓ |  |
| `onboarding_get_microphones` | `_handle_onboarding_get_microphones` | ✓ |  |
| `onboarding_get_model_catalog` | `_handle_onboarding_get_model_catalog` | — |  |
| `onboarding_get_model_options` | `_handle_onboarding_get_model_options` | ✓ |  |
| `onboarding_get_step` | `_handle_onboarding_get_step` | — |  |
| `onboarding_is_first_run` | `_handle_onboarding_is_first_run` | ✓ |  |
| `onboarding_next_step` | `_handle_onboarding_next_step` | ✓ |  |
| `onboarding_prev_step` | `_handle_onboarding_prev_step` | ✓ |  |
| `onboarding_request_keyboard_permission` | `_handle_onboarding_request_keyboard_permission` | — | invoke calls returned ``unknown_command``. |
| `onboarding_reset` | `_handle_onboarding_reset` | ✓ | G4-M-10 + PVT-G5-025 (session-3 + 5): onboarding reset — invoked by the Onboa... |
| `onboarding_set_hotkey` | `_handle_onboarding_set_hotkey` | ✓ |  |
| `onboarding_set_microphone` | `_handle_onboarding_set_microphone` | ✓ |  |
| `onboarding_set_model` | `_handle_onboarding_set_model` | ✓ |  |
| `onboarding_skip` | `_handle_onboarding_skip` | ✓ |  |
| `onboarding_start` | `_handle_onboarding_start` | ✓ |  |

### Models (download, import, delete, status, prewarm)

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `cancel_model_download` | `_handle_cancel_model_download` | ✓ | NEW-PRIV-011: allow cancel_model_download so the renderer can cancel an in-pr... |
| `delete_model` | `_handle_delete_model` | ✓ | NEW-UX-005: allow delete_model so the renderer can actually delete model file... |
| `download_model` | `_handle_download_model` | ✓ |  |
| `get_model_catalog` | `_handle_get_model_catalog` | ✓ | Models page: VRAM, languages, speed/accuracy ratings). |
| `get_model_status` | `_handle_get_model_status` | ✓ |  |
| `get_prewarm_status` | `_handle_get_prewarm_status` | ✓ | page's "Cache Status" card. |
| `get_volume_backend_status` | `_handle_get_volume_backend_status` | ✓ |  |
| `import_model` | `_handle_import_model` | ✓ | MODEL-IMPORT: allow import_model so the Models page can scan and import pre-d... |
| `open_prewarm_log` | `_handle_open_prewarm_log` | ✓ | from the About page's "View prewarm log" button. |
| `pause_model_download` | `_handle_pause_model_download` | ✓ | NEW-PAUSE-001: pause/resume in-progress model downloads. |
| `resume_model_download` | `_handle_resume_model_download` | ✓ |  |
| `run_prewarm` | `_handle_run_prewarm` | ✓ | subprocess; the frontend polls get_prewarm_status to track it. |

### History (CRUD, favorites, search, today stats)

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `clear_history` | `_handle_clear_history` | ✓ |  |
| `delete_history` | `_handle_delete_history` | ✓ |  |
| `get_favorites` | `_handle_get_favorites` | ✓ |  |
| `get_history` | `_handle_get_history` | ✓ |  |
| `restore_history` | `_handle_restore_history` | ✓ |  |
| `search_history` | `_handle_search_history` | ✓ |  |
| `toggle_favorite` | `_handle_toggle_favorite` | ✓ |  |

### Vocabulary (save, get, suggestions)

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `apply_vocabulary_suggestion` | `_handle_apply_vocabulary_suggestion` | — |  |
| `dismiss_vocabulary_suggestion` | `_handle_dismiss_vocabulary_suggestion` | — |  |
| `get_vocabulary` | `_handle_get_vocabulary` | ✓ |  |
| `get_vocabulary_suggestions` | `_handle_get_vocabulary_suggestions` | — | suggestions.  See ``vocabulary_automation_handlers.py``. |
| `save_vocabulary` | `_handle_save_vocabulary` | ✓ |  |

### Templates

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `get_templates` | `_handle_get_templates` | ✓ |  |
| `save_templates` | `_handle_save_templates` | ✓ |  |

### Microphones (list, refresh)

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `get_microphones` | `_handle_get_microphones` | ✓ |  |
| `refresh_microphones` | `_handle_refresh_microphones` | — |  |

### Microphone test (start, stop, cancel, level)

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `microphone_test_cancel` | `_handle_microphone_test_cancel` | ✓ |  |
| `microphone_test_get_level` | `_handle_microphone_test_get_level` | ✓ |  |
| `microphone_test_start` | `_handle_microphone_test_start` | ✓ | Microphone test commands |
| `microphone_test_status` | `_handle_microphone_test_status` | — |  |
| `microphone_test_stop` | `_handle_microphone_test_stop` | ✓ |  |

### Continuous level monitor

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `level_monitor_start` | `_handle_level_monitor_start` | ✓ | Continuous level monitor |
| `level_monitor_status` | `_handle_level_monitor_status` | — |  |
| `level_monitor_stop` | `_handle_level_monitor_stop` | ✓ |  |

### Audio status / RMS

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `get_audio_status` | `_handle_get_audio_status` | — |  |
| `get_rms_level` | `_handle_get_rms_level` | — |  |

### Hotkey / ESC cancel

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `set_esc_cancel_paused` | `_handle_set_esc_cancel_paused` | ✓ | the UI is capturing a custom hotkey. |

### Accessibility status

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `check_accessibility` | `_handle_check_accessibility` | — |  |

### Diagnostics / GDPR / personal data

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `delete_all_personal_data` | `_handle_delete_all_personal_data` | — | VoiceTyperService (delete_all_personal_data / export_gdpr_bundle). |
| `export_diagnostics` | `_handle_export_diagnostics` | — |  |
| `export_gdpr_bundle` | `_handle_export_gdpr_bundle` | — |  |

### LLM connection test

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `test_llm_connection` | `_handle_test_llm_connection` | — |  |

### Notifications (Electron)

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `show_electron_notification` | `_handle_show_electron_notification` | — |  |

### Other / unclassified

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `get_today_stats` | `_handle_get_today_stats` | ✓ |  |

## Push events (28 typed)

Push events flow server to renderer via `window.python.onEvent(callback)`.
The `PythonPushEvent` union in `types/ipc.ts` is the canonical list —
events not in the union fall through to the `string` overload of
`usePythonEvent` (BG-84) and lose compile-time typo detection.

| Event type | Interface | Data shape |
|------------|-----------|------------|
| `status_change` | `StatusChangeEvent` | `{ status: string }` |
| `error` | `ErrorEvent` | `{           code: ErrorCodes` |
| `transcription_final` | `TranscriptionFinalEvent` | `{ text: string` |
| `history_changed` | `HistoryChangedEvent` | `{ reason: string }` |
| `state_changed` | `StateChangedEvent` | `Record<string, unknown>` |
| `paste_failed` | `PasteFailedEvent` | `Record<string, unknown>` |
| `download_progress` | `DownloadProgressEvent` | `Record<string, unknown>` |
| `notification` | `NotificationEvent` | `Record<string, unknown>` |
| `vocabulary_suggestion` | `VocabularySuggestionEvent` | `Record<string, unknown>` |
| `microphones_changed` | `MicrophonesChangedEvent` | `Record<string, unknown>` |
| `microphone_test_complete` | `MicrophoneTestCompleteEvent` | `Record<string, unknown>` |
| `audio_clip` | `AudioClipEvent` | `Record<string, unknown>` |
| `tray_menu` | `TrayMenuEvent` | `Record<string, unknown>` |
| `navigate` | `NavigateEvent` | `Record<string, unknown>` |
| `ready` | `ReadyEvent` | `Record<string, unknown>` |
| `bubble_show` | `BubbleShowEvent` | `Record<string, unknown>` |
| `bubble_hide` | `BubbleHideEvent` | `Record<string, unknown>` |
| `bubble_set_state` | `BubbleSetStateEvent` | `Record<string, unknown>` |
| `bubble_level` | `BubbleLevelEvent` | `Record<string, unknown>` |
| `bubble_config` | `BubbleConfigEvent` | `Record<string, unknown>` |
| `show_window` | `ShowWindowEvent` | `Record<string, unknown>` |
| `quit_app` | `QuitAppEvent` | `Record<string, unknown>` |
| `relaunch_app` | `RelaunchAppEvent` | `Record<string, unknown>` |
| `tray_state` | `TrayStateEvent` | `{ icon?: string` |
| `consent_required` | `ConsentRequiredEvent` | `{ provider: string` |
| `parakeet_cpu_fallback` | `ParakeetCpuFallbackEvent` | `{ device: string` |
| `reconnecting` | `ReconnectingEvent` | `{ reason: string }` |
| `reconnected` | `ReconnectedEvent` | `{ reason: string }` |

## See also

- [python-api.md](./python-api.md) — Python class API reference
  (`VoiceTyperApp`, `Recorder`, `IpcServer`, etc.).
- [ARCHITECTURE.md](./ARCHITECTURE.md) — high-level architecture
  overview (renderer <-> Electron main <-> Python backend <-> Rust host).
- [CONTRIBUTING.md §6.4](../CONTRIBUTING.md) — IPC command parity
  contract (the three-allowlist rule enforced here).
- [SECURITY.md](../SECURITY.md) — security model (SEC-002 allowlist,
  SEC-018 TCP auth, SEC-019 renderer gate, SEC-026 sandboxed bubble).
