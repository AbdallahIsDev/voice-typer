// Shared types for the microphone page package.
//
// Extracted from the former monolithic ``pages/Microphone.tsx`` so the
// hook / component modules can reference a single canonical definition
// of the backend test-recording result envelope without re-declaring
// it (which previously led to drift between the inline interface and
// the backend dict shape).

/**
 * Quality metrics returned by the backend after a microphone test
 * recording. Mirrors the dict returned from
 * ``level_monitor.stop_test_recording``.
 */
export interface TestResultQuality {
	volume_level: "good" | "low" | "very_low";
	volume_rms: number;
	peak_level: number;
	noise_level: "low" | "moderate" | "high";
	has_voice: boolean;
	has_clipping: boolean;
	detected_issues: string[];
	estimated_transcription_quality: number;
	silence_ratio: number;
}

/**
 * Full result envelope returned by the ``microphone_test_stop`` IPC
 * command. ``quality`` is optional in practice — the backend omits it
 * when the recording was too short to analyse — but the type keeps it
 * required so callers must null-check before reading its fields.
 */
export interface TestStopResult {
	success: boolean;
	audio_base64: string;
	raw_audio_base64: string;
	duration_ms: number;
	sample_rate: number;
	message: string;
	quality: TestResultQuality;
}
