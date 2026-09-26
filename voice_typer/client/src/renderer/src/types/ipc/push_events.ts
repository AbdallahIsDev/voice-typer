import type { ErrorCodes } from "./enums";

export interface StatusChangeEvent {
	type: "status_change";
	// message mirrors set_state(state, message); optional for legacy emitters.
	data: { status: string; message?: string };
}

// message OPTIONAL (unknown_tray_item omits it); other fields optional.
export interface ErrorEvent {
	type: "error";
	data: {
		code: ErrorCodes;
		//`message` is OPTIONAL, the `unknown_tray_item`
		message?: string;
		field?: string;
		command?: string;
		id?: string | number;
	};
}

// Wire: {type:"transcription_partial", data:{text, cycle_id?, supported?:false}}
export interface TranscriptionPartialEvent {
	type: "transcription_partial";
	data: { text: string; cycle_id?: string; supported?: false };
}

// quality optional (Whisper batch only). duration_ms not on wire — do not re-add.
export interface TranscriptionQualitySummary {
	/**
	 * Mean per-segment `avg_logprob`, closer to 0 = more confident decoding.
	 */
	mean_logprob?: number;
	/**
	 * Worst single-segment `avg_logprob`.
	 */
	min_logprob?: number;
	/**
	 * Highest per-segment `no_speech_prob` (high = segment likely silent).
	 */
	no_speech_prob_max?: number;
	/**
	 * How many segments contributed numeric stats.
	 */
	segments?: number;
}

export interface TranscriptionFinalEvent {
	type: "transcription_final";
	data: { text: string; quality?: TranscriptionQualitySummary };
}

export interface RecordingStartedEvent {
	type: "recording_started";
}

export interface RecordingStoppedEvent {
	type: "recording_stopped";
}

// Do NOT re-add model_loaded without publisher + subscriber + parity test.

/**
 * Pushed after every successful set_config so the renderer can
 * update UI-local state (font-scale, theme, etc.) immediately
 * without needing a full get_config round-trip.
 */
export interface ConfigChangedEvent {
	type: "config_changed";
	/**
	 * The validated subset of fields that were actually applied.
	 */
	data: Record<string, unknown>;
}

/**
 * Pushed when the backend detects Esc during hotkey capture mode.
 * The backend consumes the key at the OS level (RegisterHotKey), so the
 * DOM keydown event never reaches the renderer, this event tells the
 */
export interface HotkeyCaptureCancelEvent {
	type: "hotkey_capture_cancel";
}

/**
 * current renderer page (clear/delete/restore/star from another window,
 * caches so they don't show ghost records. `reason` is one of
 * "cleared" | "deleted" | "restored" | "favorite_toggled".
 */
export interface HistoryChangedEvent {
	type: "history_changed";
	data: { reason: string };
}

// percent: number }` once the wire shape is verified).

//emitted on every client connect
export interface StateChangedEvent {
	type: "state_changed";
	data: { status?: string; message?: string };
}

// emitted by at least one `event_bus.publish(...)` call in the Python

/**
 * Paste-to-active-window failed (Linux/macOS paste path); renderer
 * surfaces a toast. Emitted from `paste_controller.py`.
 */
export interface PasteFailedEvent {
	type: "paste_failed";
	data: Record<string, unknown>;
}

/**
 * HuggingFace model download progress (chunk counter). Emitted from
 * `model_downloader.py`. Consumed by `Home.tsx:402`.
 */
export interface DownloadProgressEvent {
	type: "download_progress";
	data: Record<string, unknown>;
}

/**
 * Generic user-facing notification (title + body + severity). Emitted
 * from various handler mixins.
 */
export interface NotificationEvent {
	type: "notification";
	data: Record<string, unknown>;
}

/**
 * A learned vocabulary correction suggestion surfaced for the user to
 * accept/reject. Emitted from `vocabulary_suggester.py`.
 */
