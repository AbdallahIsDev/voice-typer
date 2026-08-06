# IPC Reference

Auto-generated reference for the Voice Typer IPC protocol.

**Source of truth:**

- [_COMMAND_REGISTRY](../voice_typer/server/ipc/registry.py) — server-side handler map (canonical command list)
- [ALLOWED_COMMANDS](../voice_typer/client/src/main/allowed-commands.ts) — Electron main-process allowlist (renderer-reachable subset)
- [types/ipc/](../voice_typer/client/src/renderer/src/types/ipc/) — TypeScript subpackage: requests, push_events, bridge, enums, etc.
- [allowed_commands()](../src-tauri/src/commands/sidecar_cmds.rs) — Rust host's defense-in-depth allowlist gate (CR-4)

> This file is a human-readable summary. The four sources above are the
> authoritative references; if this doc disagrees with any of them, the
> source files win.

## Four-allowlist contract (NH-33 / NH-34 / CR-4)

Every IPC command must exist in **all four** places to be reachable from
the renderer:

1. **Server registry** (`_COMMAND_REGISTRY` in `voice_typer/server/ipc/registry.py`):
   maps `command_name` to `_handler_method` on the `IPCServer` class.
   Without an entry here, the server returns
   `{type:"error", data:{code:"unknown_command"}}`.
2. **Electron allowlist** (`ALLOWED_COMMANDS` in `allowed-commands.ts`): the
   canonical set of commands the renderer may invoke via
   `window.python.call(...)`. The Electron main process rejects anything
   not in this set BEFORE the command reaches the Python backend
   (SEC-019).
3. **Renderer types** (`PythonRequest` / `PythonPushEvent` in the
   `types/ipc/` subpackage): TypeScript unions that give the renderer
   compile-time type safety for the request/response shapes.
4. **Rust allowlist** (`allowed_commands()` in
   `src-tauri/src/commands/sidecar_cmds.rs`): defense-in-depth backstop
   for the Tauri host path (CR-4). The Rust host mirrors the TS
   allowlist exactly so a compromised renderer cannot bypass the TS gate
   by some other route to `invoke('dispatch', ...)`.

The parity tests
`tests/test_electron_ipc_and_build.py::test_allowlist_matches_server_commands`
and `tests/test_security_doc_command_count.py` assert that
`ALLOWED_COMMANDS` (sliced as a literal substring from
`allowed-commands.ts`) matches the renderer-reachable subset of
`_COMMAND_REGISTRY`, and that the Rust `allowed_commands()` set mirrors
the TS set exactly (modulo the `_TS_ONLY_EXCEPTIONS` documented in the
parity test). Commands in `_COMMAND_REGISTRY` but NOT in
`ALLOWED_COMMANDS` are server-only (invoked internally by the backend or
via the Rust tray host's `dispatch_inner`, which **bypasses the
renderer ALLOWED_COMMANDS gate but still routes through
`_COMMAND_REGISTRY`** — i.e. the handler must be registered or the
dispatch fails with `unknown_command`).

## Commands (65 total — 63 renderer-reachable + 2 host-only: shutdown, tray_click)

Grouped by namespace. "✓" in the Allowlist column means the command is
in `ALLOWED_COMMANDS` (renderer-reachable); "—" means server-only.

### System / config / heartbeat

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `get_config` | `_handle_get_config` | ✓ |  |
| `get_defaults` | `_handle_get_defaults` | ✓ |  |
| `get_status` | `_handle_get_status` | ✓ |  |
| `heartbeat` | `_handle_heartbeat` | ✓ | RW-10 watchdog tick — coalesces repeated ticks so a stalled backend doesn't strand the mic open + mutex held. |
| `relaunch_ack` | `_handle_relaunch_ack` | ✓ | PERF-005 relaunch ack — event-driven wait bounded by a 2s timeout. |
| `set_config` | `_handle_set_config` | ✓ |  |

### App control (toggle, undo, repaste, tray, force-cancel, restart/quit)

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `force_cancel_transcription` | `_handle_force_cancel_transcription` | ✓ | 3×90s watchdog timeout. |
| `quit_app` | `_handle_quit_app` | ✓ |  |
| `repaste_last` | `_handle_repaste_last` | ✓ | UX-23: re-paste the last transcription (repaste_handlers mixin). |
| `restart_app` | `_handle_restart_app` | ✓ |  |
| `set_tray_locale` | `_handle_set_tray_locale` | ✓ | TRAY-008: allows set_tray_locale so tray labels update when the user changes the UI locale. |
| `shutdown` | `_handle_shutdown` | — | Cooperative shutdown — handler runs identically on TCP / stdin / WS. |
| `toggle_dictation` | `_handle_toggle_dictation` | ✓ |  |
| `tray_click` | `_handle_tray_click` | — | Host-only — Rust tray dispatcher invokes via `dispatch_inner`, so a renderer `dispatch` call returns "unknown command". |
| `undo_last` | `_handle_undo_last` | ✓ |  |

