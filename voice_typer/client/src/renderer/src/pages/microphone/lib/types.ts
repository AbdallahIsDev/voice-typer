// Shared types for the microphone page package.
// hook / component modules can reference a single canonical definition
// of the backend test-recording result envelope without re-declaring
// the backend dict shape).

/**
 * Reference to a WAV file persisted by the backend under
 * `<config>/mic-test-recordings/`. The bytes are fetched chunked via
 * ``microphone_test_read_audio``, a completed 10 s test's WAVs are
 * ~1 MB each, which exceeds the 1 MiB single-frame IPC cap when
 * base64-encoded, so they can never ride on the stop response itself.
 */
export interface TestAudioFileRef {
	path: string;
	bytes: number;
}

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

/** One slice of a ``microphone_test_read_audio`` response. */
export interface TestAudioChunk {
	success: boolean;
	data_b64: string;
	bytes_read: number;
	total_bytes: number;
	eof: boolean;
	message: string;
}

export interface TestStopResult {
	success: boolean;
	audio_file: TestAudioFileRef | null;
	raw_audio_file: TestAudioFileRef | null;
	duration_ms: number;
	sample_rate: number;
	message: string;
	quality: TestResultQuality;
	transcription?: string;
	/** True when the backend could not transcribe (e.g. no engine loaded). */
	transcription_unavailable?: boolean;
}
