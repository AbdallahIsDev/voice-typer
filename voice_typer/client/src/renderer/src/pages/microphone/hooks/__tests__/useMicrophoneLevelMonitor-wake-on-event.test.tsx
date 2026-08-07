/**
 * Regression tests: `useMicrophoneLevelMonitor` rAF loop pauses
 * on idle (no `mic_level` events for 500ms) and re-arms via `wake()`.
 *
 * Background
 * ----------
 * Before the fix: the rAF callback unconditionally re-scheduled the next
 * frame on EVERY gate-closed branch (hidden / not monitoring /
 * playing) "so the loop can react to gate flips without a remount".
 * When the Microphone page was mounted but the user wasn't actively
 * testing / monitoring (the common case — user navigates to the page
 * and reads / scrolls), the loop ticked at ~60 Hz doing 3 ref reads +
 * visibility check + a no-op reschedule, keeping the renderer's
 * compositing thread awake on battery-constrained laptops.
 *
 * After the fix: the loop adopts the bubble's `useAudioLevels`
 * wake-on-event pattern (`useAudioLevels.ts:286-305`):
 *   - The `mic_level` push handler updates `lastLevelEventAtRef.current`
 *     and calls `wakeRef.current?.()` on every event.
 *   - The rAF callback checks `performance.now() - lastLevelEventAtRef.current
 *     > IDLE_TIMEOUT_MS` (500ms). If idle, it returns WITHOUT
 *     scheduling the next frame — the loop pauses.
 *   - The next `mic_level` event re-arms via `wake()`.
 *
 * These tests verify:
 *   1. After mount, the loop starts (initial wake) — `requestAnimationFrame`
 *      is called once.
 *   2. When `performance.now()` returns a time > 500ms after the last
 *      `mic_level` event, the loop pauses — `requestAnimationFrame` is
 *      NOT called again on the next tick.
 *   3. A `mic_level` event re-arms the loop — `requestAnimationFrame`
 *      is called again after the event.
 *
 * The test renders a Probe that mounts `useMicrophoneLevelMonitor`
 * with stubbed refs + a `meterRef` pointing at a real DOM tree
 * (`<div><div role="progressbar"><div /></div></div>`) so the
 * selector-based DOM write path is exercised. `requestAnimationFrame`
 * is spied on so we can count calls + fire callbacks synchronously.
 * `performance.now()` is stubbed to return a controllable value so the
 * idle threshold can be crossed deterministically.
 */
import { act, cleanup, render } from "@testing-library/react";
import type { MutableRefObject, ReactNode, RefObject } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mocks ───────────────────────────────────────────────────────────
const callMock = vi.fn();
const usePythonEventMock = vi.fn();

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({
		call: callMock,
		status: "connected",
		connectionStatus: "connected",
	}),
	usePythonEvent: usePythonEventMock,
}));

vi.mock("@/types/config", () => ({}));

// rAF mock: jsdom's rAF fires at ~16ms via setInterval. We replace it
// with a controllable stub so the test can:
//   - count calls (to verify the loop pauses / resumes)
//   - fire callbacks synchronously (no real waiting)
let rafCount = 0;
let rafQueue: Array<() => void> = [];
let perfNowValue = 0;

beforeEach(() => {
	rafCount = 0;
	rafQueue = [];
	perfNowValue = 0;
	vi.stubGlobal(
		"requestAnimationFrame",
		vi.fn((cb: () => void) => {
			rafCount += 1;
			const id = rafCount;
			rafQueue.push(cb);
			return id;
		}),
	);
	vi.stubGlobal(
		"cancelAnimationFrame",
		vi.fn((id: number) => {
			void id;
		}),
	);
	vi.stubGlobal("performance", {
		now: () => perfNowValue,
	});
	callMock.mockResolvedValue({ success: true });
});

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
	callMock.mockReset();
	usePythonEventMock.mockReset();
});

// Fire the next queued rAF callback (if any). Returns true if a
// callback was fired.
function fireNextFrame(): boolean {
	const cb = rafQueue.shift();
	if (cb) {
		cb();
		return true;
	}
	return false;
}

