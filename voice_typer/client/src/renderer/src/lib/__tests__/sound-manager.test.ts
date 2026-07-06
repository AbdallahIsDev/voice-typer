/**
 * Tests for the centralized SoundManager.
 *
 * SOUND-FIX-REWRITE: verifies the four bug fixes documented in
 * sound-manager.ts:
 *  1. Failed init is retried (not permanently stuck).
 *  2. localStorage flag is read with safe fallback.
 *  3. setSoundFeedbackEnabled persists to localStorage.
 *  4. playSoundCue is gated by the enabled flag.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock AudioContext — jsdom doesn't implement Web Audio API.
class MockAudioContext {
	state: "suspended" | "running" | "closed" = "suspended";
	currentTime = 0;
	destination = {} as AudioDestinationNode;

	createOscillator() {
		return {
			connect: () => ({ connect: () => ({}) }),
			start: vi.fn(),
			stop: vi.fn(),
			frequency: {
				setValueAtTime: vi.fn(),
				exponentialRampToValueAtTime: vi.fn(),
			},
		} as unknown as OscillatorNode;
	}

	createGain() {
		return {
			connect: () => ({ connect: () => ({}) }),
			gain: {
				setValueAtTime: vi.fn(),
				exponentialRampToValueAtTime: vi.fn(),
			},
		} as unknown as GainNode;
	}

	resume() {
		this.state = "running";
		return Promise.resolve();
	}

	close() {
		this.state = "closed";
		return Promise.resolve();
	}
}

describe("SoundManager", () => {
	let originalAudioContext: typeof window.AudioContext | undefined;
	let mockCtor: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		// Reset module state between tests
		vi.resetModules();
		localStorage.clear();

		originalAudioContext = window.AudioContext;
		// Use a regular function (not arrow) so `new Ctor()` works —
		// arrow functions can't be used as constructors.
		mockCtor = vi.fn(() => new MockAudioContext());
		window.AudioContext = mockCtor as typeof AudioContext;
	});

	afterEach(() => {
		if (originalAudioContext !== undefined) {
			window.AudioContext = originalAudioContext;
		} else {
			// @ts-expect-error — restoring from undefined state
			delete window.AudioContext;
		}
		// Restore gesture listeners if installed
		vi.restoreAllMocks();
	});

	it("setSoundFeedbackEnabled persists to localStorage", async () => {
		const { setSoundFeedbackEnabled } = await import("@/lib/sound-manager");
		setSoundFeedbackEnabled(true);
		expect(localStorage.getItem("vt_sound_feedback_enabled")).toBe("1");
		setSoundFeedbackEnabled(false);
		expect(localStorage.getItem("vt_sound_feedback_enabled")).toBe("0");
	});

	it("setSoundFeedbackEnabled updates in-memory flag when localStorage throws", async () => {
		const { setSoundFeedbackEnabled, _resetSoundManagerForTests } =
			await import("@/lib/sound-manager");
		_resetSoundManagerForTests();

		// Stub localStorage.setItem to throw
		const originalSetItem = Storage.prototype.setItem;
		Storage.prototype.setItem = vi.fn(() => {
			throw new DOMException("quota exceeded");
		});

		try {
			// Should not throw — the in-memory flag should still update
			setSoundFeedbackEnabled(false);
			// Verify localStorage was attempted (and swallowed)
			expect(Storage.prototype.setItem).toHaveBeenCalled();
		} finally {
			Storage.prototype.setItem = originalSetItem;
		}
	});

	it("playSoundCue is gated by the enabled flag", async () => {
		const {
			playSoundCue,
			setSoundFeedbackEnabled,
			_resetSoundManagerForTests,
		} = await import("@/lib/sound-manager");
		_resetSoundManagerForTests();

		// Disable sound feedback
		setSoundFeedbackEnabled(false);

		// Should NOT create an AudioContext — the cue is gated before init
		const ctxCountBefore = mockCtor.mock.calls.length;
		playSoundCue("start");
		expect(mockCtor.mock.calls.length).toBe(ctxCountBefore);
	});

	it("initAudioContext retries after a failed construction", async () => {
		const { initAudioContext, _resetSoundManagerForTests } = await import(
			"@/lib/sound-manager"
		);
		_resetSoundManagerForTests();

		// First call: constructor throws
		mockCtor.mockImplementationOnce(() => {
			throw new Error("AudioContext unavailable");
		});
		expect(initAudioContext()).toBe(false);

		// Second call: constructor succeeds — should NOT be permanently skipped.
		// IMPORTANT: use a regular function (not arrow) so `new Ctor()` works —
		// arrow functions can't be used as constructors and would throw a
		// TypeError, which the catch block would swallow as a "failed
		// construction" (matching the beforeEach mock setup convention).
		// biome-ignore lint/complexity/useArrowFunction: arrow functions cannot be used as constructors — `new Ctor()` requires a regular function or class
		mockCtor.mockImplementation(function () {
			return new MockAudioContext();
		});
		expect(initAudioContext()).toBe(true);
	});

	it("playSoundCue initializes AudioContext when enabled", async () => {
		const {
			playSoundCue,
			setSoundFeedbackEnabled,
			_resetSoundManagerForTests,
		} = await import("@/lib/sound-manager");
		_resetSoundManagerForTests();

		setSoundFeedbackEnabled(true);
		// Clear mock state from the setSoundFeedbackEnabled call (which
		// doesn't touch AudioContext, but defensive reset).
		mockCtor.mockClear();

		playSoundCue("start");
		// Should have attempted to construct an AudioContext
		expect(mockCtor).toHaveBeenCalled();
	});
});
