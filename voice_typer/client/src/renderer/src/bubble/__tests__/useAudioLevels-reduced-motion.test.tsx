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

/** Parse the scaleY factor out of an inline `transform: scaleY(s)`. */
function parseScaleY(transform: string): number {
	const m = transform.match(/scaleY\(([\d.eE+-]+)\)/);
	return m?.[1] ? Number.parseFloat(m[1]) : Number.NaN;
}

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
		// so the initial `reducedMotionMql.matches` read gets `true`.
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
		const midScale = midHeight / MAX_HEIGHT;
		for (const bar of bars) {
			// The bar animates via a transform on a full-height box:
			// the box height is the constant MAX_HEIGHT and the scale
			// lands the VISUAL bar at the static mid-height, NOT the
			// level-driven animated height.
			expect(bar.style.height).toBe(`${MAX_HEIGHT}px`);
			expect(parseScaleY(bar.style.transform)).toBeCloseTo(midScale, 5);
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
		const midScale = midHeight / MAX_HEIGHT;
		for (const bar of bars) {
			expect(bar.style.height).toBe(`${MAX_HEIGHT}px`);
			expect(parseScaleY(bar.style.transform)).toBeCloseTo(midScale, 5);
			expect(bar.style.opacity).toBe("0.5");
		}
	});

	it("does NOT gate when reduced-motion is not set (no false positive)", async () => {
		reducedMotionMql.matches = false;

		// Deterministic rAF driver (see the transform-writes test for
		// the rationale — jsdom's real rAF clock is not flushed
		// reliably by setTimeout(0) ticks).
		const rafQueue: FrameRequestCallback[] = [];
		const rafSpy = vi
			.spyOn(window, "requestAnimationFrame")
			.mockImplementation((cb: FrameRequestCallback) => {
				rafQueue.push(cb);
				return rafQueue.length;
			});
		const flushFrames = (count: number) => {
			for (let i = 0; i < count; i++) {
				const cbs = rafQueue.splice(0);
				for (const cb of cbs) cb(performance.now());
			}
		};

		render(<Bubble />);
		showBubble();
		flushFrames(3);

		// Push audio levels so bars animate to non-mid-height values.
		act(() => {
			for (const cb of mockBubble._listeners.level) {
				cb({ rms: 0.9, peak: 0.99 });
			}
		});
		flushFrames(6);

		const bars =
			document.querySelectorAll<HTMLSpanElement>(".gap-0\\.75 > span");
		expect(bars.length).toBe(7);

		const midHeight = (MIN_HEIGHT + MAX_HEIGHT) / 2;
		const midScale = midHeight / MAX_HEIGHT;
		// At least one bar should NOT be at the static mid-height — the
		// animation is running normally.
		const atMid = Array.from(bars).filter(
			(b) =>
				Math.abs(parseScaleY(b.style.transform) - midScale) < 0.01 / MAX_HEIGHT,
		);
		expect(atMid.length).toBeLessThan(bars.length);

		rafSpy.mockRestore();
	});

	it("animates bars via transform writes, never per-frame height/layout geometry", async () => {
		reducedMotionMql.matches = false;

		// jsdom's real `requestAnimationFrame` fires on a ~16 ms
		// internal clock that `setTimeout(0)` flushes only race
		// against — the loop's DOM writes would be timing-flaky.
		// Drive the frames deterministically instead: collect the
		// callbacks in a queue and flush them manually (the loop's
		// self-rescheduling lands in the NEXT flush iteration).
		const rafQueue: FrameRequestCallback[] = [];
		const rafSpy = vi
			.spyOn(window, "requestAnimationFrame")
			.mockImplementation((cb: FrameRequestCallback) => {
				rafQueue.push(cb);
				return rafQueue.length;
			});
		const flushFrames = (count: number) => {
			for (let i = 0; i < count; i++) {
				const cbs = rafQueue.splice(0);
				for (const cb of cbs) cb(performance.now());
			}
		};

		render(<Bubble />);
		showBubble();
		flushFrames(3);

		// Push audio levels so the loop animates through several frames.
		act(() => {
			for (const cb of mockBubble._listeners.level) {
				cb({ rms: 0.8, peak: 0.9 });
			}
		});
		flushFrames(6);

		const bars =
			document.querySelectorAll<HTMLSpanElement>(".gap-0\\.75 > span");
		expect(bars.length).toBe(7);
		for (const bar of bars) {
			// Base layout box: reserved ONCE at the full height — a
			// per-frame height write would force layout on the pill
			// (the LevelBar-style compositor-only contract).
			expect(bar.style.height).toBe(`${MAX_HEIGHT}px`);
			// The animated value lives in the transform...
			const scale = parseScaleY(bar.style.transform);
			expect(Number.isNaN(scale)).toBe(false);
			const visual = scale * MAX_HEIGHT;
			expect(visual).toBeGreaterThanOrEqual(MIN_HEIGHT - 1e-9);
			expect(visual).toBeLessThanOrEqual(MAX_HEIGHT + 1e-9);
			// ...plus the cap-radius var consumed by the prepared
			// counter-scaled border-radius.
			const barScaleVar = bar.style.getPropertyValue("--bar-scale");
			expect(barScaleVar).not.toBe("");
			expect(Number.parseFloat(barScaleVar)).toBeCloseTo(scale, 5);
			// The prepared radius keeps the counter-scaled form.
			expect(bar.style.borderRadius).toContain("max(var(--bar-scale)");
		}

		rafSpy.mockRestore();
	});
});
