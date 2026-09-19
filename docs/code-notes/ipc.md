# IPC / WS / handlers code notes

Deep rationale that previously lived as long inline comments. Inline code keeps
short anchors (`SEC-*`, `C-WS-*`, `RACE-*`, `PERF-*`) and points here.

## Command registry contracts

Source: `voice_typer/server/ipc/registry.py`. Parity contract: CONTRIBUTING.md
§6.4 — Python `_COMMAND_REGISTRY` and Rust `allowed_commands()` must stay in
lockstep. Host-dispatched commands (`shutdown`, `tray_click`, `heartbeat`,
`relaunch_ack`) are intentionally absent from the Rust renderer allowlist.

Pinned sets:
- `_COMMAND_REGISTRY` — 75 keys (tests/test_ipc_server.py).
- `_READONLY_COMMANDS` — `{get_status, get_config, get_model_catalog, heartbeat}`.
  WS/stdin dispatcher bypasses `_dispatch_lock` for these (pure reads).
- `_PYTHON_ONLY_COMMANDS` — `{shutdown, tray_click}`. Renderer must never
  invoke them (spoofing / DoS).
- `_INSTANT_CONTROL_COMMANDS` — `{pause_model_download, resume_model_download,
  cancel_model_download}`. Bypass dispatch lock: mutation is a single atomic
  flag under the download locks. Safety: no network/disk I/O, idempotent,
  never starts a long operation.

### Registry history

Removed / restored command notes (regression guard:
`tests/test_dead_code_stays_removed.py`):

- Removed to match Rust allowlist: `refresh_microphones`, `get_rms_level`,
  `get_audio_status` (service methods remain; IPC route deleted).
- Onboarding aliases unused by renderer: `onboarding_get_step`,
  `onboarding_get_model_catalog`, `onboarding_request_keyboard_permission`.
- Polled endpoints replaced by push events: `microphone_test_status`,
  `level_monitor_status`.
- Host-owned (no Python IPC bridge): `export_diagnostics`, `show_notification`,
  `delete_all_personal_data`, `export_gdpr_bundle`.
- Vocabulary automation deferred: `get_vocabulary_suggestions`,
  `apply_vocabulary_suggestion`, `dismiss_vocabulary_suggestion` (handler mixin
  kept for future rewire).
- Prewarm surface: `run_prewarm` retired then re-implemented in-process
  (`prewarm.status.run_prewarm_now`); `get_prewarm_status` / `open_prewarm_log`
  restored (About → Cache Status card is user-facing).
- `check_accessibility` removed then re-added (Settings → Troubleshooting
  surfaces stale-grant `tccutil` reset); keep Python + Rust allowlists lockstep.
- `test_cloud_connection` / `add_trusted_endpoint` listed in
  `KNOWN_UNDOCUMENTED_COMMANDS` pending ADR-0020 §16 addendum.

## Auth / redaction / limits

- `SEC-002` set_config allowlist (`IPC_CONFIG_ALLOWLIST`) — no fields outside
  it; see `voice_typer/server/config_validators/__init__.py`.
- `SEC-003` redaction — `get_config` must not echo secret fields.
- `SEC-008` outbound pending buffer cap — `_pending_tcp` is bounded; drain
  prefers the most recent events under backpressure.
- `SEC-009` inbound line/frame size cap on TCP (`transport.py`) — OOM DoS.
- `SEC-010` history IPC bounds — `limit`/`offset` capped (history_bounds +
  history handlers).
- `SEC-018` sidecar WS auth token; `SEC-019` renderer command allowlist.
- `SEC-026` sandboxed bubble preload (Tauri capability surface).

## WS wire contract (C-WS-*)

- `C-WS-1` Post-auth frame order in `_handle_connection_inner`:
  `_install_subscriber` → `_emit_ready_if_first` →
  `_emit_initial_state_snapshot`. `ready` is the first post-auth frame.
  Rust `wait_for_auth_ok` accepts only `auth_ok`/`ready` first.
- `C-WS-2` Sidecar→host frames are **str** (TEXT opcode), never bytes.
  Every dispatch response echoes a numeric top-level `id`.
- `C-WS-3` Respawn requests must carry the requesting connection generation
  (`Some(my_generation)`); dequeue-time staleness re-check required.

## Sidecar_ws split leaves (C-ARCH-2)

`sidecar_ws.py` stays the pinned path. Leaves under
`voice_typer/server/sidecar_ws_internals/` own the implementations.
Production resolves cross-module names through sibling **module-object**
attribute reads at call time so tests patch the owning submodule.

Re-exports on `sidecar_ws` keep historical import/patch paths working.
Canonical observers:
- `run()` → `dispatch._make_dispatch`, `stdout_banner._emit_server_started`
- `_dispatch_and_respond` → `outbound._safe_send`
- `_handle_connection_inner` → `handshake._authenticate`, `read_loop._read_loop`,
  `outbound._start_writer`, connection helpers (`_install_subscriber`,
  `_emit_ready_if_first`, …)

Value aliases (`_MAX_FRAME_BYTES`, `_AUTH_TIMEOUT_SECONDS`,
`_HEARTBEAT_RATE_*`, heartbeat/caps) exist so source-grep tests still read
this file. Patch the **owning leaf** when a test needs to rebind.

## Dispatch path

- `_dispatch` validates msg shape, cooperative-shutdown gate, rate limiter
  (SEC-019 / per-connection budgets), registry lookup, read-only lock bypass,
  response id echo.
- `heartbeat` / `relaunch_ack` live on IPCServer (PERF-005 ack replaces fixed
  restart sleep; heartbeat watchdog is host-alive detection).

## Sender / TCP pending buffer

- Push events serialize outside the connection lock; write-readiness gate
  before `sendall`.
- Shutdown path skips non-critical pushes; critical control events may still
  flush. Dead-end buffer capped (SEC-008).
- Never log push payload bodies (may contain transcription text).

## Event bus

Catalogue of event names + payloads: `docs/code-notes/event-catalogue.md`.
Docstring in `event_bus.py` is pinned by `tests/test_event_bus.py`
(`tray_menu`, `tray_state`, `consent_required`, `parakeet_cpu_fallback`,
`Total: 49 events`). RT auto-defer (PERF-2) + bounded deferred queue
(`_DEFERRED_QUEUE_MAX`).

## Logging

- `C-LOG-1` file/terminal log templates live in `log/formatters.py`.
- `C-LOG-2` duration suffix via `voice_typer.server.duration.format_duration`.
