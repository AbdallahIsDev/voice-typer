/**
 * F-17: axe-core automated WCAG scan for the Bubble overlay component.
 *
 * The existing `a11y/axe-core.test.tsx` covers the top-level pages but
 * skips the Bubble overlay entirely (the Bubble runs in a separate,
 * sandboxed BrowserWindow with its own renderer entry — it is not part
 * of the App's render graph). This file fills that gap by mounting the
 * real `<Bubble>` in each of its five `BubbleMode` values and running
 * axe-core against the rendered container.
 *
 * Modes covered:
 *   - `recording`   — default mode; visualiser bars + (when configured)
 *                     the mic/stop/dismiss affordances.
 *   - `transcribing` — "Transcribing…" label + three animated dots.
 *   - `fading`      — brief transition between `transcribing` and the
 *                     exit animation; produced by `onHide` firing while
 *                     the bubble is in `transcribing` mode.
 *   - `idle`        — empty pill with an sr-only "Transcription complete"
 *                     announcement (always_visible mode).
 *   - `error`       — red "⚠ Error" label + retry affordance.
 *
 * The color-contrast rule is disabled because the test environment
 * doesn't load the full Tailwind stylesheet (same approach as
 * `a11y/axe-core.test.tsx`).
 */
import { act, cleanup, render } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Bubble } from "@/Bubble";

// Disable color-contrast — the test environment doesn't load the full
// Tailwind stylesheet, so axe's computed contrast values would be
// meaningless and produce false positives.
const AXE_OPTIONS: axe.RunOptions = {
	rules: {
		"color-contrast": { enabled: false },
	},
};

// ── Mock window.bubble API ──────────────────────────────────────────
// Bubble.tsx uses window.bubble.onLevel, onShow, onHide, onSetState etc.
// We provide stubs so the component mounts without crashing. The
// listeners object lets the test reach into the mock and dispatch
// state-show / state-hide events to drive the Bubble through its
// various modes (mirrors the pattern in Bubble.test.tsx).
function makeMockBubble() {
	const listeners: {
		show: Array<() => void>;
		hide: Array<() => void>;
		setState: Array<(state: string) => void>;
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
		onConfig: vi.fn(() => vi.fn()),
		signalReady: vi.fn(),
		hideComplete: vi.fn(),
		resizeTo: vi.fn(),
		moveBy: vi.fn(),
		toggleDictation: vi.fn(),
		dismiss: vi.fn(),
		_listeners: listeners,
	};
}

let mockBubble: ReturnType<typeof makeMockBubble>;

beforeEach(() => {
	mockBubble = makeMockBubble();
	(window as unknown as Record<string, unknown>).bubble = mockBubble;
});

afterEach(() => {
	cleanup();
	delete (window as unknown as Record<string, unknown>).bubble;
});

// ── Helpers ─────────────────────────────────────────────────────────

/** Trigger an onSetState callback with the given state, wrapped in act(). */
function setBubbleState(state: string) {
	const cbs = mockBubble._listeners.setState ?? [];
	act(() => {
		for (const cb of cbs) {
			(cb as (s: string) => void)(state);
		}
	});
}

/** Trigger an onHide callback, wrapped in act(). */
function triggerHide() {
	const cbs = mockBubble._listeners.hide ?? [];
	act(() => {
		for (const cb of cbs) {
			(cb as () => void)();
		}
	});
}

/** Axe helper — filters out the disabled color-contrast rule. */
async function expectNoAxeViolations(container: HTMLElement): Promise<void> {
	const results = await axe.run(container, AXE_OPTIONS);
	const violations = results.violations.filter(
		(v) => v.id !== "color-contrast",
	);
	expect(violations).toEqual([]);
}

describe("F-17: axe-core WCAG scan — Bubble overlay (all five modes)", () => {
	it("recording mode: no axe violations", async () => {
		// Default mode after mount is "recording" — no state change needed.
		const { container } = render(<Bubble />);
		await expectNoAxeViolations(container);
	});

	it("transcribing mode: no axe violations", async () => {
		const { container } = render(<Bubble />);
		setBubbleState("transcribing");
		await expectNoAxeViolations(container);
	});

	it("fading mode (transcribing → onHide): no axe violations", async () => {
		// `fading` is the brief transition state the state machine enters
		// when `onHide` fires while the bubble is in `transcribing` mode.
		// See useBubbleStateMachine.ts → onHide handler.
		const { container } = render(<Bubble />);
		setBubbleState("transcribing");
		triggerHide();
		await expectNoAxeViolations(container);
	});

	it("idle mode: no axe violations", async () => {
		const { container } = render(<Bubble />);
		setBubbleState("idle");
		await expectNoAxeViolations(container);
	});

	it("error mode: no axe violations", async () => {
		const { container } = render(<Bubble />);
		setBubbleState("error");
		await expectNoAxeViolations(container);
	});
});