### Onboarding wizard

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `onboarding_apply` | `_handle_onboarding_apply` | ✓ |  |
| `onboarding_check_permissions` | `_handle_onboarding_check_permissions` | ✓ | Backs the Onboarding wizard's Permissions step. |
| `onboarding_get_hotkey_presets` | `_handle_onboarding_get_hotkey_presets` | ✓ |  |
| `onboarding_get_microphones` | `_handle_onboarding_get_microphones` | ✓ |  |
| `onboarding_get_model_options` | `_handle_onboarding_get_model_options` | ✓ |  |
| `onboarding_is_first_run` | `_handle_onboarding_is_first_run` | ✓ |  |
| `onboarding_next_step` | `_handle_onboarding_next_step` | ✓ |  |
| `onboarding_prev_step` | `_handle_onboarding_prev_step` | ✓ |  |
| `onboarding_reset` | `_handle_onboarding_reset` | ✓ | G4-M-10 + PVT-G5-025 (session-3 + 5): onboarding reset — invoked by the Onboarding wizard's "Start over" button. |
| `onboarding_set_hotkey` | `_handle_onboarding_set_hotkey` | ✓ |  |
| `onboarding_set_microphone` | `_handle_onboarding_set_microphone` | ✓ |  |
| `onboarding_set_model` | `_handle_onboarding_set_model` | ✓ |  |
| `onboarding_skip` | `_handle_onboarding_skip` | ✓ |  |
| `onboarding_start` | `_handle_onboarding_start` | ✓ |  |

### Models (download, import, delete, status, prewarm, cloud test, trusted endpoints)

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `cancel_model_download` | `_handle_cancel_model_download` | ✓ | NEW-PRIV-011: allows cancel_model_download so the renderer can cancel an in-progress download. |
| `delete_model` | `_handle_delete_model` | ✓ | NEW-UX-005: allows delete_model so the renderer can actually delete model files from disk. |
| `download_model` | `_handle_download_model` | ✓ |  |
| `get_model_catalog` | `_handle_get_model_catalog` | ✓ | Models page: VRAM, languages, speed/accuracy ratings. |
| `get_model_status` | `_handle_get_model_status` | ✓ |  |
| `get_prewarm_status` | `_handle_get_prewarm_status` | ✓ | Backs the Models page's "Cache Status" card. |
| `get_volume_backend_status` | `_handle_get_volume_backend_status` | ✓ |  |
| `import_model` | `_handle_import_model` | ✓ | MODEL-IMPORT: allows import_model so the Models page can scan and import pre-downloaded model directories. |
| `open_prewarm_log` | `_handle_open_prewarm_log` | ✓ | Opens the prewarm log file — invoked from the About page's "View prewarm log" button. |
| `pause_model_download` | `_handle_pause_model_download` | ✓ | NEW-PAUSE-001: pause/resume in-progress model downloads. |
| `resume_model_download` | `_handle_resume_model_download` | ✓ |  |
| `run_prewarm` | `_handle_run_prewarm` | ✓ | Spawns the prewarm subprocess; the frontend polls get_prewarm_status to track it. |
| `test_cloud_connection` | `_handle_test_cloud_connection` | ✓ | Cloud ASR/LLM endpoint reachability probe — invoked from Settings → Models → Cloud "Test connection" button. Handler: `cloud_test_handlers.py`. |
| `add_trusted_endpoint` | `_handle_add_trusted_endpoint` | ✓ | ADR-0017 §"Runtime Extensions": extends the per-process URL allowlist at runtime so users can configure self-hosted ASR/LLM endpoints. Handler: `config_handlers.py`. |

