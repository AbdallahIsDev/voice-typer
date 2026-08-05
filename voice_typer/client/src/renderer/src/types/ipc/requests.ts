// types/ipc/requests.ts
//
// All Python-request interfaces + the `PythonRequest` discriminated
// union. Sent via `window.python.call(...)`.
//
//Split out from the original monolithic `types/ipc.ts` ( / ).
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

//removed ``UpdateConfigRequest``.
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

//(fix): RestartRequest was defined with type 'restart' but
// the server uses 'restart_app'. Removed the dead type — restart is
// triggered from the tray menu via the main process (stopPython sends
// quit_app), not from the renderer.
//
//confirmed that ``restart_app`` / ``quit_app`` are only
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

// ── Additional renderer-called commands ────────────────────────────
//
// Widened coverage for commands invoked from the renderer but not yet
// modelled above. Each interface uses a permissive ``data?`` shape so
// the typed ``PythonCall`` overload (see ``hooks/usePython.ts``) can
// catch command-name typos at compile time without pinning a wire
// shape that may drift. Tighten individual interfaces to bare
// (no-data) or stricter ``data:`` shapes as the wire contracts are
// verified against the Python ``_COMMAND_REGISTRY`` (out of lane for
// this slice — the server-side enum lives in another sub-agent's
// scope; see review.md (Python-side plan) for the Python-side plan).
//
// Commands surveyed via ``rg 'call<...>\("..."'`` across
// ``src/renderer/src``. The parity test in
// ``types/__tests__/ipc-requests-coverage.test.ts`` pins the count so
// future contributors adding a new ``call('foo')`` site get a
// compile-time nudge to add the matching interface here.

export interface CancelModelDownloadRequest {
	type: "cancel_model_download";
	data?: Record<string, unknown>;
}

export interface ForceCancelTranscriptionRequest {
	type: "force_cancel_transcription";
	data?: Record<string, unknown>;
}

// 12 missing interfaces — commands that ARE registered in the
// Python ``_COMMAND_REGISTRY`` (``server/ipc/registry.py``) AND in
// the renderer allowlist (``src/main/allowed-commands.ts``) AND are
// invoked from the renderer but were absent from the ``PythonRequest``
// union. Each interface uses a permissive ``data?: Record<string,
// unknown>`` shape (matching the existing widened entries above) so
// the typed ``PythonCall`` overload in ``hooks/usePython.ts`` catches
// command-name typos at compile time without pinning a wire shape
// that may drift. Tighten individual interfaces to bare (no-data) or
// stricter ``data:`` shapes as the wire contracts are verified
// against the Python handler signatures (out of lane for this slice
// — the Python-side enum lives in another sub-agent's scope).

export interface GetDefaultsRequest {
	type: "get_defaults";
	data?: Record<string, unknown>;
}

export interface DownloadModelRequest {
	type: "download_model";
	data?: Record<string, unknown>;
}

export interface ImportModelRequest {
	type: "import_model";
	data?: Record<string, unknown>;
}

export interface DeleteModelRequest {
	type: "delete_model";
	data?: Record<string, unknown>;
}

export interface TestCloudConnectionRequest {
	type: "test_cloud_connection";
	data?: Record<string, unknown>;
}

export interface SetEscCancelPausedRequest {
	type: "set_esc_cancel_paused";
	data?: Record<string, unknown>;
}

export interface MicrophoneTestStartRequest {
	type: "microphone_test_start";
	data?: Record<string, unknown>;
}

export interface GetVolumeBackendStatusRequest {
	type: "get_volume_backend_status";
	data?: Record<string, unknown>;
}

export interface OpenPrewarmLogRequest {
	type: "open_prewarm_log";
	data?: Record<string, unknown>;
}

export interface OnboardingGetModelOptionsRequest {
	type: "onboarding_get_model_options";
	data?: Record<string, unknown>;
}

export interface OnboardingGetHotkeyPresetsRequest {
	type: "onboarding_get_hotkey_presets";
	data?: Record<string, unknown>;
}

export interface AddTrustedEndpointRequest {
	type: "add_trusted_endpoint";
	data?: Record<string, unknown>;
}

