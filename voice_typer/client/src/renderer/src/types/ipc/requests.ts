// types/ipc/requests.ts
//
// All Python-request interfaces + the `PythonRequest` discriminated
// union. Sent via `window.python.call(...)`.
//
// Split out from the original monolithic `types/ipc.ts` (DT-31 / DT-FIX-7).
// No behaviour change vs. the original file — pure structural refactor.
//
// The response-data shapes for `get_history_count` and
// `get_transcription_text` live in `./history.ts` (alongside the other
// history-domain types).

// ── Request messages (sent via window.python.call) ────────────────

export interface GetConfigRequest {
	type: "get_config";
}

// ``set_config`` is the primary config-update RPC. The renderer uses
// it directly via ``call('set_config', diff)`` from a dozen call sites
// (useSettingsConfig, useModelConfig, useTheme, useGlobalKeyboardShortcuts,
// Onboarding, useMicrophoneTest, useMicrophoneData, …). Declared here
// so the strict ``call()`` overload in ``usePython`` can validate the
// diff at the type level.
export interface SetConfigRequest {
	type: "set_config";
	data: Record<string, unknown>;
}

// Connection-readiness probe used by ``useConnection`` and the
// ``usePython`` error-envelope tests. Returns
// ``{ type: "status", data: { connected: boolean, … } }``. No payload.
export interface SetTrayLocaleRequest {
	type: "set_tray_locale";
	data: { locale: string; labels: Record<string, string> };
}

export interface OnboardingResetRequest {
	type: "onboarding_reset";
}

export interface OnboardingCheckPermissionsRequest {
	type: "onboarding_check_permissions";
}

export interface GetStatusRequest {
	type: "get_status";
}

// Microphone VU-meter monitor RPCs used by ``useMicrophoneTest``.
// ``level_monitor_start`` begins a 10 Hz push of ``level_update``
// events for the given mic; ``level_monitor_stop`` tears it down.
export interface LevelMonitorStartRequest {
	type: "level_monitor_start";
	data: { mic_id: string };
}

export interface LevelMonitorStopRequest {
	type: "level_monitor_stop";
}

// One-shot level read used to seed the VU meter before the first
// ``microphone_test_complete`` event arrives. Returns
// ``{ rms: number; peak: number }``.
export interface MicrophoneTestGetLevelRequest {
	type: "microphone_test_get_level";
}

// NEW-IPC-009 / NEW-MISMATCH-002: removed ``UpdateConfigRequest``.
// The server command is ``set_config`` (not ``update_config``), and
// the renderer uses untyped ``call<T>('set_config', data)`` directly
// — there is no consumer of this type.  Keeping a mismatched type
// (claiming ``type: 'update_config'``) gave a false impression of
// type safety while not actually being enforced anywhere.

export interface GetMicrophonesRequest {
	type: "get_microphones";
}

export interface ToggleDictationRequest {
	type: "toggle_dictation";
}

// ERR-IPC-004 (fix): RestartRequest was defined with type 'restart' but
// the server uses 'restart_app'. Removed the dead type — restart is
// triggered from the tray menu via the main process (stopPython sends
// quit_app), not from the renderer.
//
// NEW-IPC-009: confirmed that ``restart_app`` / ``quit_app`` are only
// sent by the Electron main process (tray menu / before-quit), never
// by the renderer.  No ``RestartRequest`` type is needed in the
// renderer's type union.

export interface GetHistoryRequest {
	type: "get_history";
	data?: { limit?: number; offset?: number };
}

export interface DeleteHistoryRequest {
	type: "delete_history";
	data: { id: number };
}

export interface ClearHistoryRequest {
	type: "clear_history";
}

export interface ToggleFavoriteRequest {
	type: "toggle_favorite";
	data: { id: number };
}

export interface GetFavoritesRequest {
	type: "get_favorites";
	data?: { limit?: number; offset?: number };
}

export interface SearchHistoryRequest {
	type: "search_history";
	data: { query: string; limit?: number; offset?: number };
}

export interface GetTodayStatsRequest {
	type: "get_today_stats";
}