### History (CRUD, favorites, search, today stats, count, transcription text)

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `clear_history` | `_handle_clear_history` | ✓ |  |
| `delete_history` | `_handle_delete_history` | ✓ |  |
| `get_favorites` | `_handle_get_favorites` | ✓ |  |
| `get_history` | `_handle_get_history` | ✓ |  |
| `get_history_count` | `_handle_get_history_count` | ✓ | Dashboard "Total Dictations" stat — returns the count of stored history rows without transferring the full row list. |
| `get_transcription_text` | `_handle_get_transcription_text` | ✓ | History page expansion — fetches the full transcription text for a single history row by id. |
| `get_today_stats` | `_handle_get_today_stats` | ✓ |  |
| `restore_history` | `_handle_restore_history` | ✓ |  |
| `search_history` | `_handle_search_history` | ✓ |  |
| `toggle_favorite` | `_handle_toggle_favorite` | ✓ |  |

### Vocabulary (save, get)

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `get_vocabulary` | `_handle_get_vocabulary` | ✓ |  |
| `save_vocabulary` | `_handle_save_vocabulary` | ✓ |  |

### Templates

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `get_templates` | `_handle_get_templates` | ✓ |  |
| `save_templates` | `_handle_save_templates` | ✓ |  |

### Microphones (list)

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `get_microphones` | `_handle_get_microphones` | ✓ |  |

### Microphone test (start, stop, cancel, level)

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `microphone_test_cancel` | `_handle_microphone_test_cancel` | ✓ |  |
| `microphone_test_get_level` | `_handle_microphone_test_get_level` | ✓ |  |
| `microphone_test_start` | `_handle_microphone_test_start` | ✓ | Microphone test commands |
| `microphone_test_stop` | `_handle_microphone_test_stop` | ✓ |  |

### Continuous level monitor

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `level_monitor_start` | `_handle_level_monitor_start` | ✓ | Continuous level monitor |
| `level_monitor_stop` | `_handle_level_monitor_stop` | ✓ |  |

### Hotkey / ESC cancel

| Command | Handler | Allowlist | Notes |
|---------|---------|-----------|-------|
| `set_esc_cancel_paused` | `_handle_set_esc_cancel_paused` | ✓ | Pauses ESC-cancel while the UI is capturing a custom hotkey. |

### Removed / never-existed commands (documented for searchability — do NOT re-add)

The following command names appear in older drafts of this document but
are **not** present in `_COMMAND_REGISTRY` and were never reachable from
the renderer. They are listed here so search-engine queries landing on
this page find the canonical "this command does not exist" answer:

`apply_vocabulary_suggestion`, `check_accessibility`,
`delete_all_personal_data`, `dismiss_vocabulary_suggestion`,
`export_diagnostics`, `export_gdpr_bundle`, `get_audio_status`,
`get_rms_level`, `get_vocabulary_suggestions`, `level_monitor_status`,
`microphone_test_status`, `onboarding_get_model_catalog`,
`onboarding_get_step`, `onboarding_request_keyboard_permission`,
`refresh_microphones`, `show_electron_notification`,
`test_llm_connection`.

The corresponding host-side workflows (vocabulary automation pipeline,
macOS Accessibility probe, GDPR export/delete, diagnostics export, LLM
connection test, onboarding step / model-catalog reads, microphone
refresh, Electron notification, RMS / audio-status reads, microphone
test status, level monitor status) are all handled by dedicated service
modules invoked directly by the host (not via the IPC `dispatch` path)
or by renderer-reachable substitutes listed in the tables above.

## Push events (36 typed)

Push events flow server to renderer via `window.python.onEvent(callback)`.
The `PythonPushEvent` union in `types/ipc/push_events.ts` is the canonical
list — events not in the union fall through to the `string` overload of
`usePythonEvent` (BG-84) and lose compile-time typo detection.

