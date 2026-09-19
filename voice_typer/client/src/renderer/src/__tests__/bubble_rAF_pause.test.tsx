import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Bubble } from "@/Bubble";

// ── Mock window.bubble API ──────────────────────────────────────────
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
	// properties, stub it so the barColor fallback path doesn't throw.
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

async function tickFrames(count = 5) {
	for (let i = 0; i < count; i++) {
		await act(async () => {
			await new Promise<void>((resolve) => setTimeout(resolve, 0));
		});
	}
}

describe("DJ-90: useAudioLevels rAF loop pauses when bubble is hidden", () => {
	it("does NOT schedule new rAF frames after the bubble is hidden", async () => {
		// Spy on requestAnimationFrame. We need to call the REAL rAF
		// so the loop actually runs, but we want to count calls.
		const rafSpy = vi.spyOn(window, "requestAnimationFrame");

		render(<Bubble />);

		// Trigger an initial show so the loop starts.
		showBubble();
		await tickFrames(3);

		// Reset the spy so we count ONLY post-hide calls.
		rafSpy.mockClear();

		// Hide the bubble. The next in-flight frame will run, find
		// `visibleRef.current === false`, and NOT schedule another
		// frame. After tickFrames flushes the in-flight frame, no
		// further rAF calls should occur.
		hideBubble();
		await tickFrames(10);

		// At most ONE call, the in-flight frame that was already
		// scheduled before hideBubble() fired. (If hideBubble happened
		// to fire between frames, even that one might not run.) The
		// key assertion: the loop did NOT keep spinning.
		expect(rafSpy.mock.calls.length).toBeLessThanOrEqual(1);

		// Tick more frames to be sure no further rAFs are scheduled.
		const callsAfterMoreTicks = rafSpy.mock.calls.length;
		await tickFrames(10);
		expect(rafSpy.mock.calls.length).toBe(callsAfterMoreTicks);
	});

	it("resumes scheduling rAF frames when the bubble becomes visible again", async () => {
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

		// Show again, the wake trigger (in the visibility-tracking
		// effect) should kick off a fresh rAF, and the loop should
		// resume scheduling per-frame.
		showBubble();
		await tickFrames(3);

		expect(rafSpy.mock.calls.length).toBeGreaterThan(0);
	});
});
