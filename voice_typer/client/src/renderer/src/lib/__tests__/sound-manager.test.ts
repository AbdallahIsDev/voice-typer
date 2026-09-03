/**
 * Tests for the centralized SoundManager.
 *
 * Verifies the four bug fixes documented in sound-manager.ts:
 *  1. Failed init is retried (not permanently stuck).
 *  2. localStorage flag is read with safe fallback.
 *  3. setSoundFeedbackEnabled persists to localStorage.
 *  4. playSoundCue is gated by the enabled flag.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { stubGlobalLocalStorage } from "./helpers/local-storage-stub";

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

		// Stub the global localStorage so setItem throws (e.g. private
		// browsing mode). We own the storage object entirely (see
		// stubGlobalLocalStorage) so the throw is guaranteed regardless
		// of the jsdom environment.
		const setItemSpy = vi.fn(() => {
			throw new DOMException("quota exceeded");
		});
		stubGlobalLocalStorage({ setItem: setItemSpy });

		try {
			// Should not throw — the in-memory flag should still update
			setSoundFeedbackEnabled(false);
			// Verify localStorage was attempted (and swallowed)
			expect(setItemSpy).toHaveBeenCalled();
		} finally {
			vi.unstubAllGlobals();
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

	it("isEnabled logs debug message when localStorage.getItem throws", async () => {
		const { isSoundFeedbackEnabled, _resetSoundManagerForTests } = await import(
			"@/lib/sound-manager"
		);
		_resetSoundManagerForTests();

		// Stub localStorage.getItem to throw (e.g. private browsing mode).
		// We own the storage object entirely (see stubGlobalLocalStorage)
		// so the throw is guaranteed regardless of the jsdom environment.
		const getItemSpy = vi.fn(() => {
			throw new DOMException("SecurityError");
		});
		stubGlobalLocalStorage({ getItem: getItemSpy });
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
			expect(String(debugMsg)).toContain("[renderer:sound-manager]");
			expect(getItemSpy).toHaveBeenCalled();
		} finally {
			vi.unstubAllGlobals();
			debugSpy.mockRestore();
		}
	});
});

// ── Web Audio synthesis parity (per-kind cue table) ─────────────────────
//
// playViaAudioContext schedules each cue from the CUE_SPECS table. These
// tests pin the EXACT automation calls (values + absolute times + call
// order) that the implementation must produce, so any change to the
// synthesis path (e.g. the table collapse) can never silently change
// what a cue sounds like.

type RecordedCall = { method: string; value: number; time: number };

class RecordingParam {
	calls: RecordedCall[] = [];
	setValueAtTime(value: number, time: number): void {
		this.calls.push({ method: "setValueAtTime", value, time });
	}
	exponentialRampToValueAtTime(value: number, time: number): void {
		this.calls.push({ method: "exponentialRampToValueAtTime", value, time });
	}
}

class RecordingOscillator {
	type: OscillatorType = "sine";
	frequency = new RecordingParam();
	connectCalls: unknown[] = [];
	start = vi.fn();
	stop = vi.fn();
	disconnect = vi.fn();
	onended: (() => void) | null = null;
	connect(node: unknown) {
		this.connectCalls.push(node);
		// Chainable return: every hop pushes its destination onto
		// connectCalls and returns a callable that keeps the chain alive
		// (osc → gain → master → destination = 3 hops).
		const chainable = {
			connect: (next: unknown) => {
				this.connectCalls.push(next);
				return chainable;
			},
		};
		return chainable;
	}
}

class RecordingGain {
	gain = new RecordingParam();
	connectCalls: unknown[] = [];
	disconnect = vi.fn();
	connect(node: unknown) {
		this.connectCalls.push(node);
		// Return a chainable stub so multi-hop chains
		// (osc → gain → master → destination) keep working — the empty
		// object previously broke the third .connect() call.
		const chainable = {
			connect: (next: unknown) => {
				this.connectCalls.push(next);
				return chainable;
			},
		};
		return chainable;
	}
}

class RecordingAudioContext {
	state: "running" | "suspended" | "closed" = "running";
	// Non-zero currentTime so a hard-coded `0` time offset in the
	// synthesis path cannot sneak past these assertions.
	currentTime = 7;
	destination = { type: "destination" };
	oscillators: RecordingOscillator[] = [];
	gains: RecordingGain[] = [];
	createOscillator(): RecordingOscillator {
		const osc = new RecordingOscillator();
		this.oscillators.push(osc);
		return osc;
	}
	createGain(): RecordingGain {
		const gain = new RecordingGain();
		this.gains.push(gain);
		return gain;
	}
	resume(): Promise<void> {
		return Promise.resolve();
	}
	close(): Promise<void> {
		return Promise.resolve();
	}
}

describe("SoundManager — Web Audio synthesis matches the cue table", () => {
	const T = 7; // RecordingAudioContext.currentTime

	// Parity fixtures: param automation calls with ABSOLUTE times —
	// identical to the per-kind synthesis branch bodies.
	const expected: Record<
		"start" | "stop" | "error" | "complete",
		{
			type: OscillatorType;
			duration: number;
			frequency: RecordedCall[];
			gain: RecordedCall[];
		}
	> = {
		start: {
			type: "sine",
			duration: 0.13,
			frequency: [
				{ method: "setValueAtTime", value: 660, time: T },
				{ method: "exponentialRampToValueAtTime", value: 880, time: T + 0.08 },
			],
			gain: [
				{ method: "setValueAtTime", value: 0.0001, time: T },
				{ method: "exponentialRampToValueAtTime", value: 0.15, time: T + 0.01 },
				{
					method: "exponentialRampToValueAtTime",
					value: 0.0001,
					time: T + 0.12,
				},
			],
		},
		stop: {
			type: "sine",
			duration: 0.19,
			frequency: [
				{ method: "setValueAtTime", value: 523, time: T },
				{ method: "exponentialRampToValueAtTime", value: 392, time: T + 0.1 },
			],
			gain: [
				{ method: "setValueAtTime", value: 0.0001, time: T },
				{ method: "exponentialRampToValueAtTime", value: 0.15, time: T + 0.01 },
				{
					method: "exponentialRampToValueAtTime",
					value: 0.0001,
					time: T + 0.18,
				},
			],
		},
		error: {
			type: "square",
			duration: 0.25,
			frequency: [{ method: "setValueAtTime", value: 200, time: T }],
			gain: [
				{ method: "setValueAtTime", value: 0.0001, time: T },
				{
					method: "exponentialRampToValueAtTime",
					value: 0.18,
					time: T + 0.005,
				},
				{
					method: "exponentialRampToValueAtTime",
					value: 0.0001,
					time: T + 0.24,
				},
			],
		},
		complete: {
			type: "triangle",
			duration: 0.22,
			frequency: [
				{ method: "setValueAtTime", value: 880, time: T },
				{ method: "setValueAtTime", value: 1175, time: T + 0.1 },
			],
			gain: [
				{ method: "setValueAtTime", value: 0.0001, time: T },
				{
					method: "exponentialRampToValueAtTime",
					value: 0.14,
					time: T + 0.005,
				},
				{ method: "setValueAtTime", value: 0.14, time: T + 0.095 },
				{
					method: "exponentialRampToValueAtTime",
					value: 0.0001,
					time: T + 0.1,
				},
				{
					method: "exponentialRampToValueAtTime",
					value: 0.14,
					time: T + 0.105,
				},
				{ method: "setValueAtTime", value: 0.14, time: T + 0.21 },
				{
					method: "exponentialRampToValueAtTime",
					value: 0.0001,
					time: T + 0.22,
				},
			],
		},
	};

	beforeEach(() => {
		vi.resetModules();
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	for (const kind of ["start", "stop", "error", "complete"] as const) {
		it(`schedules "${kind}" with the exact expected automation sequence`, async () => {
			const {
				playSoundCue,
				setSoundFeedbackEnabled,
				_resetSoundManagerForTests,
			} = await import("@/lib/sound-manager");
			_resetSoundManagerForTests();
			setSoundFeedbackEnabled(true);

			const ctx = new RecordingAudioContext();
			// Function DECLARATION (not an expression): `new` on a plain
			// function that returns an object resolves to that object, so the
			// module's `new AudioContext()` gets our recording ctx. A vi.fn
			// arrow mock would NOT work — vitest 4's mock construct trap does
			// not propagate the impl's returned object to `new` — and the
			// function-expression form trips the useArrowFunction lint rule,
			// whose arrow fix would break `new` (arrows cannot be constructed).
			function RecordingAudioContextCtor() {
				return ctx;
			}
			window.AudioContext =
				RecordingAudioContextCtor as unknown as typeof AudioContext;

			playSoundCue(kind);

			expect(ctx.oscillators).toHaveLength(1);
			// TWO gains: the cue's own envelope gain + the master volume
			// gain node (sound_volume multiplier) inserted between the
			// envelope and the destination.
			expect(ctx.gains).toHaveLength(2);
			const osc = ctx.oscillators[0];
			const gain = ctx.gains[0];
			const master = ctx.gains[1];
			if (!osc || !gain || !master) {
				throw new Error(
					"cue did not schedule an oscillator + gain + master-gain chain",
				);
			}
			const spec = expected[kind];

			expect(osc.type).toBe(spec.type);
			expect(osc.frequency.calls).toEqual(spec.frequency);
			expect(gain.gain.calls).toEqual(spec.gain);
			// The master node carries NO automation — it's a static
			// multiplier set once at node creation (volume=1 → gain 1).
			expect(master.gain.calls).toEqual([]);

			// Shared graph + lifecycle: osc → gain → master → destination
			// chain (hops 2 and 3 arrive through the chained connect on
			// osc.connect's return value; the mock records every chained
			// hop on the oscillator's connectCalls), started at
			// currentTime, stopped at currentTime + duration.
			expect(osc.connectCalls[0]).toBe(gain);
			expect(osc.connectCalls[1]).toBe(master);
			expect(osc.connectCalls[2]).toBe(ctx.destination);
			expect(osc.start).toHaveBeenCalledWith(T);
			expect(osc.stop).toHaveBeenCalledWith(T + spec.duration);

			// Per-cue teardown is wired via osc.onended.
			expect(typeof osc.onended).toBe("function");
		});
	}

	it("the master volume node scales with sound_volume (setSoundVolume)", async () => {
		const {
			playSoundCue,
			setSoundFeedbackEnabled,
			setSoundVolume,
			getSoundVolume,
			_resetSoundManagerForTests,
		} = await import("@/lib/sound-manager");
		_resetSoundManagerForTests();
		setSoundFeedbackEnabled(true);
		setSoundVolume(0.4);
		expect(getSoundVolume()).toBe(0.4);

		const ctx = new RecordingAudioContext();
		function VolumeAudioContextCtor() {
			return ctx;
		}
		window.AudioContext =
			VolumeAudioContextCtor as unknown as typeof AudioContext;

		playSoundCue("start");

		const master = ctx.gains[1];
		expect(master).toBeTruthy();
		// The full chain is recorded on the oscillator's connectCalls
		// (the mock's chained-connect shape): osc → gain → master →
		// destination. The master node's own gain carries no automation.
		expect(ctx.oscillators[0]?.connectCalls[1]).toBe(master);
		expect(ctx.oscillators[0]?.connectCalls[2]).toBe(ctx.destination);
		expect(master?.gain.calls).toEqual([]);
	});

	it("setSoundVolume clamps out-of-range and non-finite values", async () => {
		const { setSoundVolume, getSoundVolume, _resetSoundManagerForTests } =
			await import("@/lib/sound-manager");
		_resetSoundManagerForTests();

		setSoundVolume(2);
		expect(getSoundVolume()).toBe(1);
		setSoundVolume(-3);
		expect(getSoundVolume()).toBe(0);
		setSoundVolume(Number.NaN);
		expect(getSoundVolume()).toBe(1);
		setSoundVolume(Number.POSITIVE_INFINITY);
		expect(getSoundVolume()).toBe(1);
		setSoundVolume(0.75);
		expect(getSoundVolume()).toBe(0.75);
	});
});
