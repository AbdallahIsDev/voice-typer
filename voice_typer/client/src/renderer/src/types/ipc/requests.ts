export interface GetConfigRequest {
	type: "get_config";
}

export interface SetConfigRequest {
	type: "set_config";
	data: Record<string, unknown>;
}

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

export interface ResetMacosAccessibilityRequest {
	type: "reset_macos_accessibility";
}

export interface ResetLinuxPermissionsRequest {
	type: "reset_linux_permissions";
}

// `suggest_reset: false`). The renderer must treat missing
export interface CheckAccessibilityRequest {
	type: "check_accessibility";
}

export interface GetStatusRequest {
	type: "get_status";
}

export interface LevelMonitorStartRequest {
	type: "level_monitor_start";
	data: { mic_id: string };
}

export interface LevelMonitorStopRequest {
	type: "level_monitor_stop";
}

export interface MicrophoneTestGetLevelRequest {
	type: "microphone_test_get_level";
}

export interface GetMicrophonesRequest {
	type: "get_microphones";
}

export interface ToggleDictationRequest {
	type: "toggle_dictation";
}

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

export interface GetHistoryCountRequest {
	type: "get_history_count";
}

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

export interface TestVocabularyCorrectionRequest {
	type: "test_vocabulary_correction";
	data: { text: string };
}

export interface GetCorrectionUsageRequest {
	type: "get_correction_usage";
}

// catch command-name typos at compile time without pinning a wire
// (no-data) or stricter ``data:`` shapes as the wire contracts are

export interface CancelModelDownloadRequest {
	type: "cancel_model_download";
	data?: Record<string, unknown>;
}

export interface ForceCancelTranscriptionRequest {
	type: "force_cancel_transcription";
	data?: Record<string, unknown>;
}

// command-name typos at compile time without pinning a wire shape
// stricter ``data:`` shapes as the wire contracts are verified

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

export interface GetDownloadQueueRequest {
	type: "get_download_queue";
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

// thread, see prewarm/status.run_prewarm_now). The wire shape is
export interface RunPrewarmRequest {
	type: "run_prewarm";
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

// (`src-tauri/src/commands/sidecar_cmds/allowlist.rs`) in lockstep.
// Pinned by `tests/test_event_types_parity.py`.
// Wire shape (mirrors `PACK_EVENT_TYPES` in
export interface TranscribeOfflineRequest {
	type: "transcribe_offline";
	data: {
		audio_path: string;
		sample_rate: number;
		language: string | null;
	};
}

// the remote `pack-manifest.json` from GitHub Releases (C-DATA-1
// (`config.offline_pack_consent` must be true, C-DATA-1 category-3
export interface CheckPackUpdateRequest {
	type: "check_offline_pack_update";
	data?: Record<string, unknown>;
}

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

export interface MicrophoneTestReadAudioRequest {
	type: "microphone_test_read_audio";
	data?: Record<string, unknown>;
}

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

export interface OnboardingSetBackendRequest {
	type: "onboarding_set_backend";
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
	| TestVocabularyCorrectionRequest
	| GetCorrectionUsageRequest
	| GetHistoryCountRequest
	| GetTranscriptionTextRequest
	| SetConfigRequest
	| GetStatusRequest
	| LevelMonitorStartRequest
	| LevelMonitorStopRequest
	| MicrophoneTestGetLevelRequest
	| SetTrayLocaleRequest
	| OnboardingResetRequest
	| OnboardingCheckPermissionsRequest
	| ResetMacosAccessibilityRequest
	| ResetLinuxPermissionsRequest
	| CheckAccessibilityRequest
	| CancelModelDownloadRequest
	| ForceCancelTranscriptionRequest
	| GetModelCatalogRequest
	| GetModelStatusRequest
	| GetPrewarmStatusRequest
	| RunPrewarmRequest
	| GetTemplatesRequest
	| MicrophoneTestCancelRequest
	| MicrophoneTestStopRequest
	| MicrophoneTestReadAudioRequest
	| OnboardingApplyRequest
	| OnboardingGetMicrophonesRequest
	| OnboardingIsFirstRunRequest
	| OnboardingNextStepRequest
	| OnboardingPrevStepRequest
	| OnboardingSetHotkeyRequest
	| OnboardingSetMicrophoneRequest
	| OnboardingSetModelRequest
	| OnboardingSetBackendRequest
	| OnboardingSkipRequest
	| OnboardingStartRequest
	| CheckPackUpdateRequest
	| OpenPrewarmLogRequest
	| PauseModelDownloadRequest
	| RepasteLastRequest
	| RestoreHistoryRequest
	| ResumeModelDownloadRequest
	| SaveTemplatesRequest
	| UndoLastRequest
	| GetDefaultsRequest
	| DownloadModelRequest
	| GetDownloadQueueRequest
	| ImportModelRequest
	| DeleteModelRequest
	| TestCloudConnectionRequest
	| SetEscCancelPausedRequest
	| MicrophoneTestStartRequest
	| GetVolumeBackendStatusRequest
	| OnboardingGetModelOptionsRequest
	| OnboardingGetHotkeyPresetsRequest
	| AddTrustedEndpointRequest
	// worker). See `TranscribeOfflineRequest` above for the wire
	// shape + rationale. Pinned by `tests/test_event_types_parity.py`.
	| TranscribeOfflineRequest;
