// types/ipc/enums.ts
//
// Primitive enums / string-literal unions for the IPC layer.
//
//Split out from the original monolithic `types/ipc.ts` ( / ).
// This module owns the three foundational unions that other domain files
// build on:
//   - `RecordingState` — the 6-state backend lifecycle union.
//   - `Page` — the renderer's route-name union.
//   - `ErrorCodes` — the namespaced + legacy error-code union referenced
//     by `ErrorEvent` (in `./push_events`).
//
// No behaviour change vs. the original file — pure structural refactor.

// ── Recording states ──────────────────────────────────────────────

//aligned with the Python ``AppState`` enum in
// ``voice_typer/server/tray_types.py``.  The previous type union
// included 7 dead values (``listening``, ``processing``, ``warming_up``,
// ``downloading``, ``paused``, ``setup``, ``not_configured``) that the // Python backend never emits.  The old Home status-pill helper
// (``statusKeyFor``, removed with RecordingStatusPill) had to
// normalize ``listening`` → ``idle`` to paper over the mismatch;
// other dead values silently fell through to the default "READY"
// label, hiding real state changes from the user.
//
// The 6 values below are the only ones the backend actually emits:
//   idle, recording, transcribing, loading, cancelling, error.
export type RecordingState =
	| "idle"
	| "recording"
	| "transcribing"
	| "loading"
	| "cancelling"
	| "error";

export type Page =
	| "home"
	| "history"
	| "templates"
	| "vocabulary"
	| "models"
	| "microphone"
	| "analytics"
	| "settings"
	| "settingsGeneral"
	| "settingsAiAudio"
	| "settingsAppearance"
	| "settingsPrivacy"
	| "onboarding"
	| "about"
	| "privacy";

// ── Error code union ──────────────────────────────────────────────
//
//(addresses []): narrowed `code` from bare `string` to the
// `ErrorCodes` union below. The namespaced forms (`server.*`, `client.*`)
// are the forward contract; the legacy aliases are still emitted by some
// backend paths (see `voice_typer/server/handlers/_base.py:178`,
// `voice_typer/server/ipc_server.py:1186,1547`, and
// `voice_typer/server/sidecar_ws.py:316,404`) and must remain valid here
// until the migration to namespaced forms is completed (tracked by
//[] /  in the backend).
//
// Referenced by `ErrorEvent` in `./push_events.ts`.
export type ErrorCodes =
	| "client.invalid_field"
	| "client.missing_field"
	| "client.invalid_payload"
	| "client.payload_too_large"
	| "client.rate_limited"
	| "client.path_not_allowed"
	| "client.not_found"
	| "client.auth_failed"
	| "client.consent_required"
	// Backend duplicate enforcement (save_vocabulary): the write would
	// create a duplicate correction (same wrong phrase,
	// case-insensitive). The renderer surfaces the localized "This
	// correction already exists" message and does NOT add the row.
	| "client.duplicate_entry"
	| "server.internal_error"
	| "server.handler_error"
	| "server.file_locked"
	| "server.model_switch_failed"
	| "server.shutting_down"
	| "server.unknown_command"
	| "server.unknown_tray_item"
	| "server.not_found"
	| "server.not_initialized"
	| "server.consent_required"
	| "server.max_connections_reached"
	| "server.duplicate_connection"
	| "server.cloud_auth_failed"
	| "server.cloud_rate_limited"
	| "server.cloud_server_error"
	| "server.cloud_network_error"
	| "server.cloud_config_error"
	| "server.cloud_engine_error"
	| "server.protocol_version_mismatch"
	// Recording-pipeline resample failures emitted by the Python
	// recording layer (see `voice_typer/server/recording/exceptions.py`).
	// The renderer surfaces a targeted "audio pipeline misconfiguration"
	// or "install scipy for better audio quality" message instead of the
	// generic `server.internal_error` toast.
	| "server.recording_resample_failed"
	| "server.recording_resample_unavailable"
	// Renderer-synthesized codes (from Tauri host supervisor events)
	| "respawn_exhausted"
	// Rust-host dispatch cap codes (VP-5). The Tauri `#[tauri::command]`
	// layer in `src-tauri/src/commands/sidecar_cmds/` emits the BARE
	// legacy forms: `pending_full` (dispatch queue full — renderer must
	// back off ~250ms and retry) and `data_too_large` (payload exceeds
	// the 256 KiB cap). The namespaced forms below are canonical targets
	// for future migration, but the bare forms are what the wire
	// actually carries TODAY — both MUST be accepted by any `switch`
	// that narrows `ErrorEvent.code`.
	// (Note: keep semicolons OUT of comment lines inside the union body.
	// The parity-test parser in `tests/test_error_codes_registry.py`
	// slices the union on the first semicolon it sees, so a stray one
	// in a comment silently truncates the parsed set and breaks the
	// `Python ERROR_CODES subset of TS ErrorCodes` assertion.)
	| "pending_full"
	| "data_too_large"
	| "client.pending_full"
	| "client.payload_too_large_dispatch"
	// Legacy aliases (still emitted by some paths for backward compat)
	| "internal_error"
	| "shutting_down"
	| "unknown_command"
	| "unknown_tray_item"
	| "auth_failed"
	| "rate_limited"
	| "invalid_payload"
	| "invalid_field"
	| "missing_field"
	| "not_initialized"
	| "payload_too_large"
	| "handler_error"
	| "sidecar_disconnected"
	| "disallowed_command"
	| "disallowed_window";

// ── Python-call envelope error codes ──────────────────────────────
//
//the Electron main process's `python-call` IPC handler
// (`src/main/ipc/python-call-handler.ts`) stamps a structured `_code`
// field on its `{_error, _code}` error envelope so the renderer can
// branch on the failure class (timeout vs. not-connected vs.
// backend-exited) without parsing the human-readable message text.
//
// The canonical declaration lives in
// `src/shared/python-call-error-code.ts` — a cross-boundary module
// included by BOTH `tsconfig.web.json` and `tsconfig.node.json` (both
// tsconfigs now list `src/shared` recursively in their `include`
// arrays). This file re-exports the type so existing imports
// (`@/types/ipc/enums` -> `PythonCallErrorCode`) keep resolving; the
// previous mirror declaration that required both files to carry a
// pointer comment has been removed.
//
// The codes are stable (the python-call-handler docstring says "never
// rename an existing code, only add new ones"), so future additions
// only need to touch the shared file.
export type { PythonCallErrorCode } from "../../../../shared/python-call-error-code";
