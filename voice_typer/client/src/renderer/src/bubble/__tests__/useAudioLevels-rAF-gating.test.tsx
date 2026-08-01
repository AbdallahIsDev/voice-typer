/**
 *  regression tests: `useAudioLevels` rAF loop scheduling gate.
 *
 * Background
 * ----------
 * Pre-: the rAF loop in `useAudioLevels.ts` unconditionally
 * re-scheduled the next frame at the TOP of the `animate` callback:
 *
 *   const animate = () => {
 *     frameRef.current = requestAnimationFrame(animate); // always
 *     if (!visibleRef.current) return;
 *     if (!recordingRef.current) return;
 *     // ... DOM work ...
 *   };
 *
 * Combined with the bubble BrowserWindow's `backgroundThrottling: false`
 * (lifecycle.ts:99), this meant the renderer process never entered an
 * idle/low-power state — the rAF chain kept the process warm at 60 Hz
 * for the entire app lifetime, even when the bubble was hidden (which
 * is ~90 % of the lifetime in `show_on_record` mode, the default).
 *
 * Post-: the scheduling call has moved to the END of the callback,
 * guarded by `if (visibleRef.current && recordingRef.current)`. When
 * either gate closes, the loop STOPS scheduling new frames. A separate
 * `useEffect` watches the `isVisible` prop and (a) cancels the
 * in-flight frame when `isVisible` becomes false, and (b) re-arms the
 * loop via `wake()` when `isVisible` becomes true. The `onShow` /
 * `onSetState` callbacks also call `wake()` to cover the recording-mode
 * re-arm path.
 *
 * These tests verify:
 *   1. `cancelAnimationFrame` IS called when the bubble is hidden
 *      (the in-flight frame is cancelled, not just left to no-op).
 *   2. `requestAnimationFrame` is NOT called again after the bubble is
 *      hidden (the loop stops spinning).
 *   3. `requestAnimationFrame` resumes when the bubble becomes visible
 *      again (re-arm via the `isVisible` watcher effect).
 *
 * The test renders the full `<Bubble />` component (which wires
 * `useAudioLevels` via `useBubbleLifecycle`) so the rAF loop, the
 * recording-mode tracking, and the visibility watcher are all
 * exercised in their real configuration.
 */
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Bubble } from "@/Bubble";

// ── Mock window.bubble API ──────────────────────────────────────────
// Mirrors the mock in bubble_rAF_pause.test.tsx — provides the
// `onShow` / `onHide` / `onSetState` / `onLevel` subscribers so the
// test can drive visibility + mode transitions.

function makeMockBubble() {
	const listeners: {
		show: Array<() => void>;
		hide: Array<() => void>;
		setState: Array<(state: string) => void>;
		level: Array<(data: { rms: number; peak: number }) => void>;
		config?: (cfg: Record<string, unknown>) => void;
	} = { show: [], hide: [], setState: [], level: [] };
	return {
		onLevel: vi.fn((cb: (data: { rms: number; peak: number }) => void) => {
			listeners.level.push(cb);
			return () => {
				listeners.level = listeners.level.filter((l) => l !== cb);
			};
		}),
		onShow: vi.fn((cb: () => void) => {
			listeners.show.push(cb);
			return () => {
				listeners.show = listeners.show.filter((l) => l !== cb);
			};
		}),
		onHide: vi.fn((cb: () => void) => {
			listeners.hide.push(cb);
			return () => {
				listeners.hide = listeners.hide.filter((l) => l !== cb);
			};
		}),
		onSetState: vi.fn((cb: (state: string) => void) => {
			listeners.setState.push(cb);
			return () => {
				listeners.setState = listeners.setState.filter((l) => l !== cb);
			};
		}),
		onDraggable: vi.fn(() => vi.fn()),
		signalReady: vi.fn(),
		hideComplete: vi.fn(),
		resizeTo: vi.fn(),
		moveBy: vi.fn(),
		onConfig: vi.fn((cb: (cfg: Record<string, unknown>) => void) => {
			listeners.config = cb;
			return () => {
				listeners.config = undefined;
			};
		}),
		toggleDictation: vi.fn(),
		dismiss: vi.fn(),
		_listeners: listeners,
	};
}

let mockBubble: ReturnType<typeof makeMockBubble>;

beforeEach(() => {
	mockBubble = makeMockBubble();
	(window as unknown as Record<string, unknown>).bubble = mockBubble;

	Object.defineProperty(window, "matchMedia", {
		value: vi.fn().mockImplementation((query: string) => ({
			matches: false,
			media: query,
			onchange: null,
			addListener: vi.fn(),
			removeListener: vi.fn(),
			addEventListener: vi.fn(),
			removeEventListener: vi.fn(),
			dispatchEvent: vi.fn(),
		})),
		writable: true,
	});

	// jsdom's getComputedStyle returns empty strings for CSS custom
	// properties — stub it so the barColor fallback path doesn't throw.
	vi.spyOn(window, "getComputedStyle").mockImplementation(
		() =>
			({
				getPropertyValue: () => "",
			}) as unknown as CSSStyleDeclaration,
	);
});

