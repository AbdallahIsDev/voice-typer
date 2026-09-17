/**
 * Unit tests for the module-level last-test session cache.
 *
 * The cache survives Microphone-page unmount so navigating away and
 * back does not discard a just-completed test recording + verdict.
 * Start / mic-switch paths must clear it (wrong-mic A/B material).
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
	_resetMicrophoneTestCache,
	type CachedTestSession,
	readTestSessionCache,
	writeTestSessionCache,
} from "@/pages/microphone/lib/testSessionCache";
import type { TestResultQuality } from "@/pages/microphone/lib/types";

const EMPTY: CachedTestSession = {
	audioBase64: null,
	rawAudioBase64: null,
	quality: null,
	durationMs: 0,
	transcription: null,
	transcriptionUnavailable: false,
};

function makeQuality(): TestResultQuality {
	return {
		volume_level: "good",
		volume_rms: 0.2,
		peak_level: 0.8,
		noise_level: "low",
		has_voice: true,
		has_clipping: false,
		detected_issues: [],
		estimated_transcription_quality: 0.9,
		silence_ratio: 0.1,
	};
}

function makeSession(
	overrides: Partial<CachedTestSession> = {},
): CachedTestSession {
	return {
		audioBase64: "YXVkaW8=",
		rawAudioBase64: "cmF3",
		quality: makeQuality(),
		durationMs: 10_000,
		transcription: "hello world",
		transcriptionUnavailable: false,
		...overrides,
	};
}

beforeEach(() => {
	_resetMicrophoneTestCache();
});

afterEach(() => {
	_resetMicrophoneTestCache();
});

describe("readTestSessionCache", () => {
	it("returns the empty snapshot before any write", () => {
		expect(readTestSessionCache()).toEqual(EMPTY);
	});
});

describe("writeTestSessionCache / readTestSessionCache", () => {
	it("round-trips a completed test session", () => {
		const session = makeSession();
		writeTestSessionCache(session);
		expect(readTestSessionCache()).toEqual(session);
	});

	it("records transcription-unavailable without fabricating text", () => {
		const session = makeSession({
			transcription: null,
			transcriptionUnavailable: true,
			quality: null,
		});
		writeTestSessionCache(session);
		expect(readTestSessionCache()).toEqual(session);
	});
});

describe("_resetMicrophoneTestCache", () => {
	it("restores the empty snapshot after a write", () => {
		writeTestSessionCache(makeSession());
		_resetMicrophoneTestCache();
		expect(readTestSessionCache()).toEqual(EMPTY);
	});
});