export interface VocabularySuggestionEvent {
	type: "vocabulary_suggestion";
	data: Record<string, unknown>;
}

/**
 * The set of available microphones changed (hot-plug / unplug).
 * Emitted from `mic_watcher.py`.
 */
export interface MicrophonesChangedEvent {
	type: "microphones_changed";
	data: Record<string, unknown>;
}

/**
 * A `test_microphone` request finished, renderer shows the recorded
 * duration + RMS. Emitted from `microphone_handlers.py`.
 */
export interface MicrophoneTestCompleteEvent {
	type: "microphone_test_complete";
	data: Record<string, unknown>;
}

/**
 * A raw audio clip is being pushed (e.g. for the waveform display or
 * for clipboard copy). Emitted from `recording_controller.py`.
 */
export interface AudioClipEvent {
	type: "audio_clip";
	data: Record<string, unknown>;
}

/**
 * The tray menu config changed, renderer can refresh its in-app
 * mirror. Emitted from `tray.py`.
 */
export interface TrayMenuEvent {
	type: "tray_menu";
	data: Record<string, unknown>;
}

/**
 * Request that the renderer switch to a different page. Emitted from
 * tray menu clicks + onboarding flow. Consumed by `App.tsx:209`.
 */
export interface NavigateEvent {
	type: "navigate";
	data: Record<string, unknown>;
}

/**
 * Backend finished its startup sequence, renderer can hide the
 * loading splash. Emitted from `startup_sequence.py`.
 */
export interface ReadyEvent {
	type: "ready";
	data: Record<string, unknown>;
}

export interface BubbleShowEvent {
	type: "bubble_show";
	data: Record<string, unknown>;
}

export interface BubbleHideEvent {
	type: "bubble_hide";
	data: Record<string, unknown>;
}

export interface BubbleSetStateEvent {
	type: "bubble_set_state";
	data: Record<string, unknown>;
}

export interface BubbleLevelEvent {
	type: "bubble_level";
	data: Record<string, unknown>;
}

export interface BubbleConfigEvent {
	type: "bubble_config";
	data: Record<string, unknown>;
}

/**
 * Tray "Open app", Python asks the host to show + focus the dashboard.
 */
export interface ShowWindowEvent {
	type: "show_window";
	data: Record<string, unknown>;
}

/**
 * Tray "Quit", Python is about to force-exit.
 */
export interface QuitAppEvent {
	type: "quit_app";
	data: Record<string, unknown>;
}

/**
 * Tray "Restart", Python's `restart_app()` pushes this BEFORE calling
 * `sys.exit(0)`.
 *
 */
export interface RelaunchAppEvent {
	type: "relaunch_app";
	data: Record<string, unknown>;
}

/**
 * Pushed by `voice_typer/server/tray_menu.py:416` to update the tray
 * icon + tooltip. The Python emitter (`_push_tray_state`) only
 * includes `icon` and/or `tooltip` if non-null, and bails out if BOTH
 */
export interface TrayStateEvent {
	type: "tray_state";
	data: { icon?: string; tooltip?: string };
}

/**
 * Pushed when a consent-gated action is refused because the user has
 * from a single emitter and three of its "required" fields were absent
 * from the other emitters):
 */
export interface ConsentRequiredEvent {
	type: "consent_required";
	data: {
		consent_field?: string;
		provider?: string;
		model?: string;
		message?: string;
	};
}

/**
 * Pushed by `voice_typer/server/parakeet_engine.py:910-915` when GPU
 * transcription fails and the engine falls back to CPU. `device` is
 * always `"cpu"` today; `reason` is the truncated exception message
 */
export interface ParakeetCpuFallbackEvent {
	type: "parakeet_cpu_fallback";
	data: { device: string; reason: string };
}

/**
 * Pushed by `voice_typer/server/asr_registry.py:625-637` when an ASR
 * backend (e.g. whisper CUDA) auto-disables after repeated OOM / load
 * failures and the registry falls back to a different backend. The
 */