function makeRefs(): {
	playingRef: MutableRefObject<boolean>;
	testRunningRef: MutableRefObject<boolean>;
	meterRef: RefObject<HTMLElement | null>;
} {
	const playingRef: MutableRefObject<boolean> = { current: false };
	const testRunningRef: MutableRefObject<boolean> = { current: false };
	const meterDiv = document.createElement("div");
	const progress = document.createElement("div");
	progress.setAttribute("role", "progressbar");
	const fill = document.createElement("div");
	progress.appendChild(fill);
	meterDiv.appendChild(progress);
	const meterRef: RefObject<HTMLElement | null> = { current: meterDiv };
	return { playingRef, testRunningRef, meterRef };
}

async function renderProbe(refs: ReturnType<typeof makeRefs>) {
	const { useMicrophoneLevelMonitor } = await import(
		"../useMicrophoneLevelMonitor"
	);
	const captures: {
		hook: ReturnType<typeof useMicrophoneLevelMonitor> | null;
	} = { hook: null };
	function Probe() {
		captures.hook = useMicrophoneLevelMonitor({
			// Biometric consent granted so the mount effect's
			// ``level_monitor_start`` (gated on
			// ``voice_biometric_consent``) still fires.
			config: {
				microphone: null,
				voice_biometric_consent: true,
			} as unknown as Parameters<typeof useMicrophoneLevelMonitor>[0]["config"],
			playingRef: refs.playingRef,
			testRunningRef: refs.testRunningRef,
			meterRef: refs.meterRef,
		});
		return null as unknown as ReactNode;
	}
	const utils = render(<Probe />);
	return { captures, ...utils };
}

describe("useMicrophoneLevelMonitor rAF loop pauses on idle", () => {
	it("loop schedules exactly 1 rAF on mount (initial wake)", async () => {
		const refs = makeRefs();
		await renderProbe(refs);

		// After mount, `wake()` is called once → exactly 1 rAF scheduled.
		// micMonitoring defaults to true, so the gate is open.
		expect(rafCount).toBe(1);
	});

	it("loop pauses when performance.now() - lastEvent > 500ms (idle)", async () => {
		const refs = makeRefs();
		await renderProbe(refs);

		// After mount, lastLevelEventAtRef = performance.now() = 0.
		// The first frame fires with now=0, idle check passes (0-0=0).
		// Verify it schedules the next frame.
		expect(rafCount).toBe(1);
		perfNowValue = 100; // 100ms after mount — still within idle window.
		act(() => {
			fireNextFrame();
		});
		expect(rafCount).toBe(2); // Next frame scheduled.

		// Advance time past the 500ms idle threshold.
		perfNowValue = 1000; // 1000ms after the last event — well past 500ms.
		act(() => {
			fireNextFrame();
		});
		// The idle check fails → animate returns WITHOUT scheduling.
		// rafCount should NOT have increased.
		expect(rafCount).toBe(2);

		// Fire any remaining queued callbacks (should be none — the
		// loop is paused).
		const fired = fireNextFrame();
		expect(fired).toBe(false);
	});

	it("a `mic_level` event re-arms the loop after idle pause", async () => {
		const refs = makeRefs();
		const { captures } = await renderProbe(refs);

		// Drive the loop into idle pause.
		expect(rafCount).toBe(1);
		perfNowValue = 1000; // Past the idle threshold.
		act(() => {
			fireNextFrame();
		});
		expect(rafCount).toBe(1); // No new rAF scheduled (paused).

		// Invoke the `mic_level` push handler — should update
		// `lastLevelEventAtRef.current` and call `wake()` to
		// re-arm the loop.
		const micLevelHandler = usePythonEventMock.mock.calls.find(
			(c) => c[0] === "mic_level",
		)?.[1] as ((data?: Record<string, unknown>) => unknown) | undefined;
		expect(typeof micLevelHandler).toBe("function");

		// Reset perfNowValue to "now" so the idle check passes on
		// the next frame. The push handler sets
		// `lastLevelEventAtRef.current = performance.now()`.
		perfNowValue = 1100;
		act(() => {
			micLevelHandler?.({ level: 0.5, peak: 0.7, active: true });
		});

		// `wake()` should have scheduled a new rAF → rafCount
		// increased.
		expect(rafCount).toBe(2);

		// Verify the level was written to the DOM by the resumed
		// loop's first frame.
		perfNowValue = 1150; // 50ms after the event — well within idle window.
		act(() => {
			fireNextFrame();
		});
		const fill = refs.meterRef.current?.querySelector<HTMLElement>(
			'[role="progressbar"] > div',
		);
		expect(fill).toBeTruthy();
		expect(fill?.style.width).toContain("50");
		// `levelRef` should also reflect the new value (the push
		// handler mutates the ref).
		expect(captures.hook?.levelRef.current).toBe(0.5);
	});

	it("gate-closed branch (tab hidden) does NOT reschedule (no idle spin)", async () => {
		const refs = makeRefs();
		await renderProbe(refs);

		const stubVisibility = (state: string) => {
			Object.defineProperty(document, "visibilityState", {
				value: state,
				configurable: true,
			});
		};

		expect(rafCount).toBe(1);
		// Simulate the tab being hidden. The rAF callback's
		// visibility check returns without scheduling the next
		// frame.
		stubVisibility("hidden");
		const countBeforeHiddenTick = rafCount;
		act(() => {
			fireNextFrame();
		});
		// No new rAF should have been scheduled during the hidden tick.
		expect(rafCount).toBe(countBeforeHiddenTick);

		// Restore visibility + send a mic_level event to re-arm.
		stubVisibility("visible");
		const micLevelHandler = usePythonEventMock.mock.calls.find(
			(c) => c[0] === "mic_level",
		)?.[1] as ((data?: Record<string, unknown>) => unknown) | undefined;
		act(() => {
			micLevelHandler?.({ level: 0.3, peak: 0.4, active: true });
		});
		expect(rafCount).toBeGreaterThan(countBeforeHiddenTick);
	});
});

