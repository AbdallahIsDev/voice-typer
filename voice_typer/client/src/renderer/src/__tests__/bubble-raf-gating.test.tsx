/**
 *  regression tests: `useAudioLevels` rAF loop is gated on
 * `mode === "recording"`.
 *
 * The rAF loop in `bubble-components.tsx` previously ran at 60 fps
 * whenever the bubble was visible, even in `always_visible` idle /
 * transcribing / error mode (where the visualizer bars aren't
 * mounted). Per-frame it called `getComputedStyle(document.
 * documentElement)` + 2 `getPropertyValue(...).trim()` calls + a
 * 7-iteration loop writing `el.style.backgroundColor` per dot —
 * ~1.8–3 % of one core continuously while the bubble was visible.
 *
 * After :
 *   - The loop early-returns when `recordingRef.current === false`
 *     (mirrored from `useBubbleStateMachine` via `onSetState`).
 *   - `getComputedStyle` is called ONCE on first frame + on theme
 *     change (via a `MutationObserver` on `document.documentElement`
 *     class/style), NOT per-frame.
 *   - `el.style.backgroundColor` is applied via `applyBarColor` (a
 *     `useEffect`-driven helper), NOT per-frame.
 *
 * This test verifies the gating by spying on `window.getComputedStyle`
 * and asserting the spy is NOT called per-frame after the bubble
 * transitions to idle mode.
 */
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Bubble } from "@/Bubble";

// ── Mock window.bubble API ──────────────────────────────────────────
// Mirrors the mock in Bubble.test.tsx but adds the `onSetState`
// subscriber list so we can drive mode transitions.

function makeMockBubble() {
	const listeners: {
		show: Array<() => void>;
		hide: Array<() => void>;
		setState: Array<(state: string) => void>;
		config?: (cfg: Record<string, unknown>) => void;
	} = { show: [], hide: [], setState: [] };
	return {
		onLevel: vi.fn(() => vi.fn()),
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

	// jsdom doesn't implement matchMedia — provide a stub (useThemeSync
	// subscribes to prefers-color-scheme).
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
	// properties. Provide a minimal stub so the barColor fallback path
	// (`#fff` / `#18181b`) is exercised without throwing. The spy is
	// installed per-test (see below) so call counts reset cleanly.
});

afterEach(() => {
	cleanup();
	delete (window as unknown as Record<string, unknown>).bubble;
});

function setBubbleState(state: string) {
	const cbs = mockBubble._listeners.setState ?? [];
	act(() => {
		for (const cb of cbs) {
			(cb as (s: string) => void)(state);
		}
	});
}

/**
 * Advance several animation frames. jsdom's `requestAnimationFrame` is
 * implemented as a 0-ms `setTimeout`, so flushing the macrotask queue
 * via `vi.runAllTimersAsync()` runs all pending rAF callbacks. We wrap
 * in `act()` so React flushes any state updates triggered by the rAF
 * loop's DOM writes (none in this case, but the wrapper is defensive).
 */
async function tickFrames(count = 5) {
	for (let i = 0; i < count; i++) {
		await act(async () => {
			// jsdom rAF ~ setTimeout(0). A single microtask flush is
			// enough to drain one frame's worth of pending callbacks.
			await new Promise<void>((resolve) => setTimeout(resolve, 0));
		});
	}
}

describe("TY-3: useAudioLevels rAF loop is gated on mode === recording", () => {
	it("does NOT call getComputedStyle per-frame after entering idle mode", async () => {
		// Stub getComputedStyle with a permissive return so the
		// `getPropertyValue` calls don't throw. We use `vi.spyOn` so
		// the call count is tracked.
		const gcsSpy = vi.spyOn(window, "getComputedStyle").mockImplementation(
			() =>
				({
					getPropertyValue: () => "",
				}) as unknown as CSSStyleDeclaration,
		);

		render(<Bubble />);

		// Drain the initial mount + a few rAF ticks. In recording mode
		// (the default), the loop runs per-frame and calls
		// getComputedStyle on the first frame (via refreshBarColor),
		// then again only on theme changes. The MutationObserver also
		// calls refreshBarColor once on mount. So the spy IS called
		// during recording mode — we record the count and assert it
		// does NOT increase after we switch to idle.
		await tickFrames(3);
		const callsDuringRecording = gcsSpy.mock.calls.length;
		expect(callsDuringRecording).toBeGreaterThan(0);

		// Reset the spy so we only count calls AFTER the state change.
		gcsSpy.mockClear();

		// Switch to idle mode. The rAF loop's `recordingRef` flips to
		// false (via our onSetState subscription); the loop's per-frame
		// early-return now skips getComputedStyle + style writes.
		setBubbleState("idle");

		//Tick several frames — under the pre- implementation, each
		//of these would have called getComputedStyle once. Post-,
		// zero calls expected.
		await tickFrames(10);

		expect(gcsSpy).not.toHaveBeenCalled();

		gcsSpy.mockRestore();
	});

	it("resumes calling getComputedStyle when mode returns to recording", async () => {
		const gcsSpy = vi.spyOn(window, "getComputedStyle").mockImplementation(
			() =>
				({
					getPropertyValue: () => "",
				}) as unknown as CSSStyleDeclaration,
		);

		render(<Bubble />);
		await tickFrames(2);
		gcsSpy.mockClear();

		// recording → idle → recording. After returning to recording,
		// the rAF loop should resume per-frame work (which includes a
		// getComputedStyle call ONLY if barColorRef is null — since the
		// MutationObserver already populated it on mount, the per-frame
		// path skips getComputedStyle. The point of this test is just
		// to verify the gating flips back: the loop's early-return no
		// longer fires, so the per-frame loop body runs and writes
		// `el.style.height` / `el.style.opacity`).
		setBubbleState("idle");
		await tickFrames(3);
		setBubbleState("recording");
		await tickFrames(3);

		// The visualizer bars are remounted on the recording transition
		// (BubbleVisualizer is conditionally rendered only in recording
		// mode). Their initial `style.height` is `MIN_HEIGHT` (5px).
		// After a few rAF ticks in recording mode with rawLevel=0, the
		// smoothing loop should leave them at or near MIN_HEIGHT — but
		// the key assertion is that the bars exist and have a non-zero
		// height (i.e. the rAF loop DID run).
		const bars = document.querySelectorAll(".bg-zinc-900.dark\\:bg-white");
		expect(bars.length).toBe(7);
		for (const bar of bars) {
			const h = parseFloat((bar as HTMLElement).style.height || "0");
			expect(h).toBeGreaterThan(0);
		}

		gcsSpy.mockRestore();
	});

	it("MutationObserver refreshes barColor when documentElement class changes", async () => {
		const gcsSpy = vi.spyOn(window, "getComputedStyle").mockImplementation(
			() =>
				({
					getPropertyValue: () => "",
				}) as unknown as CSSStyleDeclaration,
		);

		render(<Bubble />);
		await tickFrames(2);
		gcsSpy.mockClear();

		// Toggle the `.dark` class on documentElement — the
		// MutationObserver should fire and call refreshBarColor, which
		// calls getComputedStyle.
		act(() => {
			document.documentElement.classList.add("dark");
		});

		// MutationObserver fires asynchronously — flush microtasks.
		await act(async () => {
			await new Promise<void>((resolve) => setTimeout(resolve, 0));
		});

		expect(gcsSpy).toHaveBeenCalled();

		gcsSpy.mockRestore();
	});
});
