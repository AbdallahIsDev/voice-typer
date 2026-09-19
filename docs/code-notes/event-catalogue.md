# Event catalogue

Code-side anchor: module docstring of `voice_typer/server/event_bus.py` (tests pin names + `Total: N events`).
Spec-side anchor: ADR-0020 §2 Sidecar→UI Event Table. Update all three when adding an event.

Python registry: `EVENT_TYPES` in `event_bus.py` (superset of this list: also includes IPCServer.push-only and some pipeline events).
Rust wire allowlist: `ALLOWED_EVENT_TYPES` in `src-tauri/src/sidecar/ws/event_protocol.rs`.
TS unions: `types/ipc/push_events.ts` + `types/ipc/requests.ts`. Parity: `tests/test_event_types_parity.py`.

## Bus-published events (`event_bus.publish`)

| Event | Payload | Notes |
|-------|---------|-------|
| `ready` | `{}` | First authenticated WS / TCP start |
| `bubble_show` / `bubble_hide` | `{}` | Waveform bubble |
| `bubble_level` | `{rms, peak}` | ~60 Hz RMS/peak |
| `bubble_set_state` | `{state}` | Bubble state machine |
| `transcription_final` | `{text, quality?}` | quality only from Whisper batch path |
| `transcription_partial` | `{text, cycle_id, supported?}` | ≤4 Hz live preview; `supported:false` once when engine lacks words |
| `vocabulary_suggestion` | `{suggestions:[…]}` | Pending corrections |
| `hotkey_capture_cancel` | `{}` | |
| `config_changed` | `{<validated updates>}` | Renderer should refresh |
| `history_changed` | `{reason}` | add/delete/clear/fav/restore |
| `microphone_test_complete` | `{duration}` | |
| `microphones_changed` | `{count}` | Hot-plug |
| `audio_clip` | `{peak, count}` | |
| `recording_started` / `recording_stopped` | `{}` | Dictation lifecycle |
| `download_progress` | model + progress fields | Model download |
| `notification` | `{title, message, duration_ms, critical}` | Renderer toast |
| `navigate` | `{path}` | Tray → UI route |
| `show_window` / `quit_app` / `relaunch_app` | `{}` | Window/app control |
| `paste_failed` | `{message, recovery_path}` | Recovery file action |
| `tray_menu` | `{items}` | Tauri host only (`TAURI_SIDECAR=1`) |
| `tray_state` | `{icon?, tooltip?}` | Tauri host only |
| `consent_required` | `{provider, model, message}` | HuggingFace download gate |
| `parakeet_cpu_fallback` | `{device, reason}` | Tray shows CPU fallback |
| `gpu_cpu_fallback` | `{device, reason}` | Whisper GPU→CPU reload |
| `text_enhancement_failed` | `{}` | Rule-based AI step failed |
| `llm_polish_failed` | `{}` | LLM polish path (distinct) |
| `microphone_permission_revoked` | `{}` | OS revoked mid-recording |
| `microphone_disconnected` | `{}` | Active mic lost |
| `cloud_fallback_used` | `{provider, reason}` | Cloud ASR failed → local |
| `dictation_suppressed` | `{duration, recorded_rms, reason}` | Near-silent UX silence |
| `history_corrupted` | `{path, db_path, recovered_count}` | Rebuilt after corruption |
| `history_fts5_rebuild_failed` | `{db_path, deleted, error, source}` | |
| `paste_deferred` | `{reason, message}` | e.g. macOS Secure Input |
| `tray_fallback_notification` | `{"data":{title,message}}` | Nested under `data` |
| `asr_backend_ready` | `{backend, model_size}` | Background model load ok |
| `asr_backend_load_failed` | `{backend, model_size, failure_reason}` | |

## Offline pack (§7.4)

Schema: `OFFLINE_PACK_EVENT_TYPES` in `voice_typer/server/service/offline_pack.py`.

| Event | Payload |
|-------|---------|
| `offline_pack_download_started` | `{version, url, total_bytes}` |
| `offline_pack_download_progress` | `{version, progress, downloaded_bytes, total_bytes, speed_bytes_per_sec, eta_seconds}` |
| `offline_pack_download_completed` | `{version, sha256}` |
| `offline_pack_download_failed` | `{version, reason, attempts}` |
| `offline_pack_verified` | `{version, sha256}` |
| `offline_pack_missing` | `{version, path}` |
| `offline_pack_corrupt` | `{version, path, reason}` |
| `offline_pack_ready` | `{version, worker_pid}` |
| `worker_started` | `{pid, version}` |
| `worker_crashed` | `{pid, exit_code}` |
| `worker_unloaded` | `{reason}` |
| `transcribe_offline` | REQUEST `{audio_path, sample_rate, language}` → ack `{queued:True}`; result via push |
| `transcribe_offline_result` | PUSH `{text, latency_ms}` |

## IPCServer.push only (not on the bus)

| Event | Payload | Notes |
|-------|---------|-------|
| `state_changed` | `{status, message}` | Once per TCP/WS client connect |
| `status_change` | `{status}` | Every tray state transition |

## Internal config reactions

There is no bus channel for config listeners. React to `set_config` via `ConfigSideEffect` registered on `ConfigApplier` in `voice_typer/server/config_applier.py`.
