/**
 * Regression tests: `useAudioLevels` reduced-motion rAF stop.
 *
 * Background
 * ----------
 * Pre-fix: the `animate()` callback's reduced-motion branch called
 * `renderReducedMotion()` (writes static `height`/`opacity` to the 7
 * dot elements) and then UNCONDITIONALLY scheduled the next frame via
 * `frameRef.current = requestAnimationFrame(animate)`. The loop spun
 * at 60 fps writing the SAME static styles every frame — pure waste
 * (the bars are motionless, so re-writing `height`/`opacity` to the
 * same values 60 times per second costs CPU + keeps the renderer
 * process out of idle).
 *
 * Post-fix: the reduced-motion branch calls
 * `renderReducedMotion()` ONCE and then `return`s WITHOUT scheduling
 * the next frame. The loop re-arms via `wake()` when a gate flips
 * (visibility-watching effect on `isVisible → true`, `api.onShow` /
 * `api.onSetState` on recording-mode transitions). `wake()` itself
 * calls `renderReducedMotion()` before scheduling, so a re-arm
 * produces exactly one frame (which then stops again).
 *
 * These tests verify:
 *   1. When `prefers-reduced-motion: reduce` matches, the rAF loop
 *      does NOT schedule additional frames after the initial
 *      `renderReducedMotion()` call. (Spy on
 *      `requestAnimationFrame`, count calls after the first frame —
 *      should be 0.)
 *   2. The bars ARE rendered at the static mid-height (the one
 *      `renderReducedMotion()` call DID fire — not a no-op).
 *
 * NOTE: this file is `.ts` (not `.tsx`) per the task spec. The
 * existing `useAudioLevels-reduced-motion.test.tsx` covers the
 * broader reduced-motion behavior (mount-time render, runtime toggle,
 * no-false-positive). This file focuses on the specific
 * "loop stops after one frame" assertion.
 */
import { act, cleanup, renderHook } from "@testing-library/react";
import { createElement, type MutableRefObject, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DOT_COUNT, MAX_HEIGHT, MIN_HEIGHT } from "@/bubble/constants";
import { useAudioLevels } from "@/bubble/useAudioLevels";
import { BubbleBridgeProvider } from "@/bubble/useBubbleBridge";

// ── Mock window.bubble API ──────────────────────────────────────────
// Mirrors the mock in useAudioLevels-rAF-gating.test.tsx but stripped
// to the minimum the hook touches (onShow / onSetState / onLevel).

function makeMockBubble() {
	const listeners: {
		show: Array<() => void>;
		setState: Array<(state: string) => void>;
		level: Array<(data: { rms: number; peak: number }) => void>;
	} = { show: [], setState: [], level: [] };
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
		onSetState: vi.fn((cb: (state: string) => void) => {
			listeners.setState.push(cb);
			return () => {
				listeners.setState = listeners.setState.filter((l) => l !== cb);
			};
		}),
		_listeners: listeners,
	};
}

let mockBubble: ReturnType<typeof makeMockBubble>;

// Wrapper that mounts the BubbleBridgeProvider so useAudioLevels can
// obtain the bridge via context. The provider's effect attaches to
// `window.bubble` (mocked above) on mount.
function wrapper({ children }: { children: ReactNode }) {
	return createElement(BubbleBridgeProvider, null, children);
}

beforeEach(() => {
	mockBubble = makeMockBubble();
	(window as unknown as Record<string, unknown>).bubble = mockBubble;

	// Mock `matchMedia` so `prefers-reduced-motion: reduce` matches.
	// The hook calls `window.matchMedia("(prefers-reduced-motion: reduce)")`
	// on every `animate()` call (via `prefersReducedMotion()`).
	vi.stubGlobal(
		"matchMedia",
		vi.fn().mockImplementation((query: string) => ({
			matches: query === "(prefers-reduced-motion: reduce)",
			media: query,
			onchange: null,
			addListener: vi.fn(),
			removeListener: vi.fn(),
			addEventListener: vi.fn(),
			removeEventListener: vi.fn(),
			dispatchEvent: vi.fn(),
		})),
	);

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
	cleanup();
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
	delete (window as unknown as Record<string, unknown>).bubble;
});

/**
 * Flush pending macrotasks. jsdom implements `requestAnimationFrame`
 * as `setTimeout(0)`, so flushing the macrotask queue runs all
 * pending rAF callbacks.
 */
async function flushMacrotasks(count = 5) {
	for (let i = 0; i < count; i++) {
		await act(async () => {
			await new Promise<void>((resolve) => setTimeout(resolve, 0));
		});
	}
}

function makeDotRefs(): MutableRefObject<(HTMLSpanElement | null)[]> {
	const dots: (HTMLSpanElement | null)[] = [];
	for (let i = 0; i < DOT_COUNT; i++) {
		const el = document.createElement("span");
		document.body.appendChild(el);
		dots.push(el);
	}
	return { current: dots };
}

describe("useAudioLevels reduced-motion rAF stop", () => {
	it("does NOT schedule additional rAF frames after renderReducedMotion (loop stops)", async () => {
		const dotRefs = makeDotRefs();

		// Spy on requestAnimationFrame. Use the REAL implementation so
		// the loop actually runs (jsdom's rAF = setTimeout(0)), but
		// count calls.
		const rafSpy = vi.spyOn(window, "requestAnimationFrame");

		renderHook(() => useAudioLevels(dotRefs, true), { wrapper });

		// `wake()` is called on mount (end of the subscription effect).
		// It schedules one rAF → `animate()` runs → hits the
		// reduced-motion branch → calls `renderReducedMotion()` →
		// returns WITHOUT scheduling the next frame.
		// Flush macrotasks so that one frame fires.
		await flushMacrotasks(3);

		// Record the rAF call count after the initial frame.
		const callsAfterInitial = rafSpy.mock.calls.length;
		expect(callsAfterInitial).toBeGreaterThanOrEqual(1);

		// Reset the spy so we count ONLY post-initial-frame calls.
		rafSpy.mockClear();

		// Flush more macrotasks — if the loop were still spinning,
		// additional rAF calls would happen here.
		await flushMacrotasks(10);

		// Assertion: NO additional rAF calls after the initial
		// frame. The reduced-motion branch stopped the loop.
		expect(rafSpy.mock.calls.length).toBe(0);

		rafSpy.mockRestore();
	});

	it("renders bars at the static mid-height (the one renderReducedMotion call DID fire)", async () => {
		const dotRefs = makeDotRefs();

		renderHook(() => useAudioLevels(dotRefs, true), { wrapper });

		// Flush so the initial `wake()` → `animate()` →
		// `renderReducedMotion()` chain fires.
		await flushMacrotasks(3);

		const midHeight = (MIN_HEIGHT + MAX_HEIGHT) / 2;
		for (const el of dotRefs.current) {
			if (!el) continue;
			// The bars should be at the static mid-height with opacity 0.5.
			expect(parseFloat(el.style.height)).toBeCloseTo(midHeight, 5);
			expect(el.style.opacity).toBe("0.5");
		}
	});
});
