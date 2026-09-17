import type { TestResultQuality } from "./types";

/**
 * Module-level cache for the last-test recording + quality
 * verdict, mirrors the ``_cachedMicrophones`` / ``_cachedConfig``
 * pattern in ``useMicrophoneData``. Persists across page navigations so
 * a user who runs a mic test, navigates to the Models page to download
 * a model, then returns to the Microphone page sees their previous
 * test's recording + verdict WITHOUT having to re-run the test (which
 * would otherwise be lost, the test audio + quality were React-state
 * only, cleared on unmount). The cache is invalidated whenever:
 *   • ``startTest`` runs (a new test supersedes the old one), or
 *   • ``selectMicrophone`` picks a different mic (the cached recording
 *     was for a DIFFERENT mic, keeping it would be misleading A/B
 *     comparison material).
 */
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

/** Read the current cache contents (used to seed per-test state). */
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

/**
 * Mirror a freshly-captured test recording into the module-level
 * cache so a page navigation does NOT discard it. Mirrors the
 * ``_cachedConfig`` write-through pattern in useMicrophoneData.
 */
export function writeTestSessionCache(session: CachedTestSession): void {
	_cachedTestAudioBase64 = session.audioBase64;
	_cachedRawAudioBase64 = session.rawAudioBase64;
	_cachedTestQuality = session.quality;
	_cachedTestDurationMs = session.durationMs;
	_cachedTestTranscription = session.transcription;
	_cachedTestTranscriptionUnavailable = session.transcriptionUnavailable;
}

/**
 * Reset the module-level test cache. The cached recordings are tied
 * to the PREVIOUS mic + filter config, keeping them across a mic
 * switch would let the user "play" the wrong recording against the
 * wrong mic (mismatched A/B).
 */
export function _resetMicrophoneTestCache(): void {
	_cachedTestAudioBase64 = null;
	_cachedRawAudioBase64 = null;
	_cachedTestQuality = null;
	_cachedTestDurationMs = 0;
	_cachedTestTranscription = null;
	_cachedTestTranscriptionUnavailable = false;
}