describe("useMicrophoneLevelMonitor — consent refusal surfaces the deep-link callback", () => {
	it("invokes onConsentRequired with the envelope's consent_field when level_monitor_start is refused", async () => {
		// Race path: consent revoked between the renderer's client-side
		// gate and the IPC (or a stale renderer). The backend's
		// ``client.consent_required`` envelope must reach the caller's
		// deep-link snackbar instead of being console.warn'd silently.
		const consentErr = new Error(
			"voice biometric consent required to start level monitor",
		);
		(consentErr as { code?: string }).code = "client.consent_required";
		(consentErr as { consent_field?: string }).consent_field =
			"voice_biometric_consent";
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "level_monitor_start") return Promise.reject(consentErr);
			return Promise.resolve({ success: true });
		});

		const refs = makeRefs();
		const { useMicrophoneLevelMonitor } = await import(
			"../useMicrophoneLevelMonitor"
		);
		const onConsentRequired = vi.fn();
		const captures: {
			hook: ReturnType<typeof useMicrophoneLevelMonitor> | null;
		} = { hook: null };
		function Probe() {
			captures.hook = useMicrophoneLevelMonitor({
				config: {
					microphone: null,
					voice_biometric_consent: true,
				} as unknown as Parameters<
					typeof useMicrophoneLevelMonitor
				>[0]["config"],
				playingRef: refs.playingRef,
				testRunningRef: refs.testRunningRef,
				meterRef: refs.meterRef,
				onConsentRequired,
			});
			return null as unknown as ReactNode;
		}
		render(<Probe />);

		// Wait for the mount effect's level_monitor_start rejection.
		await act(async () => {
			await new Promise((r) => setTimeout(r, 0));
		});

		expect(onConsentRequired).toHaveBeenCalledTimes(1);
		expect(onConsentRequired).toHaveBeenCalledWith("voice_biometric_consent");
	});
});