// dedicated ``get_history_count`` request — returns the TRUE
// total transcription row count (uncapped, unlike ``get_history``'s
// 200-row sample). Used by the Dashboard's "Total Dictations" stat
// card so the count keeps growing past 200.
export interface GetHistoryCountRequest {
	type: "get_history_count";
}

// on-demand full-text fetch. The renderer calls this when the
// user expands a History row past the 500-char ``text`` preview
// returned by ``get_history``. Response shape:
// ``{type: "transcription_text", data: {id: number, text: string}}``.
export interface GetTranscriptionTextRequest {
	type: "get_transcription_text";
	data: { id: number };
}

export interface GetVocabularyRequest {
	type: "get_vocabulary";
}

export interface SaveVocabularyRequest {
	type: "save_vocabulary";
	data: Record<string, unknown>;
}

export type PythonRequest =
	| GetConfigRequest
	| GetMicrophonesRequest
	| ToggleDictationRequest
	| GetHistoryRequest
	| DeleteHistoryRequest
	| ClearHistoryRequest
	| ToggleFavoriteRequest
	| GetFavoritesRequest
	| SearchHistoryRequest
	| GetTodayStatsRequest
	| GetVocabularyRequest
	| SaveVocabularyRequest
	// new endpoints — see GetHistoryCountRequest /
	// GetTranscriptionTextRequest above for the rationale.
	| GetHistoryCountRequest
	| GetTranscriptionTextRequest
	// Commonly-used commands that were missing from the union.
	// See the individual interface declarations above for rationale.
	| SetConfigRequest
	| GetStatusRequest
	| LevelMonitorStartRequest
	| LevelMonitorStopRequest
	| MicrophoneTestGetLevelRequest
	| SetTrayLocaleRequest
	| OnboardingResetRequest
	| OnboardingCheckPermissionsRequest;

// ── Response data shapes ────────────────────────────────────────────────────────────────
//
// XZ-CC-16: removed the dead ``ToggleDictationResult``,
// ``ToggleFavoriteResult``, and ``SaveVocabularyResult`` interfaces.
// They were only ever referenced by the now-removed ``ResponseData<T>``
// mapped type (see the "Helper: map request type to its response data"
// note below), which itself had zero consumers — ``usePython.call``
// uses ``async <T = unknown>(type: string, ...)`` (generic over T with
// default ``unknown``, NOT constrained to ``PythonRequest["type"]``),
// so the conditional-types cascade never flowed into any call site.
// Callers continue to pass explicit type arguments (e.g.
// ``call<HistoryRecord[]>('get_history')``, ``call<MicrophoneDevice[]>(
// 'get_microphones')``), which is the pattern actually used throughout
// the renderer.  Keeping the dead types gave a false impression of
// type safety while not actually being enforced anywhere.
//
// NEW-IPC-009 / NEW-MISMATCH-002: ``RestartResult`` was previously
// removed for the same reason — ``restart_app`` / ``quit_app`` are not
// sent from the renderer (only the Electron main process sends them),
// and the server returns ``{type: "ack", data: {}}`` for these.

// ── Helper: map request type to its response data ─────────────────
//
// XZ-CC-16: removed the dead ``ResponseData<T extends
// PythonRequest["type"]>`` mapped type.  The 26-line conditional-types
// cascade (mapping each request type to its response-data shape) had
// ZERO consumers — ``usePython.call`` is generic over ``<T = unknown>``
// with no constraint on ``PythonRequest["type"]``, so the cascade
// never flowed into any call site.  Callers continue to pass explicit
// type arguments (e.g. ``call<VoiceTyperConfig>('get_config')``),
// which is the pattern actually used throughout the renderer.
//
// NEW-IPC-009 / NEW-MISMATCH-002: ``update_config`` and ``restart``
// branches were already removed from this cascade in a prior cleanup
// for the same reason — the server's actual commands are ``set_config``
// and ``restart_app``, and the renderer uses untyped
// ``call<T>('set_config', data)`` and never sends ``restart_app`` from
// the renderer anyway.  ``set_config`` returns ``{type: "ack", data: {}}``
// on success (or ``{type: "ack", data: {accepted: [...], rejected: [...]}}``
// when some keys were silently dropped — see NEW-IPC-015 in the server).