export interface ASRBackendDisabledEvent {
	type: "asr_backend_disabled";
	data: {
		/**
		 * The disabled backend's name (e.g. `"whisper"`, `"parakeet"`).
		 */
		backend: string;
		/**
		 * Number of consecutive failures that triggered the disable.
		 */
		failure_count: number;
		/**
		 * ISO-8601 timestamp emitted by the Python `datetime.now(timezone.utc)`.
		 */
		timestamp: string;
	};
}

/**
 * Pushed by `voice_typer/server/asr_registry.py:361-372` when the
 * LAST-RESORT ASR backend is unloaded, i.e. no ASR backend is
 * available until the user manually restarts the app or reconfigures.
 */
export interface ASRLastResortUnloadedEvent {
	type: "asr_last_resort_unloaded";
	data: {
		/**
		 * The last-resort backend's name that was just unloaded.
		 */
		backend: string;
		/**
		 * ISO-8601 timestamp emitted by the Python `datetime.now(timezone.utc)`.
		 */
		timestamp: string;
	};
}

/**
 * Pushed by `voice_typer/server/dictation_pipeline.py:919` when the
 * LLM polish step (the optional `ai_enhancement_enabled` path that
 * post-processes the raw transcription with grammar / style fixes)
 */
export interface LLMPolishFailedEvent {
	type: "llm_polish_failed";
}

/**
 * Pushed by `voice_typer/server/dictation_pipeline/enhancement_steps.py`
 * `_apply_ai_enhancement`, Step 7b) when the RULE-BASED text
 * enhancement pass fails. Distinct from {@link LLMPolishFailedEvent}
 */
export interface TextEnhancementFailedEvent {
	type: "text_enhancement_failed";
}

/**
 * Pushed by the level monitor / recording pipeline when the ACTIVE
 * microphone disappears (unplug, Bluetooth power-off, driver reset) and
 * retries are exhausted. Emitters (all publish the same wire shape):
 */
export interface DeviceLostEvent {
	type: "device_lost";
	data: { source: string };
}

//(addresses []): these events are NOT emitted by the Python
// `src-tauri/src/sidecar/supervisor.rs` or predecessor main) when the transport
// every IPC message must have a matching type definition).
// `reason` values currently emitted:

/**
 * Pushed when the host bridge starts a reconnect attempt after a
 * transport drop. Consumed by `hooks/useConnection.ts:277`.
 */
export interface ReconnectingEvent {
	type: "reconnecting";
	data: { reason: string };
}

/**
 * Pushed when the host bridge successfully reconnected to the Python
 * backend. Consumed by `hooks/useConnection.ts:287`.
 */
export interface ReconnectedEvent {
	type: "reconnected";
	data: { reason: string };
}

// worker pattern as `bubble_level` (≤30 Hz). Consumed by
export interface MicLevelEvent {
	type: "mic_level";
	data: { level: number; peak: number; active: boolean };
}

// `src-tauri/src/sidecar/ws/event_protocol.rs` includes every name
// here so the host does not silently drop the frames. Pinned by

/**
 * Offline-pack download lifecycle, emitted by `voice_typer/server/service/offline_pack.py`
 * when a runtime-pack download begins. Payload mirrors the model-download
 * `download_progress` event shape so the existing `useModelDownload` UI
 */
export interface OfflinePackDownloadStartedEvent {
	type: "offline_pack_download_started";
	data: { version: string; url: string; total_bytes: number };
}

/**
 * Pack download progress (silent, no UI surface today). Emitted at
 * ~1 Hz while the pack is downloading. The renderer may log this for
 * diagnostics; no user-visible component subscribes (the
 */
export interface OfflinePackDownloadProgressEvent {
	type: "offline_pack_download_progress";
	data: {
		version: string;
		progress: number;
		downloaded_bytes: number;
		total_bytes: number;
		speed_bytes_per_sec: number;
		eta_seconds: number;
	};
}

