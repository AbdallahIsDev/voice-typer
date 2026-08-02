/**
 * Reduced-motion gating regression tests for the bubble visualizer rAF.
 *
 * Background
 * ----------
 * Pre-fix: `useAudioLevels.ts` drove the 7-bar bubble visualizer via
 * `requestAnimationFrame` and directly mutated `el.style.height` and
 * `el.style.opacity` at 60fps. There was NO check for
 * `window.matchMedia("(prefers-reduced-motion: reduce)").matches` — the
 * CSS `@media (prefers-reduced-motion: reduce)` rule only affects CSS
 * animations/transitions and CANNOT suppress JS-driven rAF DOM mutation.
 * Vestibular/motion-sensitive users (≈35% of population) could not
 * disable the bubble's animated bars — WCAG 2.1 SC 2.3.3 "Animation
 * from Interactions" violation.
 *
 * Post-fix: `useAudioLevels` reads `reducedMotionRef.current` (set once
 * on mount from `window.matchMedia("(prefers-reduced-motion: reduce)")`
 * + re-evaluated on the media query's `change` event). In `animate()`,
 * when reduced-motion is set, the per-bar height/opacity mutation is
 * SKIPPED, and the bars are rendered once at a static mid-height with
 * opacity 0.5 so the visualizer is still visible but motionless.
 *
 * These tests verify:
 *   1. When `prefers-reduced-motion: reduce` matches at mount, the bars
 *      are rendered at the static mid-height with opacity 0.5 — NOT at
 *      the animated level-driven heights.
 *   2. The rAF loop still spins (so we can react to visibility /
 *      recording gates + the `change` event) — no regression of
 *   3. Toggling reduced-motion at runtime (firing the `change` event)
 *      snaps the bars to the static mid-height.
 */
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Bubble } from "@/Bubble";
import { MAX_HEIGHT, MIN_HEIGHT } from "@/bubble/constants";

// ── Mock window.bubble API ──────────────────────────────────────────
// Mirrors the mock in useAudioLevels-rAF-gating.test.tsx.

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

// A controllable matchMedia mock — lets each test decide whether
// `prefers-reduced-motion: reduce` matches at mount + dispatch `change`
// events at runtime.
interface MockMQL {
	matches: boolean;
	media: string;
	onchange: ((e: MediaQueryListEvent) => void) | null;
	addEventListener: (
		type: string,
		cb: (e: MediaQueryListEvent) => void,
	) => void;
	removeEventListener: (
		type: string,
		cb: (e: MediaQueryListEvent) => void,
	) => void;
	dispatchEvent: (e: MediaQueryListEvent) => boolean;
}

let reducedMotionMql: MockMQL;
let reducedMotionChangeListeners: Array<(e: MediaQueryListEvent) => void>;

beforeEach(() => {
	mockBubble = makeMockBubble();
	(window as unknown as Record<string, unknown>).bubble = mockBubble;

	reducedMotionChangeListeners = [];
	reducedMotionMql = {
		matches: false,
		media: "(prefers-reduced-motion: reduce)",
		onchange: null,
		addEventListener: (type, cb) => {
			if (type === "change") reducedMotionChangeListeners.push(cb);
		},
		removeEventListener: (type, cb) => {
			if (type === "change") {
				reducedMotionChangeListeners = reducedMotionChangeListeners.filter(
					(l) => l !== cb,
				);
			}
		},
		dispatchEvent: (e) => {
			for (const l of reducedMotionChangeListeners) l(e);
			return true;
		},
	};

	// Direct assignment (not Object.defineProperty) so we override any
	// leftover matchMedia mock from a previous test file that used
	// Object.defineProperty with writable:true. The delete+assign pattern
	// is the most robust way to replace a configurable property.
	const matchMediaImpl = (query: string) => {
		if (query === "(prefers-reduced-motion: reduce)") {
			return reducedMotionMql;
		}
		return {
			matches: false,
			media: query,
			onchange: null,
			addEventListener: vi.fn(),
			removeEventListener: vi.fn(),
			dispatchEvent: vi.fn(),
		};
	};
	try {
		delete (window as unknown as Record<string, unknown>).matchMedia;
	} catch {
		// property may be non-configurable; ignore
	}
	(window as unknown as Record<string, unknown>).matchMedia =
		vi.fn(matchMediaImpl);

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
	vi.unstubAllGlobals();
	cleanup();
	delete (window as unknown as Record<string, unknown>).bubble;
});