| Event type | Interface | Data shape |
|------------|-----------|------------|
| `status_change` | `StatusChangeEvent` | `{ status: string }` |
| `error` | `ErrorEvent` | `{ code: ErrorCodes, message, command?, field?, id? }` |
| `transcription_final` | `TranscriptionFinalEvent` | `{ text: string, ... }` |
| `recording_started` | `RecordingStartedEvent` | bare `{ type: "recording_started" }` — fires when recording actually starts; backs the start sound cue. |
| `recording_stopped` | `RecordingStoppedEvent` | bare `{ type: "recording_stopped" }` — fires when recording actually stops; backs the stop sound cue. |
| `config_changed` | `ConfigChangedEvent` | `{ key?: string, ... }` — emitted after `set_config` so subscribers can refetch without polling. |
| `hotkey_capture_cancel` | `HotkeyCaptureCancelEvent` | bare frame — cancels the Settings hotkey-picker capture dialog. |
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
| `tray_state` | `TrayStateEvent` | `{ icon?: string, label?: string, ... }` |
| `consent_required` | `ConsentRequiredEvent` | `{ provider: string, ... }` |
| `parakeet_cpu_fallback` | `ParakeetCpuFallbackEvent` | `{ device: string, ... }` |
| `asr_backend_disabled` | `AsrBackendDisabledEvent` | `{ backend: string, reason: string, ... }` — emitted from `asr_registry._record_failure` when a backend trips its failure threshold. |
| `asr_last_resort_unloaded` | `AsrLastResortUnloadedEvent` | `{ backend: string, reason: string, ... }` — emitted when the last-resort ASR backend is force-unloaded. |
| `llm_polish_failed` | `LlmPolishFailedEvent` | `{ reason: string, ... }` — emitted when LLM polishing fails so the UI can fall back to raw transcription. |
| `reconnecting` | `ReconnectingEvent` | `{ reason: string }` |
| `reconnected` | `ReconnectedEvent` | `{ reason: string }` |
| `mic_level` | `MicLevelEvent` | `{ rms: number, peak: number, active: boolean }` — continuous level monitor stream for the Settings microphone level meter. |

## WebSocket transport (Tauri sidecar)

Under the Tauri v2 host (ADR-0020), the renderer↔backend transport
switches from TCP-on-loopback to a localhost WebSocket. The WS transport
reuses the same `_COMMAND_REGISTRY` and the same per-connection rate
limiter as the TCP transport — the only differences are the framing and
the auth handshake.

- **URL scheme**: `ws://127.0.0.1:<ephemeral>`. The Python sidecar binds
  `127.0.0.1:0` (OS-assigned port) and emits
  `{"event":"server_started","port":N}` to stdout for the Rust host to
  discover (no port is hard-coded).
- **Auth frame**: the first frame on a new connection MUST be a JSON
  object of the shape `{type:"auth", token:<hex>, protocol_version?:<int>}`.
  The server compares `token` against `VOICE_TYPER_IPC_TOKEN` using
  `hmac.compare_digest` (constant-time). `protocol_version` is optional
  — older hosts that don't send it continue to function.
- **Protocol version**: `PROTOCOL_VERSION = 1` (see `sidecar_ws.py`).
  When the host sends a `protocol_version` that doesn't match the
  sidecar's, the server logs a `protocol_version_mismatch` WARNING
  (visible in diagnostics as the `[SIDECAR-WS] protocol version skew`
  line) but does NOT reject the connection — version negotiation is
  defense-in-depth, not a security gate.
- **Frame cap**: 1 MiB (`_MAX_FRAME_BYTES = 1 * 1024 * 1024`). Frames
  exceeding the cap are dropped with a `[SIDECAR-WS] frame too large`
  log line and a `dropped` outcome is reported on the send path.
- **Cooperative shutdown**: the host sends a bare
  `{type:"shutdown"}` frame to request graceful teardown; the sidecar
  closes its accept loop, drains in-flight handlers, and exits. This is
  the same `shutdown` handler documented in the App-control table
  above — the WS transport just dispatches it via a synthetic frame
  type rather than the regular `dispatch` envelope.

For the full Python-side module deep dive, see
[`docs/modules/sidecar_ws.md`](modules/sidecar_ws.md); for the Rust host
side, see [`docs/migration/tauri-sidecar-bridge.md`](migration/tauri-sidecar-bridge.md).

## See also

- [python-api.md](./python-api.md) — Python class API reference
  (`VoiceTyperApp`, `Recorder`, `IpcServer`, etc.).
- [ARCHITECTURE.md](./ARCHITECTURE.md) — high-level architecture
  overview (renderer <-> Electron main <-> Python backend <-> Rust host).
- [modules/sidecar_ws.md](./modules/sidecar_ws.md) — Tauri sidecar
  WebSocket transport module reference.
- [migration/tauri-sidecar-bridge.md](./migration/tauri-sidecar-bridge.md) —
  Tauri ↔ Python sidecar bridge architecture + Rust host layout.
- [CONTRIBUTING.md §6.4](../CONTRIBUTING.md) — IPC command parity
  contract (the four-allowlist rule enforced here).
- [SECURITY.md](../SECURITY.md) — security model (SEC-002 allowlist,
  SEC-018 TCP auth, SEC-019 renderer gate, SEC-026 sandboxed bubble).