/**
 * Pack download completed, emitted when the download finishes
 * verification is the NEXT step; see `offline_pack_verified` /
 * `offline_pack_corrupt`). Payload carries the computed SHA256 so the
 */
export interface OfflinePackDownloadCompletedEvent {
	type: "offline_pack_download_completed";
	data: { version: string; sha256: string };
}

/**
 * Pack download failed, emitted when the download gives up after
 * exhausting the §8.2 / §8.7 retry budgets (corruption recovery + GitHub
 * rate-limit backoff). The renderer surfaces a tray notification +
 */
export interface OfflinePackDownloadFailedEvent {
	type: "offline_pack_download_failed";
	data: { version: string; reason: string; attempts: number };
}

/**
 * Pack verified, SHA256 + signature (Windows Authenticode / macOS
 * notarization ticket) both pass. The renderer's "Pack status" badge
 * flips green.
 */
export interface OfflinePackVerifiedEvent {
	type: "offline_pack_verified";
	data: { version: string; sha256: string };
}

/**
 * Pack missing, the cheap existence probe (`os.path.exists` on the
 * expected pack path) found no pack file. Emitted on startup if the
 * pack is configured-but-absent (e.g. the user deleted it, or a
 */
export interface OfflinePackMissingEvent {
	type: "offline_pack_missing";
	data: { version: string | null; path: string };
}

/**
 * Pack corrupt, SHA256 mismatch or signature verification failed.
 * Emitted by the background checksum (§8.16) when the post-download
 * integrity check fails. The renderer surfaces a "Pack corrupt —
 */
export interface OfflinePackCorruptEvent {
	type: "offline_pack_corrupt";
	data: { version: string; path: string; reason: string };
}

/**
 * Pack ready, the worker process has started AND prewarmed the ASR
 * engine (worker lifecycle per §6.2). The renderer's
 * "Offline engine" status flips to "Ready"; queued `transcribe_offline`
 */
export interface OfflinePackReadyEvent {
	type: "offline_pack_ready";
	data: { version: string; worker_pid: number };
}

/**
 * Worker started, the worker process has spawned and completed its
 * WS handshake with the slim core. Prewarm is NOT done yet (see
 * `offline_pack_ready` for that signal).
 */
export interface WorkerStartedEvent {
	type: "worker_started";
	data: { pid: number; version: string };
}

/**
 * Worker crashed, the worker process exited with a non-zero code
 * or was killed by a signal). The slim core's supervisor restarts
 * it (with exponential backoff); the renderer surfaces a degraded-mode
 */
export interface WorkerCrashedEvent {
	type: "worker_crashed";
	data: { pid: number; exit_code: number };
}

/**
 * Worker unloaded, the worker process was unloaded (either by the
 * idle-timeout path or by an explicit user action like the "Keep
 * offline engine running" checkbox being toggled off). The renderer's
 */
export interface WorkerUnloadedEvent {
	type: "worker_unloaded";
	data: { reason: string };
}

/**
 * Offline transcription result, pushed by the worker via the slim
 * core when a `transcribe_offline` request completes. The slim core
 * forwards this to the renderer via the standard event bus (the
 */
export interface TranscribeOfflineResultEvent {
	type: "transcribe_offline_result";
	data: { text: string; latency_ms: number };
}

/**
 * ADR-0023 media job progress, published per window fraction (0-1).
 * Pushed by `handlers/media_handlers.py` during chunked transcription.
 * `phase` distinguishes the download / model-load / transcribe stages;
 * `eta_seconds` is a measured estimate, `null` until one window lands.
 */
export interface MediaTranscribeProgressEvent {
	type: "media_transcribe_progress";
	data: {
		job_id: string;
		progress: number;
		phase: "downloading" | "loading_model" | "transcribing";
		eta_seconds: number | null;
		duration_seconds: number | null;
	};
}

