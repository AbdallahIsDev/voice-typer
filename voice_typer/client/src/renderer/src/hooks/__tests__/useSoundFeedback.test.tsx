/**
 * Tests for the useSoundFeedback hook.
 *
 * RW-10 (sound consolidation): verifies that the hook delegates cue
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