afterEach(() => {
	vi.restoreAllMocks();
	cleanup();
	delete (window as unknown as Record<string, unknown>).bubble;
});

function showBubble() {
	act(() => {
		for (const cb of mockBubble._listeners.show) cb();
	});
}

function hideBubble() {
	act(() => {
		for (const cb of mockBubble._listeners.hide) cb();
	});
}

/**
 * Drain pending rAF callbacks. jsdom implements rAF as `setTimeout(0)`,
 * so flushing the macrotask queue runs all pending frames.
 */
async function tickFrames(count = 5) {
	for (let i = 0; i < count; i++) {
		await act(async () => {
			await new Promise<void>((resolve) => setTimeout(resolve, 0));
		});
	}
}

describe("AB-39: useAudioLevels rAF scheduling gate", () => {
	it("calls cancelAnimationFrame when the bubble becomes hidden", async () => {
		// Spy on cancelAnimationFrame. We need the REAL implementation
		// so the cancellation actually takes effect, but we want to
		// count calls.
		const cancelSpy = vi.spyOn(window, "cancelAnimationFrame");

		render(<Bubble />);

		// Show the bubble so the rAF loop starts.
		showBubble();
		await tickFrames(3);

		// Reset the spy so we count ONLY post-hide calls.
		cancelSpy.mockClear();

		// Hide the bubble. The visibility-watching `useEffect` should
		// cancel the in-flight frame.
		hideBubble();
		await tickFrames(2);

		//at least one cancelAnimationFrame call must have
		// fired against the in-flight frame. (Exactly one is expected,
		// but `>= 1` is the safe assertion — React may re-run the
		// effect in edge cases.)
		const cancelCalls = cancelSpy.mock.calls.length;
		expect(cancelCalls).toBeGreaterThanOrEqual(1);

		cancelSpy.mockRestore();
	});

	it("does NOT schedule new requestAnimationFrame frames after the bubble is hidden", async () => {
		// Spy on requestAnimationFrame. We need to call the REAL rAF
		// so the loop actually runs, but we want to count calls.
		const rafSpy = vi.spyOn(window, "requestAnimationFrame");

		render(<Bubble />);
		showBubble();
		await tickFrames(3);

		// Reset the spy so we count ONLY post-hide calls.
		rafSpy.mockClear();

		// Hide the bubble. The next in-flight frame will run, find
		// `visibleRef.current === false`, and NOT schedule another
		// frame. The visibility-watcher effect ALSO cancels the
		// in-flight frame. After tickFrames flushes, no further rAF
		// calls should occur.
		hideBubble();
		await tickFrames(10);

		//the loop did NOT keep spinning. At most ZERO rAF
		// calls post-hide (the in-flight frame was cancelled by the
		// visibility-watcher effect, so even the one in-flight rAF
		// callback may not fire).
		expect(rafSpy.mock.calls.length).toBe(0);

		// Tick more frames to be sure no further rAFs are scheduled.
		const callsAfterMoreTicks = rafSpy.mock.calls.length;
		await tickFrames(10);
		expect(rafSpy.mock.calls.length).toBe(callsAfterMoreTicks);

		rafSpy.mockRestore();
	});

	it("resumes scheduling requestAnimationFrame frames when the bubble becomes visible again", async () => {
		const rafSpy = vi.spyOn(window, "requestAnimationFrame");

		render(<Bubble />);
		showBubble();
		await tickFrames(3);

		// Hide, then verify the loop stops.
		hideBubble();
		await tickFrames(5);
		rafSpy.mockClear();

		// No rAF calls should happen while hidden.
		await tickFrames(5);
		expect(rafSpy.mock.calls.length).toBe(0);

		// Show again — the visibility-watcher effect's wake() should
		// kick off a fresh rAF, and the loop should resume scheduling
		// per-frame.
		showBubble();
		await tickFrames(3);

		//the loop resumed — rAF calls are happening again.
		expect(rafSpy.mock.calls.length).toBeGreaterThan(0);

		rafSpy.mockRestore();
	});

	it("still animates when visible AND in recording mode (no regression)", async () => {
		//critical rule: the bubble MUST still animate when
		// visible AND recording. This test verifies the loop runs
		// per-frame in the default state (visible + recording).
		const rafSpy = vi.spyOn(window, "requestAnimationFrame");

		render(<Bubble />);
		showBubble();
		await tickFrames(5);

		// In recording mode (default) + visible, the loop should be
		// scheduling rAF frames continuously.
		expect(rafSpy.mock.calls.length).toBeGreaterThan(0);

		rafSpy.mockRestore();
	});
});