/**
 * ADR-0023 media job completion, carries the History row id + char count.
 * `partial` is true when the job was cancelled and the saved text is a
 * partial transcript.
 */
export interface MediaTranscribeCompleteEvent {
	type: "media_transcribe_complete";
	data: {
		job_id: string;
		/** `null` when the transcript was empty (no History row written). */
		row_id: number | null;
		chars: number;
		partial: boolean;
	};
}

/**
 * ADR-0023 media job failure (E13), pushed by `media_ingest/jobs.py`
 * when the job thread raises. `code` is the machine-readable media
 * ingest error code; `message` is the user-safe text.
 */
export interface MediaTranscribeErrorEvent {
	type: "media_transcribe_error";
	data: { job_id: string; code: string; message: string };
}

// parity is pinned by `tests/test_event_types_parity.py`

/**
 * Pushed by `model_manager/_change.py` when a background model load
 * SUCCEEDS (the load runs on a daemon thread after `set_config`
 * acked; this event is the completion signal the ack's
 */
export interface AsrBackendReadyEvent {
	type: "asr_backend_ready";
	data: { backend: string; model_size: string };
}

/**
 * Pushed by `model_manager/_change.py` when a background model load
 * FAILS after the `set_config` ack already returned, the renderer
 * must surface the failure (the Models-page "Using model" snack from
 */
export interface AsrBackendLoadFailedEvent {
	type: "asr_backend_load_failed";
	data: { backend: string; model_size: string; failure_reason: string };
}

/**
 * Pushed by `recording_controller.py` when the OS revokes microphone
 * permission MID-RECORDING. The recording is stopped and the renderer
 * shows the dedicated "Mic permission revoked" banner (distinct from
 */
export interface MicrophonePermissionRevokedEvent {
	type: "microphone_permission_revoked";
}

/**
 * Pushed by `mic_lifecycle_hooks.py` when the active microphone
 * disappears from the recorder's stream (fast OS-event path or the
 * disconnect-retry exhaustion path, the recorder-stream counterpart
 */
export interface MicrophoneDisconnectedEvent {
	type: "microphone_disconnected";
}

/**
 * Pushed by `cloud/_engine.py` when a cloud ASR provider fails and the
 * local engine takes over for that transcription. `reason` is the
 * truncated exception message (max 200 chars). Consumed by
 */
export interface CloudFallbackUsedEvent {
	type: "cloud_fallback_used";
	data: { provider: string; reason: string };
}

/**
 * Pushed by `dictation_pipeline/transcribe_step.py` when a short
 * near-silent recording's failure notification is suppressed (the
 * UX-SILENCE-GRACE path, the user tapped the hotkey accidentally).
 */
export interface DictationSuppressedEvent {
	type: "dictation_suppressed";
	data: { duration: number; recorded_rms: number; reason: string };
}

/**
 * `recovered_count` rows survived). Consumed by
 * count + the kept quarantine file).
 */
export interface HistoryCorruptedEvent {
	type: "history_corrupted";
	data: { path: string; db_path: string; recovered_count: number };
}

/**
 * FTS5 index rebuild fails after a delete/clear, the privacy
 * guarantee (deleted text unrecoverable) is broken and the user
 * warning; fires on real rebuild-failure evidence only).
 */
export interface HistoryFts5RebuildFailedEvent {
	type: "history_fts5_rebuild_failed";
	data: { db_path: string; deleted: number; error: string; source: string };
}

/**
 * Pushed by `clipboard_target_safety/validation.py` (and the paste
 * manager) when a synthesized paste keystroke is dropped, e.g. the
 * target app has macOS Secure Input active. The transcribed text
 */
export interface PasteDeferredEvent {
	type: "paste_deferred";
	data: { reason: string; message?: string };
}

/**
 * Pushed by `tray.py::_drain_pending` when the native system-tray icon
 * is unavailable and queued tray notifications cannot be shown, the
 * renderer surfaces the fallback in-app banner instead (only the
 */
