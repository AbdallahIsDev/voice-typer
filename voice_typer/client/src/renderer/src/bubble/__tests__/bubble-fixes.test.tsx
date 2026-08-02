/**
 * Tests for the bubble overlay's accessibility + state-machine
 * behaviour introduced by the  fix batch:
 *
 *   - `prefers-reduced-motion: reduce` short-circuits the rAF loop
 *     (bars rendered ONCE at a fixed mid-height, no further frames
 *     scheduled). Mirrors the CSS-side `@media (prefers-reduced-motion:
 *     reduce)` block in `index.css`.
 *   - `bubble:config` payload with a `locale` field flips
 *     `document.documentElement.dir` so RTL locales (Arabic) flip the
 *     pill's logical-property utilities at runtime.
 *   - `bubble:set-state` payload can carry a richer
 *     `{ state: string; message?: string }` shape (forward-compatible
 *     with the typed `(state: string) => void` callback) and the
 *     `message` is surfaced in the error pill.
 *   - Error mode auto-hides after `ERROR_AUTO_HIDE_MS` (7s) when the
 *     bubble is in `show_on_record` behavior; stays sticky in
 *     `always_visible`.
 *   - Recording state interrupts the fading→exit transition (zeroes
 *     exitTick, restores enter animation, switches mode to recording).
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Bubble } from "@/Bubble";

// ── Mock window.bubble API ──────────────────────────────────────────
// Mirrors the mock in Bubble.test.tsx + adds `onSetState`'s richer
// payload shape so we can drive the error-message + auto-hide tests.

function makeMockBubble() {
	const listeners: {
		show: Array<() => void>;
		hide: Array<() => void>;
		setState: Array<(state: unknown) => void>;
		// `config` is an ARRAY here (not a single callback) so multiple
		// subscribers (`Bubble.tsx` AND `useThemeSync`) both receive the
		// pushed config. The original mock in `Bubble.test.tsx` stores
		// only the LAST subscriber, which works for `Bubble.tsx`-only
		// assertions but hides `useThemeSync`'s `dir` sync.
		config: Array<(cfg: Record<string, unknown>) => void>;
	} = { show: [], hide: [], setState: [], config: [] };
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
		onSetState: vi.fn((cb: (state: unknown) => void) => {
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
			listeners.config.push(cb);
			return () => {
				listeners.config = listeners.config.filter((l) => l !== cb);
			};
		}),
		toggleDictation: vi.fn(),
		dismiss: vi.fn(),
		_listeners: listeners,
	};
}

let mockBubble: ReturnType<typeof makeMockBubble>;
// Per-test override for `prefers-reduced-motion`. `null` means "use the
// test-setup default" (which returns `matches: false`).
let reducedMotionMatches: boolean | null = null;

beforeEach(() => {
	mockBubble = makeMockBubble();
	(window as unknown as Record<string, unknown>).bubble = mockBubble;

	// Stub window.matchMedia. Tests that probe `prefers-reduced-motion`
	// override `reducedMotionMatches` to control the branch.
	Object.defineProperty(window, "matchMedia", {
		value: vi.fn().mockImplementation((query: string) => ({
			matches:
				query === "(prefers-reduced-motion: reduce)"
					? reducedMotionMatches === true
					: false,
			media: query,
			onchange: null,
			addListener: vi.fn(),
			removeListener: vi.fn(),
			addEventListener: vi.fn(),
			removeEventListener: vi.fn(),
			dispatchEvent: vi.fn(),
		})),
		writable: true,
		configurable: true,
	});

	// jsdom's getComputedStyle returns empty strings for CSS custom
	// properties — stub it so the barColor fallback path doesn't throw.
	vi.spyOn(window, "getComputedStyle").mockImplementation(
		() =>
			({
				getPropertyValue: () => "",
			}) as unknown as CSSStyleDeclaration,
	);

	// Reset document.dir so previous tests' RTL state doesn't leak.
	document.documentElement.dir = "ltr";
});

afterEach(() => {
	reducedMotionMatches = null;
	vi.restoreAllMocks();
	cleanup();
	delete (window as unknown as Record<string, unknown>).bubble;
});

function pushBubbleConfig(cfg: Record<string, unknown>) {
	const cbs = (
		mockBubble as unknown as {
			_listeners: { config: Array<(c: Record<string, unknown>) => void> };
		}
	)._listeners.config;
	act(() => {
		for (const cb of cbs) cb(cfg);
	});
}

function setBubbleState(state: unknown) {
	const cbs = mockBubble._listeners.setState ?? [];
	act(() => {
		for (const cb of cbs) {
			(cb as (s: unknown) => void)(state);
		}
	});
}

function triggerHide() {
	const cbs = mockBubble._listeners.hide ?? [];
	act(() => {
		for (const cb of cbs) {
			(cb as () => void)();
		}
	});
}

describe("bubble: prefers-reduced-motion", () => {
	it("does NOT schedule new rAF frames when prefers-reduced-motion: reduce", async () => {
		reducedMotionMatches = true;
		const rafSpy = vi.spyOn(window, "requestAnimationFrame");

		render(<Bubble />);

		// Drain any pending microtasks / rAF callbacks. jsdom implements
		// rAF as setTimeout(0), so flushing the macrotask queue runs
		// all pending frames.
		await act(async () => {
			await new Promise<void>((resolve) => setTimeout(resolve, 0));
		});

		// The wake() call on mount enters the reduced-motion branch and
		// does NOT schedule a rAF. So rafSpy should have ZERO calls
		// after mount (the wake()'s internal requestAnimationFrame is
		// never reached because the early-return fires first).
		const callsAfterMount = rafSpy.mock.calls.length;

		// Tick more frames to be sure no further rAFs are scheduled.
		await act(async () => {
			await new Promise<void>((resolve) => setTimeout(resolve, 0));
		});
		expect(rafSpy.mock.calls.length).toBe(callsAfterMount);

		rafSpy.mockRestore();
	});

	it("renders bars at a fixed mid-height (no animation) under reduced-motion", async () => {
		reducedMotionMatches = true;

		render(<Bubble />);

		// Drain pending rAF callbacks so renderReducedMotion() runs.
		await act(async () => {
			await new Promise<void>((resolve) => setTimeout(resolve, 0));
		});

		// The visualizer bars are the 7 spans inside .gap-0.75. They
		// should all be at the fixed mid-height (13.5px) with opacity
		// 0.5 — the reduced-motion fallback render (matches the
		// reduced-motion gating contract: static mid-height bars at
		// opacity 0.5, see useAudioLevels-reduced-motion.test.tsx).
		const bars = document.querySelectorAll(".gap-0\\.75 > span");
		expect(bars.length).toBe(7);
		for (const bar of Array.from(bars)) {
			const el = bar as HTMLElement;
			expect(el.style.height).toBe("13.5px");
			expect(el.style.opacity).toBe("0.5");
		}
	});
});

describe("bubble: dir sync from bubble:config locale", () => {
	it("sets dir=rtl when cfg.locale is 'ar'", () => {
		render(<Bubble />);

		pushBubbleConfig({ locale: "ar" });

		expect(document.documentElement.dir).toBe("rtl");
	});

	it("sets dir=ltr when cfg.locale is a non-RTL locale", () => {
		// First push ar to flip to rtl, then push en to flip back.
		render(<Bubble />);

		pushBubbleConfig({ locale: "ar" });
		expect(document.documentElement.dir).toBe("rtl");

		pushBubbleConfig({ locale: "en" });
		expect(document.documentElement.dir).toBe("ltr");
	});

	it("does NOT change dir when cfg.locale is missing or unknown", () => {
		document.documentElement.dir = "rtl"; // simulate a stale state

		render(<Bubble />);

		pushBubbleConfig({ bubble_behavior: "show_on_record" });
		expect(document.documentElement.dir).toBe("rtl"); // unchanged

		pushBubbleConfig({ locale: "xx" }); // not a supported locale
		expect(document.documentElement.dir).toBe("rtl"); // unchanged
	});
});

describe("bubble: error message from bubble:set-state payload", () => {
	it("surfaces the message string in error mode (object payload)", () => {
		render(<Bubble />);

		setBubbleState({ state: "error", message: "Mic permission denied" });

		// The error pill should contain both the "⚠ Error" label and
		// the short reason string.
		expect(screen.getByText(/⚠ Error/)).toBeTruthy();
		expect(screen.getByText(/Mic permission denied/)).toBeTruthy();
	});

	it("falls back to label-only when the payload is a plain string", () => {
		render(<Bubble />);

		setBubbleState("error");

		expect(screen.getByText(/⚠ Error/)).toBeTruthy();
		// No ": <message>" suffix when no message was provided.
		const errorText = screen.getByText(/⚠ Error/).textContent ?? "";
		expect(errorText).not.toContain(":");
	});

	it("clears the message when transitioning out of error mode", () => {
		render(<Bubble />);

		setBubbleState({ state: "error", message: "Mic permission denied" });
		expect(screen.getByText(/Mic permission denied/)).toBeTruthy();

		// Transition back to recording — the message should clear.
		setBubbleState("recording");
		expect(screen.queryByText(/Mic permission denied/)).toBeNull();
	});
});

describe("bubble: error-mode auto-hide (show_on_record)", () => {
	it("triggers the exit animation after ERROR_AUTO_HIDE_MS in show_on_record", () => {
		vi.useFakeTimers();
		render(<Bubble />);

		// Default behavior is show_on_record (no config push yet).
		setBubbleState("error");

		// The exit animation should NOT have fired yet.
		const output = document.querySelector('output[aria-live="polite"]');
		expect(output?.className).not.toContain("animate-bubble-exit");

		// Advance past the 7s auto-hide threshold.
		act(() => {
			vi.advanceTimersByTime(7000);
		});

		// The exit animation class should now be applied.
		const outputAfter = document.querySelector('output[aria-live="polite"]');
		expect(outputAfter?.className).toContain("animate-bubble-exit");

		vi.useRealTimers();
	});

	it("does NOT auto-hide in always_visible mode (sticky until user dismisses)", () => {
		vi.useFakeTimers();
		render(<Bubble />);

		// Push always_visible config so the bubble is sticky.
		pushBubbleConfig({
			bubble_behavior: "always_visible",
			bubble_click_to_toggle: true,
			bubble_mic_button: true,
		});
		setBubbleState("error");

		// Advance well past the 7s threshold.
		act(() => {
			vi.advanceTimersByTime(20000);
		});

		// The exit animation should NOT have fired (always_visible is sticky).
		const output = document.querySelector('output[aria-live="polite"]');
		expect(output?.className).not.toContain("animate-bubble-exit");

		vi.useRealTimers();
	});
});

describe("bubble: recording interrupts fading→exit transition", () => {
	it("cancels the pending exit and restores enter animation when recording arrives during fading", () => {
		render(<Bubble />);

		// Drive into transcribing, then trigger hide to enter fading.
		setBubbleState("transcribing");
		expect(screen.getByText("Transcribing")).toBeTruthy();

		triggerHide(); // transcribing → fading

		// Now send recording — this should interrupt the fading→exit
		// transition: zero exitTick, restore enter animation, switch
		// mode to recording.
		setBubbleState("recording");

		// The bubble should now be in recording mode (bars visible).
		const bars = document.querySelectorAll(".gap-0\\.75 > span");
		expect(bars.length).toBe(7);

		// The enter animation class should be applied (the bubble is
		// re-arming after the interrupt).
		const output = document.querySelector('output[aria-live="polite"]');
		expect(output?.className).toContain("animate-bubble-enter");

		// hideComplete should NOT have been called — the exit was
		// cancelled.
		expect(mockBubble.hideComplete).not.toHaveBeenCalled();
	});
});