function showBubble() {
	act(() => {
		for (const cb of mockBubble._listeners.show) cb();
	});
}

async function tickFrames(count = 5) {
	for (let i = 0; i < count; i++) {
		await act(async () => {
			await new Promise<void>((resolve) => setTimeout(resolve, 0));
		});
	}
}

describe("reduced-motion gating (prefers-reduced-motion)", () => {
	it("renders bars at static mid-height with opacity 0.5 when reduced-motion is set at mount", async () => {
		// Pre-set the matchMedia mock to match BEFORE the component mounts
		// so the initial `reducedMotionRef.current = mq.matches` reads `true`.
		reducedMotionMql.matches = true;

		render(<Bubble />);
		showBubble();
		await tickFrames(5);

		// Push a non-zero audio level so the non-reduced path would
		// otherwise produce animated heights != mid-height.
		act(() => {
			for (const cb of mockBubble._listeners.level) {
				cb({ rms: 0.5, peak: 0.7 });
			}
		});
		await tickFrames(5);

		const bars =
			document.querySelectorAll<HTMLSpanElement>(".gap-0\\.75 > span");
		expect(bars.length).toBe(7);

		const midHeight = (MIN_HEIGHT + MAX_HEIGHT) / 2;
		for (const bar of bars) {
			// The bar height should be the static mid-height, NOT the
			// level-driven animated height.
			expect(parseFloat(bar.style.height)).toBeCloseTo(midHeight, 5);
			expect(bar.style.opacity).toBe("0.5");
		}
	});

	it("rAF loop still spins when reduced-motion is set (no AB-39 regression)", async () => {
		reducedMotionMql.matches = true;

		const rafSpy = vi.spyOn(window, "requestAnimationFrame");

		render(<Bubble />);
		showBubble();
		await tickFrames(3);

		// The loop should still be scheduling rAF frames — the
		// reduced-motion gate skips the per-bar mutation but does NOT
		// stop the loop (so we can react to visibility / recording gates
		// + the `change` event without a remount).
		expect(rafSpy.mock.calls.length).toBeGreaterThan(0);

		rafSpy.mockRestore();
	});

	it("toggling reduced-motion at runtime snaps bars to static mid-height", async () => {
		// Mount WITHOUT reduced-motion — bars animate normally.
		reducedMotionMql.matches = false;

		render(<Bubble />);
		showBubble();
		await tickFrames(5);

		// Push audio levels so bars have non-mid-height values.
		act(() => {
			for (const cb of mockBubble._listeners.level) {
				cb({ rms: 0.8, peak: 0.9 });
			}
		});
		await tickFrames(5);

		// Now toggle reduced-motion ON at runtime — dispatch the
		// `change` event the same way the browser would.
		act(() => {
			reducedMotionMql.matches = true;
			const fakeEvent = {
				media: "(prefers-reduced-motion: reduce)",
				matches: true,
			} as MediaQueryListEvent;
			reducedMotionMql.dispatchEvent(fakeEvent);
		});
		await tickFrames(3);

		const bars =
			document.querySelectorAll<HTMLSpanElement>(".gap-0\\.75 > span");
		expect(bars.length).toBe(7);

		const midHeight = (MIN_HEIGHT + MAX_HEIGHT) / 2;
		for (const bar of bars) {
			expect(parseFloat(bar.style.height)).toBeCloseTo(midHeight, 5);
			expect(bar.style.opacity).toBe("0.5");
		}
	});

	it("does NOT gate when reduced-motion is not set (no false positive)", async () => {
		reducedMotionMql.matches = false;

		render(<Bubble />);
		showBubble();
		await tickFrames(5);

		// Push audio levels so bars animate to non-mid-height values.
		act(() => {
			for (const cb of mockBubble._listeners.level) {
				cb({ rms: 0.9, peak: 0.99 });
			}
		});
		await tickFrames(5);

		const bars =
			document.querySelectorAll<HTMLSpanElement>(".gap-0\\.75 > span");
		expect(bars.length).toBe(7);

		const midHeight = (MIN_HEIGHT + MAX_HEIGHT) / 2;
		// At least one bar should NOT be at the static mid-height — the
		// animation is running normally.
		const atMid = Array.from(bars).filter(
			(b) => Math.abs(parseFloat(b.style.height) - midHeight) < 0.01,
		);
		expect(atMid.length).toBeLessThan(bars.length);
	});
});
