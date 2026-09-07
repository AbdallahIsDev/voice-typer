/**
 * Tests for the useSoundFeedback hook.
 *
 *  (sound consolidation): verifies that the hook delegates cue
 * playback to the canonical implementation in ``@/lib/sound-manager`` —
 * NOT to a parallel implementation inside the hook file. This is the
 * regression guard that prevents the dead-code duplication from
 * sneaking back in.
 */
import { act, cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mocks ────────────────────────────────────────────────────────────
// We mock the sound-manager module so we can assert the hook calls the
// canonical playSoundCue / initAudioContext — not a local duplicate.
vi.mock("@/lib/sound-manager", () => ({
	initAudioContext: vi.fn((): boolean => true),
	playSoundCue: vi.fn((): void => {}),
	// Re-export the reset helper so any other code that imports it via
	// the hook's transitive import graph doesn't blow up.
	_resetSoundManagerForTests: vi.fn((): void => {}),
	setSoundFeedbackEnabled: vi.fn((): void => {}),
	// The hook's mount effect calls ``isSoundFeedbackEnabled`` to gate
	//``initAudioContext`` () and ``closeAudioContext`` on cleanup.
	// Without these in the mock, vitest 4.x throws "No export defined"
	// — older vitest versions silently treated missing exports as
	// ``undefined`` and the call would no-op. Vitest 4 surfaces the
	// missing export as a hard error so the mock must list every
	// imported symbol.
	isSoundFeedbackEnabled: vi.fn((): boolean => true),
	closeAudioContext: vi.fn((): void => {}),
}));

// Capture every onEvent callback registered by usePythonEvent so the
// test can dispatch synthetic recording_started / recording_stopped
// events to ALL subscribers. (The hook calls usePythonEvent twice, so
// there are two subscriptions, each with its own type filter.)
type EventCallback = (event: {
	type: string;
	data?: Record<string, unknown>;
}) => void;

let capturedEventCallbacks: EventCallback[] = [];
const unsubscribeSpy = vi.fn();

beforeEach(() => {
	capturedEventCallbacks = [];
	unsubscribeSpy.mockClear();
	// Clear any call history on the mocked sound-manager fns so each
	// test starts with a clean slate. (Module mocks persist across
	// tests in the same file — vi.restoreAllMocks in afterEach restores
	// spies but does NOT reset module-level mock fns.)
	vi.clearAllMocks();
	// Install a minimal window.python bridge.
	(window as unknown as { python: unknown }).python = {
		call: vi.fn().mockResolvedValue({}),
		onEvent: vi.fn((cb: EventCallback) => {
			capturedEventCallbacks.push(cb);
			return unsubscribeSpy;
		}),
	};
});

afterEach(() => {
	cleanup();
	vi.restoreAllMocks();
	delete (window as unknown as { python?: unknown }).python;
});

// Helper: render a component that mounts the hook.
async function renderWithHook() {
	const { useSoundFeedback } = await import("@/hooks/useSoundFeedback");
	function Probe() {
		useSoundFeedback();
		return null as unknown as ReactNode;
	}
	const result = render(<Probe />);
	return result;
}

// Helper: render the hook with an onVisualCue callback and return
// the captured callback so the test can assert on invocations.
async function renderWithOnVisualCue(onVisualCue: (cue: string) => void) {
	const { useSoundFeedback } = await import("@/hooks/useSoundFeedback");
	function Probe() {
		useSoundFeedback({
			onVisualCue: onVisualCue as (
				cue: "start" | "stop" | "complete" | "error",
			) => void,
		});
		return null as unknown as ReactNode;
	}
	const result = render(<Probe />);
	return result;
}

describe("useSoundFeedback", () => {
	it("calls initAudioContext on mount (delegates to sound-manager)", async () => {
		const { initAudioContext } = await import("@/lib/sound-manager");
		await renderWithHook();
		expect(initAudioContext).toHaveBeenCalledTimes(1);
	});

	it("plays 'start' cue on recording_started event (delegates to sound-manager)", async () => {
		const { playSoundCue } = await import("@/lib/sound-manager");
		await renderWithHook();

		expect(capturedEventCallbacks.length).toBeGreaterThan(0);
		act(() => {
			for (const cb of capturedEventCallbacks)
				cb({ type: "recording_started" });
		});
		expect(playSoundCue).toHaveBeenCalledWith("start");
	});

	it("plays 'stop' cue on recording_stopped event (delegates to sound-manager)", async () => {
		const { playSoundCue } = await import("@/lib/sound-manager");
		await renderWithHook();

		expect(capturedEventCallbacks.length).toBeGreaterThan(0);
		act(() => {
			for (const cb of capturedEventCallbacks)
				cb({ type: "recording_stopped" });
		});
		expect(playSoundCue).toHaveBeenCalledWith("stop");
	});

	it("does NOT play a cue for unrelated events", async () => {
		const { playSoundCue } = await import("@/lib/sound-manager");
		await renderWithHook();

		act(() => {
			for (const cb of capturedEventCallbacks) {
				cb({ type: "config_changed" });
				cb({ type: "audio_level" });
			}
		});
		expect(playSoundCue).not.toHaveBeenCalled();
	});

	it("re-exports the canonical playSoundCue / initAudioContext for backward compat", async () => {
		const hookModule = await import("@/hooks/useSoundFeedback");
		const soundManager = await import("@/lib/sound-manager");
		// The hook module re-exports the SAME function references from
		// sound-manager. This assertion catches regressions where someone
		// re-introduces a parallel implementation inside useSoundFeedback.
		expect(hookModule.playSoundCue).toBe(soundManager.playSoundCue);
		expect(hookModule.initAudioContext).toBe(soundManager.initAudioContext);
	});
});

describe("useSoundFeedback — runtime sound_feedback_enabled toggle (config_changed)", () => {
	it("closes the AudioContext when config_changed disables sound feedback", async () => {
		const { closeAudioContext, initAudioContext } = await import(
			"@/lib/sound-manager"
		);
		await renderWithHook();

		act(() => {
			for (const cb of capturedEventCallbacks)
				cb({ type: "config_changed", data: { sound_feedback_enabled: false } });
		});
		expect(closeAudioContext).toHaveBeenCalledTimes(1);
		// No redundant re-init on the disable path.
		expect(initAudioContext).toHaveBeenCalledTimes(1); // mount only
	});

	it("re-inits the AudioContext when config_changed enables sound feedback", async () => {
		const { initAudioContext, closeAudioContext } = await import(
			"@/lib/sound-manager"
		);
		await renderWithHook();

		act(() => {
			for (const cb of capturedEventCallbacks)
				cb({ type: "config_changed", data: { sound_feedback_enabled: true } });
		});
		expect(initAudioContext).toHaveBeenCalledTimes(2); // mount + flip
		expect(closeAudioContext).not.toHaveBeenCalled();
	});

	it("ignores config_changed pushes that don't carry sound_feedback_enabled", async () => {
		const { closeAudioContext, initAudioContext } = await import(
			"@/lib/sound-manager"
		);
		await renderWithHook();

		act(() => {
			for (const cb of capturedEventCallbacks) {
				cb({ type: "config_changed", data: { theme_mode: "dark" } });
				cb({ type: "config_changed" });
				cb({ type: "audio_level", data: { level: 0.5 } });
			}
		});
		expect(initAudioContext).toHaveBeenCalledTimes(1); // mount only
		expect(closeAudioContext).not.toHaveBeenCalled();
	});

	it("closes then re-inits across an off→on toggle sequence", async () => {
		const { closeAudioContext, initAudioContext } = await import(
			"@/lib/sound-manager"
		);
		await renderWithHook();

		act(() => {
			for (const cb of capturedEventCallbacks)
				cb({ type: "config_changed", data: { sound_feedback_enabled: false } });
		});
		act(() => {
			for (const cb of capturedEventCallbacks)
				cb({ type: "config_changed", data: { sound_feedback_enabled: true } });
		});
		expect(closeAudioContext).toHaveBeenCalledTimes(1);
		expect(initAudioContext).toHaveBeenCalledTimes(2);
	});
});

describe("useSoundFeedback — ZU-34 onVisualCue callback (deaf mirror)", () => {
	it("invokes onVisualCue('start') on recording_started", async () => {
		const { playSoundCue } = await import("@/lib/sound-manager");
		const onVisualCue = vi.fn();
		await renderWithOnVisualCue(onVisualCue);

		expect(capturedEventCallbacks.length).toBeGreaterThan(0);
		act(() => {
			for (const cb of capturedEventCallbacks)
				cb({ type: "recording_started" });
		});
		expect(playSoundCue).toHaveBeenCalledWith("start");
		expect(onVisualCue).toHaveBeenCalledTimes(1);
		expect(onVisualCue).toHaveBeenCalledWith("start");
	});

	it("invokes onVisualCue('stop') on recording_stopped", async () => {
		const onVisualCue = vi.fn();
		await renderWithOnVisualCue(onVisualCue);

		act(() => {
			for (const cb of capturedEventCallbacks)
				cb({ type: "recording_stopped" });
		});
		expect(onVisualCue).toHaveBeenCalledTimes(1);
		expect(onVisualCue).toHaveBeenCalledWith("stop");
	});

	it("invokes onVisualCue('complete') on transcription_final", async () => {
		const onVisualCue = vi.fn();
		await renderWithOnVisualCue(onVisualCue);

		act(() => {
			for (const cb of capturedEventCallbacks)
				cb({ type: "transcription_final" });
		});
		expect(onVisualCue).toHaveBeenCalledTimes(1);
		expect(onVisualCue).toHaveBeenCalledWith("complete");
	});

	it("invokes onVisualCue('error') on error event", async () => {
		const onVisualCue = vi.fn();
		await renderWithOnVisualCue(onVisualCue);

		act(() => {
			for (const cb of capturedEventCallbacks) cb({ type: "error" });
		});
		expect(onVisualCue).toHaveBeenCalledTimes(1);
		expect(onVisualCue).toHaveBeenCalledWith("error");
	});

	it("does NOT invoke onVisualCue for unrelated events", async () => {
		const onVisualCue = vi.fn();
		await renderWithOnVisualCue(onVisualCue);

		act(() => {
			for (const cb of capturedEventCallbacks) {
				cb({ type: "config_changed" });
				cb({ type: "audio_level" });
			}
		});
		expect(onVisualCue).not.toHaveBeenCalled();
	});

	it("does NOT fire onVisualCue when the callback is not provided (backwards compat)", async () => {
		// Render without any options — the hook should still play the
		// sound cue but never crash trying to call an undefined callback.
		const { playSoundCue } = await import("@/lib/sound-manager");
		await renderWithHook();

		expect(() => {
			act(() => {
				for (const cb of capturedEventCallbacks)
					cb({ type: "recording_started" });
			});
		}).not.toThrow();
		expect(playSoundCue).toHaveBeenCalledWith("start");
	});

	it("invokes onVisualCue AFTER playSoundCue (audio scheduled first)", async () => {
		const { playSoundCue } = await import("@/lib/sound-manager");
		// Cast to MockedFunction so we can access the mock-control methods.
		// The factory in vi.mock types playSoundCue as a plain function —
		// vitest's vi.fn returns a Mock but the TS inference doesn't carry
		// the Mock surface across the dynamic import.
		const mocked = playSoundCue as unknown as ReturnType<typeof vi.fn>;
		const callOrder: string[] = [];
		mocked.mockImplementation((cue: string) => {
			callOrder.push(`play:${cue}`);
		});
		const onVisualCue = vi.fn((cue: string) => {
			callOrder.push(`visual:${cue}`);
		});

		try {
			await renderWithOnVisualCue(onVisualCue);
			act(() => {
				for (const cb of capturedEventCallbacks)
					cb({ type: "recording_started" });
			});
			// Audio must be scheduled BEFORE the visual callback fires so
			// the perceived AV skew is minimised.
			expect(callOrder).toEqual(["play:start", "visual:start"]);
		} finally {
			// Restore by clearing the implementation; vitest's vi.mock
			// factory resets on the next test via vi.clearAllMocks in
			// beforeEach, so we don't need to restore the exact prior impl.
			mocked.mockReset();
			mocked.mockImplementation(() => {});
		}
	});
});