// ``GetDiskInfoRequest`` (phantom): the ``get_disk_info``
// command was a member of this union AND the renderer's
// ``useModelFolder`` hook probed it on mount, BUT it was never
// registered in the Python ``_COMMAND_REGISTRY``
// (``server/ipc/registry.py``) nor allowed through the renderer
// allowlist (``src/main/allowed-commands.ts``). The probe in
// ``useModelFolder.ts`` was therefore dead — it always threw and was
// swallowed silently by a try/catch. The interface has been removed
// and the dead probe code deleted from the hook. If a future
// backend exposes ``get_disk_info``, re-add the interface here AND
// add the matching ``ALLOWED_COMMANDS`` entry + Python handler
// before re-introducing a renderer call site.

export interface GetModelCatalogRequest {
	type: "get_model_catalog";
	data?: Record<string, unknown>;
}

export interface GetModelStatusRequest {
	type: "get_model_status";
	data?: Record<string, unknown>;
}

export interface GetPrewarmStatusRequest {
	type: "get_prewarm_status";
	data?: Record<string, unknown>;
}

export interface GetTemplatesRequest {
	type: "get_templates";
	data?: Record<string, unknown>;
}

export interface MicrophoneTestCancelRequest {
	type: "microphone_test_cancel";
	data?: Record<string, unknown>;
}

export interface MicrophoneTestStopRequest {
	type: "microphone_test_stop";
	data?: Record<string, unknown>;
}

// ``ModelsFolderSupportedRequest`` (phantom): the
// ``models_folder_supported`` command was a member of this union but
// was never registered in ``_COMMAND_REGISTRY`` nor allowed through
// ``ALLOWED_COMMANDS``. The renderer's ``useModelFolder`` hook probed
// it on mount to gate the "Open models folder" button's visibility —
// the probe always failed silently, so the button was never shown.
// Removed; the dead probe code in ``useModelFolder.ts`` is deleted
// too. If a future backend exposes this, re-add the interface here
// AND register the handler + allowlist entry before introducing a
// renderer call site.

export interface OnboardingApplyRequest {
	type: "onboarding_apply";
	data?: Record<string, unknown>;
}

export interface OnboardingGetMicrophonesRequest {
	type: "onboarding_get_microphones";
	data?: Record<string, unknown>;
}

export interface OnboardingIsFirstRunRequest {
	type: "onboarding_is_first_run";
	data?: Record<string, unknown>;
}

export interface OnboardingNextStepRequest {
	type: "onboarding_next_step";
	data?: Record<string, unknown>;
}

export interface OnboardingPrevStepRequest {
	type: "onboarding_prev_step";
	data?: Record<string, unknown>;
}

export interface OnboardingSetHotkeyRequest {
	type: "onboarding_set_hotkey";
	data?: Record<string, unknown>;
}

export interface OnboardingSetMicrophoneRequest {
	type: "onboarding_set_microphone";
	data?: Record<string, unknown>;
}

export interface OnboardingSetModelRequest {
	type: "onboarding_set_model";
	data?: Record<string, unknown>;
}

export interface OnboardingSkipRequest {
	type: "onboarding_skip";
	data?: Record<string, unknown>;
}

export interface OnboardingStartRequest {
	type: "onboarding_start";
	data?: Record<string, unknown>;
}

// ``OpenModelsFolderRequest`` (phantom): the
// ``open_models_folder`` command was a member of this union but was
// never registered in ``_COMMAND_REGISTRY`` nor allowed through
// ``ALLOWED_COMMANDS``. The renderer's ``useModelFolder`` hook
// exposed a ``handleOpenModelsFolder`` action that called it, but the
// button invoking that action was gated behind the (always-failing)
// ``models_folder_supported`` probe above — so the call never
// executed in practice. Removed; the dead ``handleOpenModelsFolder``
// body in ``useModelFolder.ts`` is deleted too. If a future backend
// exposes ``open_models_folder``, re-add the interface here AND
// register the handler + allowlist entry before introducing a
// renderer call site.

export interface PauseModelDownloadRequest {
	type: "pause_model_download";
	data?: Record<string, unknown>;
}

export interface RepasteLastRequest {
	type: "repaste_last";
	data?: Record<string, unknown>;
}

export interface RestoreHistoryRequest {
	type: "restore_history";
	data?: Record<string, unknown>;
}

