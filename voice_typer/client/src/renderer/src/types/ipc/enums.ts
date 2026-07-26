// types/ipc/enums.ts
//
// Primitive enums / string-literal unions for the IPC layer.
//
// Split out from the original monolithic `types/ipc.ts` (DT-31 / DT-FIX-7).
// This module owns the three foundational unions that other domain files
// build on:
//   - `RecordingState` — the 6-state backend lifecycle union.
//   - `Page` — the renderer's route-name union.
//   - `ErrorCodes` — the namespaced + legacy error-code union referenced
//     by `ErrorEvent` (in `./push_events`).
//
// No behaviour change vs. the original file — pure structural refactor.

// ── Recording states ──────────────────────────────────────────────

// NEW-IPC-010: aligned with the Python ``AppState`` enum in
// ``voice_typer/server/tray_types.py``.  The previous type union
// included 7 dead values (``listening``, ``processing``, ``warming_up``,
// ``downloading``, ``paused``, ``setup``, ``not_configured``) that the
// Python backend never emits.  ``Home.tsx::statusKeyFor`` had to
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
	| "onboarding"
	| "about";

// ── Error code union ──────────────────────────────────────────────
//
// EC-FIX-7 (addresses [EC-10]): narrowed `code` from bare `string` to the
// `ErrorCodes` union below. The namespaced forms (`server.*`, `client.*`)
// are the forward contract; the legacy aliases are still emitted by some
// backend paths (see `voice_typer/server/handlers/_base.py:178`,
// `voice_typer/server/ipc_server.py:1186,1547`, and
// `voice_typer/server/sidecar_ws.py:316,404`) and must remain valid here
// until the migration to namespaced forms is completed (tracked by
// [EC-10] / EC-FIX-9 in the backend).
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
	| "server.internal_error"
	| "server.handler_error"
	| "server.file_locked"
	| "server.model_switch_failed"
	| "server.shutting_down"
	| "server.unknown_command"
	| "server.unknown_tray_item"
	| "server.not_found"
	| "server.consent_required"
	| "server.cloud_auth_failed"
	| "server.cloud_rate_limited"
	| "server.cloud_server_error"
	| "server.cloud_network_error"
	| "server.cloud_config_error"
	| "server.cloud_engine_error"
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
