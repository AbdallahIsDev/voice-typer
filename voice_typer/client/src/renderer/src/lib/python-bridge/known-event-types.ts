// Runtime mirror of the `PythonPushEvent["type"]` union
// declared in `types/ipc/push_events.ts`. TS can't enumerate union
// members at runtime, so we maintain this set by hand. The dev-time
// warning in `usePythonEvent` consults this set to surface typos like
// `usePythonEvent("past_failed", ...)` (intended `"paste_failed"`) in
// the dev console.
//
// KEEP IN SYNC with the `PythonPushEvent` union in
// `types/ipc/push_events.ts`. When a new event is added there, add
// its `type` literal here too. The dev-time warning will surface
// forgetfulness the first time a renderer subscribes to the new
// event (the warning fires for unknown types — including ones added
// to the TS union but not yet to this set).
//
// Exported so the parity test
// (`hooks/__tests__/usePython-known-event-types-parity.test.ts`) can
// assert the runtime set matches the compile-time `PythonPushEvent["type"]`
// union. Not part of the public hook API — only consumed by tests.
export const KNOWN_EVENT_TYPES: ReadonlySet<string> = new Set([
	"status_change",
	"error",
	"transcription_final",
	"recording_started",
	"recording_stopped",
	"config_changed",
	"hotkey_capture_cancel",
	"history_changed",
	"state_changed",
	"paste_failed",
	"download_progress",
	"notification",
	"vocabulary_suggestion",
	"microphones_changed",
	"microphone_test_complete",
	"audio_clip",
	"tray_menu",
	"navigate",
	"ready",
	"bubble_show",
	"bubble_hide",
	"bubble_set_state",
	"bubble_level",
	"bubble_config",
	"show_window",
	"quit_app",
	"relaunch_app",
	"tray_state",
	"consent_required",
	"device_lost",
	"parakeet_cpu_fallback",
	"asr_backend_disabled",
	"asr_last_resort_unloaded",
	"llm_polish_failed",
	"reconnecting",
	"reconnected",
	// the new mic_level push event (coalesced at
	// ≤30 Hz by the same level_monitor worker that publishes
	// `bubble_level`). Subscribed to by
	// `pages/microphone/hooks/useMicrophoneTest.ts` instead of
	// the legacy 10 Hz `microphone_test_get_level` IPC poll.
	"mic_level",
	// Live partial-transcription text pushed mid-recording by the
	// hidden streaming session's coalescing broadcaster (≤4 Hz,
	// latest-value-wins). Rendered by the bubble via the mirrored
	// `bubble_set_state` transcript field; this event stays on the
	// push-event surface for main-window consumers.
	"transcription_partial",
	// ── Pack + worker IPC events (master plan §7.4 — 12 push
	// events from the slim-core / runtime-pack split). Each is
	// published by `event_bus.publish(...)` in the Python sidecar
	// (the worker→slim-core hop forwards each as a standard event-
	// bus publish). The 13th §7.4 event — `transcribe_offline` —
	// is a REQUEST (renderer → slim core → worker), so it lives
	// in `PythonRequest` (`types/ipc/requests.ts`), NOT here.
	// Pinned by `tests/test_event_types_parity.py`.
	//
	// Pack download lifecycle (silent progress + visible started/
	// completed/failed):
	"offline_pack_download_started",
	"offline_pack_download_progress",
	"offline_pack_download_completed",
	"offline_pack_download_failed",
	// Pack integrity state:
	"offline_pack_verified",
	"offline_pack_missing",
	"offline_pack_corrupt",
	"offline_pack_ready",
	// Worker process lifecycle:
	"worker_started",
	"worker_crashed",
	"worker_unloaded",
	// Offline transcription result (worker → slim core → renderer).
	// The request counterpart `transcribe_offline` is a command,
	// NOT a push event — see `PythonRequest`.
	"transcribe_offline_result",
]);