export interface ResumeModelDownloadRequest {
	type: "resume_model_download";
	data?: Record<string, unknown>;
}

export interface RunPrewarmRequest {
	type: "run_prewarm";
	data?: Record<string, unknown>;
}

export interface SaveTemplatesRequest {
	type: "save_templates";
	data?: Record<string, unknown>;
}

export interface UndoLastRequest {
	type: "undo_last";
	data?: Record<string, unknown>;
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
	| OnboardingCheckPermissionsRequest
	// Additional renderer-called commands (permissive ``data?`` shape).
	// See the individual interface declarations above for the survey
	// methodology and the rationale for the permissive shape.
	| CancelModelDownloadRequest
	| ForceCancelTranscriptionRequest
	// phantom ``GetDiskInfoRequest`` removed — never
	// registered in ``_COMMAND_REGISTRY`` nor allowed through
	// ``ALLOWED_COMMANDS``; the renderer's probe always failed.
	| GetModelCatalogRequest
	| GetModelStatusRequest
	| GetPrewarmStatusRequest
	| GetTemplatesRequest
	| MicrophoneTestCancelRequest
	| MicrophoneTestStopRequest
	// phantom ``ModelsFolderSupportedRequest`` removed —
	// same reason as ``GetDiskInfoRequest`` above.
	| OnboardingApplyRequest
	| OnboardingGetMicrophonesRequest
	| OnboardingIsFirstRunRequest
	| OnboardingNextStepRequest
	| OnboardingPrevStepRequest
	| OnboardingSetHotkeyRequest
	| OnboardingSetMicrophoneRequest
	| OnboardingSetModelRequest
	| OnboardingSkipRequest
	| OnboardingStartRequest
	// phantom ``OpenModelsFolderRequest`` removed —
	// same reason as ``GetDiskInfoRequest`` above.
	| PauseModelDownloadRequest
	| RepasteLastRequest
	| RestoreHistoryRequest
	| ResumeModelDownloadRequest
	| RunPrewarmRequest
	| SaveTemplatesRequest
	| UndoLastRequest
	// 12 missing interfaces added — commands that ARE in
	// ``_COMMAND_REGISTRY`` + ``ALLOWED_COMMANDS`` AND are called
	// from the renderer but were previously absent from this
	// union. See the individual interface declarations above.
	| GetDefaultsRequest
	| DownloadModelRequest
	| ImportModelRequest
	| DeleteModelRequest
	| TestCloudConnectionRequest
	| SetEscCancelPausedRequest
	| MicrophoneTestStartRequest
	| GetVolumeBackendStatusRequest
	| OpenPrewarmLogRequest
	| OnboardingGetModelOptionsRequest
	| OnboardingGetHotkeyPresetsRequest
	| AddTrustedEndpointRequest;

// ── Response data shapes ────────────────────────────────────────────────────────────────
//
//removed the dead ``ToggleDictationResult``,
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
//``RestartResult`` was previously
// removed for the same reason — ``restart_app`` / ``quit_app`` are not
// sent from the renderer (only the Electron main process sends them),
// and the server returns ``{type: "ack", data: {}}`` for these.

// ── Helper: map request type to its response data ─────────────────
//
//removed the dead ``ResponseData<T extends
// PythonRequest["type"]>`` mapped type.  The 26-line conditional-types
// cascade (mapping each request type to its response-data shape) had
// ZERO consumers — ``usePython.call`` is generic over ``<T = unknown>``
// with no constraint on ``PythonRequest["type"]``, so the cascade
// never flowed into any call site.  Callers continue to pass explicit
// type arguments (e.g. ``call<VoiceTyperConfig>('get_config')``),
// which is the pattern actually used throughout the renderer.
//
//``update_config`` and ``restart``
// branches were already removed from this cascade in a prior cleanup
// for the same reason — the server's actual commands are ``set_config``
// and ``restart_app``, and the renderer uses untyped
// ``call<T>('set_config', data)`` and never sends ``restart_app`` from
// the renderer anyway.  ``set_config`` returns ``{type: "ack", data: {}}``
// on success (or ``{type: "ack", data: {accepted: [...], rejected: [...]}}``
//when some keys were silently dropped — see  in the server).