export interface TrayFallbackNotificationEvent {
	type: "tray_fallback_notification";
	data: { title?: string; message?: string };
}

export type PythonPushEvent =
	| StatusChangeEvent
	| ErrorEvent
	| TranscriptionFinalEvent
	| TranscriptionPartialEvent
	| RecordingStartedEvent
	| RecordingStoppedEvent
	| ConfigChangedEvent
	| HotkeyCaptureCancelEvent
	| HistoryChangedEvent
	| StateChangedEvent
	| PasteFailedEvent
	| DownloadProgressEvent
	| NotificationEvent
	| VocabularySuggestionEvent
	| MicrophonesChangedEvent
	| MicrophoneTestCompleteEvent
	| AudioClipEvent
	| TrayMenuEvent
	| NavigateEvent
	| ReadyEvent
	| BubbleShowEvent
	| BubbleHideEvent
	| BubbleSetStateEvent
	| BubbleLevelEvent
	| BubbleConfigEvent
	| ShowWindowEvent
	| QuitAppEvent
	| RelaunchAppEvent
	| TrayStateEvent
	| ConsentRequiredEvent
	| ParakeetCpuFallbackEvent
	// See the per-interface docstrings for the wire shape (all three
	| ASRBackendDisabledEvent
	| ASRLastResortUnloadedEvent
	| LLMPolishFailedEvent
	| TextEnhancementFailedEvent
	// recorder emitters). See `DeviceLostEvent` above for the wire shape.
	| DeviceLostEvent
	| ReconnectingEvent
	| ReconnectedEvent
	//coalesced mic-level push event (≤30 Hz).
	// See `MicLevelEvent` above for the wire shape + emitter.
	| MicLevelEvent
	// docstrings above for the wire shapes + emitters. The 13th
	// Pinned by `tests/test_event_types_parity.py`.
	| OfflinePackDownloadStartedEvent
	| OfflinePackDownloadProgressEvent
	| OfflinePackDownloadCompletedEvent
	| OfflinePackDownloadFailedEvent
	| OfflinePackVerifiedEvent
	| OfflinePackMissingEvent
	| OfflinePackCorruptEvent
	| OfflinePackReadyEvent
	| WorkerStartedEvent
	| WorkerCrashedEvent
	| WorkerUnloadedEvent
	| TranscribeOfflineResultEvent
	| MediaTranscribeProgressEvent
	| MediaTranscribeCompleteEvent
	| MediaTranscribeErrorEvent
	// (see the per-interface docstrings above for the emitters + wire
	// shapes; pinned by tests/test_event_types_parity.py).
	| AsrBackendReadyEvent
	| AsrBackendLoadFailedEvent
	| MicrophonePermissionRevokedEvent
	| MicrophoneDisconnectedEvent
	| CloudFallbackUsedEvent
	| DictationSuppressedEvent
	| HistoryCorruptedEvent
	| HistoryFts5RebuildFailedEvent
	| PasteDeferredEvent
	| TrayFallbackNotificationEvent;

// (`src-tauri/src/sidecar/ws.rs`) both pin the current constant
//   - Rust:   `src-tauri/src/sidecar/ws.rs:EXPECTED_PROTOCOL_VERSION`
export const IPC_PROTOCOL_VERSION = 1;

// The auth frame shape on the wire. The Rust host constructs this
// frame (`src-tauri/src/sidecar/ws.rs:queue_auth_and_store_ws_tx`);
// `protocol_version` is OPTIONAL: legacy senders that omit it
export interface AuthFrame {
	type: "auth";
	token: string;
	protocol_version?: number;
}

// Error envelope emitted on the version-mismatch path. Emitted by
export interface ProtocolVersionMismatchError {
	type: "error";
	data: {
		code: "server.protocol_version_mismatch";
		message: string;
		client_protocol_version: number;
		server_protocol_version: number;
	};
}
