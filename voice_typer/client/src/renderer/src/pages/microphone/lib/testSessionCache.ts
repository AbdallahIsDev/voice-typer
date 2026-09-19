import type { TestResultQuality } from "./types";

let _cachedTestAudioBase64: string | null = null;
let _cachedRawAudioBase64: string | null = null;
let _cachedTestQuality: TestResultQuality | null = null;
let _cachedTestDurationMs = 0;
let _cachedTestTranscription: string | null = null;
let _cachedTestTranscriptionUnavailable = false;

/** Snapshot of the cached test session, for seeding React state. */
export interface CachedTestSession {
	audioBase64: string | null;
	rawAudioBase64: string | null;
	quality: TestResultQuality | null;
	durationMs: number;
	transcription: string | null;
	transcriptionUnavailable: boolean;
}

export function readTestSessionCache(): CachedTestSession {
	return {
		audioBase64: _cachedTestAudioBase64,
		rawAudioBase64: _cachedRawAudioBase64,
		quality: _cachedTestQuality,
		durationMs: _cachedTestDurationMs,
		transcription: _cachedTestTranscription,
		transcriptionUnavailable: _cachedTestTranscriptionUnavailable,
	};
}

export function writeTestSessionCache(session: CachedTestSession): void {
	_cachedTestAudioBase64 = session.audioBase64;
	_cachedRawAudioBase64 = session.rawAudioBase64;
	_cachedTestQuality = session.quality;
	_cachedTestDurationMs = session.durationMs;
	_cachedTestTranscription = session.transcription;
	_cachedTestTranscriptionUnavailable = session.transcriptionUnavailable;
}

export function _resetMicrophoneTestCache(): void {
	_cachedTestAudioBase64 = null;
	_cachedRawAudioBase64 = null;
	_cachedTestQuality = null;
	_cachedTestDurationMs = 0;
	_cachedTestTranscription = null;
	_cachedTestTranscriptionUnavailable = false;
}
