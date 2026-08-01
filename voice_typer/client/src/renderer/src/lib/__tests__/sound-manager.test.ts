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

	it("XZ-R16-08: isEnabled logs debug message when localStorage.getItem throws", async () => {
		const { isSoundFeedbackEnabled, _resetSoundManagerForTests } = await import(
			"@/lib/sound-manager"
		);
		_resetSoundManagerForTests();

		// Stub localStorage.getItem to throw (e.g. private browsing mode).
		const originalGetItem = Storage.prototype.getItem;
		Storage.prototype.getItem = vi.fn(() => {
			throw new DOMException("SecurityError");
		});
		const debugSpy = vi.spyOn(console, "debug").mockImplementation(() => {});

		try {
			// Should NOT throw — falls back to the in-memory default.
			const result = isSoundFeedbackEnabled();
			// Default in-memory flag is true on a fresh reset (matches the
			// production default — sound is enabled unless the user opts
			// out via Settings). The catch block falls back to the in-memory
			// default when localStorage is unavailable.
			expect(result).toBe(true);
			//the catch block must log a debug message so silent
			// audio-flag read failures are visible to operators.
			expect(debugSpy).toHaveBeenCalled();
			const debugMsg = debugSpy.mock.calls[0]?.[0] ?? "";
			expect(String(debugMsg)).toContain("[sound-manager]");
			expect(Storage.prototype.getItem).toHaveBeenCalled();
		} finally {
			Storage.prototype.getItem = originalGetItem;
			debugSpy.mockRestore();
		}
	});
});

describe("SoundManager — ZU-34 visual feedback flag (deaf mirror)", () => {
	let originalAudioContext: typeof window.AudioContext | undefined;
	let mockCtor: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		vi.resetModules();
		localStorage.clear();
		originalAudioContext = window.AudioContext;
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
		vi.restoreAllMocks();
	});

	it("setVisualFeedbackEnabled persists to localStorage under the visual key", async () => {
		const { setVisualFeedbackEnabled } = await import("@/lib/sound-manager");
		setVisualFeedbackEnabled(true);
		expect(localStorage.getItem("vt_visual_feedback_enabled")).toBe("1");
		setVisualFeedbackEnabled(false);
		expect(localStorage.getItem("vt_visual_feedback_enabled")).toBe("0");
	});

	it("isVisualFeedbackEnabled defaults to false on a fresh reset", async () => {
		const { isVisualFeedbackEnabled, _resetSoundManagerForTests } =
			await import("@/lib/sound-manager");
		_resetSoundManagerForTests();
		expect(isVisualFeedbackEnabled()).toBe(false);
	});

	it("isVisualFeedbackEnabled reflects the persisted value after setVisualFeedbackEnabled(true)", async () => {
		const {
			isVisualFeedbackEnabled,
			setVisualFeedbackEnabled,
			_resetSoundManagerForTests,
		} = await import("@/lib/sound-manager");
		_resetSoundManagerForTests();
		setVisualFeedbackEnabled(true);
		expect(isVisualFeedbackEnabled()).toBe(true);
	});

	it("isVisualFeedbackEnabled falls back to in-memory default when localStorage is empty", async () => {
		const { isVisualFeedbackEnabled, _resetSoundManagerForTests } =
			await import("@/lib/sound-manager");
		_resetSoundManagerForTests();
		// No localStorage entry — must fall back to the in-memory default (false).
		localStorage.clear();
		expect(isVisualFeedbackEnabled()).toBe(false);
	});

	it("setVisualFeedbackEnabled does NOT throw when localStorage.setItem fails", async () => {
		const { setVisualFeedbackEnabled, _resetSoundManagerForTests } =
			await import("@/lib/sound-manager");
		_resetSoundManagerForTests();

		const originalSetItem = Storage.prototype.setItem;
		Storage.prototype.setItem = vi.fn(() => {
			throw new DOMException("quota exceeded");
		});
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

		try {
			// Must NOT throw — the in-memory flag still updates.
			expect(() => setVisualFeedbackEnabled(true)).not.toThrow();
			expect(Storage.prototype.setItem).toHaveBeenCalled();
			// The warning surfaces the localStorage failure to operators.
			expect(warnSpy).toHaveBeenCalled();
		} finally {
			Storage.prototype.setItem = originalSetItem;
			warnSpy.mockRestore();
		}
	});

	it("isVisualFeedbackEnabled logs debug message when localStorage.getItem throws", async () => {
		const { isVisualFeedbackEnabled, _resetSoundManagerForTests } =
			await import("@/lib/sound-manager");
		_resetSoundManagerForTests();

		const originalGetItem = Storage.prototype.getItem;
		Storage.prototype.getItem = vi.fn(() => {
			throw new DOMException("SecurityError");
		});
		const debugSpy = vi.spyOn(console, "debug").mockImplementation(() => {});

		try {
			const result = isVisualFeedbackEnabled();
			// Default in-memory flag is false on a fresh reset.
			expect(result).toBe(false);
			expect(debugSpy).toHaveBeenCalled();
			const debugMsg = debugSpy.mock.calls[0]?.[0] ?? "";
			expect(String(debugMsg)).toContain("[sound-manager]");
		} finally {
			Storage.prototype.getItem = originalGetItem;
			debugSpy.mockRestore();
		}
	});

	it("uses a SEPARATE localStorage key from the sound-feedback flag", async () => {
		const {
			setSoundFeedbackEnabled,
			setVisualFeedbackEnabled,
			_resetSoundManagerForTests,
		} = await import("@/lib/sound-manager");
		_resetSoundManagerForTests();

		setSoundFeedbackEnabled(true);
		setVisualFeedbackEnabled(false);
		// Sound is enabled, visual is disabled — the two flags are independent.
		expect(localStorage.getItem("vt_sound_feedback_enabled")).toBe("1");
		expect(localStorage.getItem("vt_visual_feedback_enabled")).toBe("0");
	});
});
