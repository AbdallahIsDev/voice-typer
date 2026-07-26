/**
 * Tests for the Bubble component (voice level visualiser + transcribing overlay).
 *
 * NEW-BUBBLE-TRANSCRIBING: The bubble has three visual modes:
 *   - "recording" — shows animated voice level bars (default)
 *   - "transcribing" — hides bars, shows "Transcribing…" text with animated dots
 *   - "idle" — empty pill (for always_visible mode)
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Bubble } from "@/Bubble";

// ── Mock window.bubble API ──────────────────────────────────────────
// Bubble.tsx uses window.bubble.onLevel, onShow, onHide, onSetState etc.
// We provide stubs so the component mounts without crashing.

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
		// UX-10: bubble config + mic-button toggle (sandboxed renderer).
		onConfig: vi.fn((cb: (cfg: Record<string, unknown>) => void) => {
			listeners.config = cb;
			return () => {
				listeners.config = undefined;
			};
		}),
		toggleDictation: vi.fn(),
		// BG-96: dismiss IPC send (sandboxed renderer).
		dismiss: vi.fn(),
		_listeners: listeners,
	};
}

// UX-10: helper to push bubble config (simulates the backend's
// bubble:config event). The Bubble subscribes via onConfig and shows
// the mic button only when always_visible + both toggles are on.
function pushBubbleConfig(cfg: Record<string, unknown>) {
	const cb = (
		mockBubble as unknown as {
			_listeners: { config?: (c: Record<string, unknown>) => void };
		}
	)._listeners.config;
	if (cb)
		act(() => {
			cb(cfg);
		});
}

let mockBubble: ReturnType<typeof makeMockBubble>;

beforeEach(() => {
	mockBubble = makeMockBubble();
	(window as unknown as Record<string, unknown>).bubble = mockBubble;

	// Stub window.matchMedia for jsdom (used by useThemeSync in Bubble.tsx)
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

/** Trigger an onShow/onHide callback, wrapped in act(). */
function triggerCallback(type: "show" | "hide") {
	const cbs = mockBubble._listeners[type] ?? [];
	act(() => {
		for (const cb of cbs) {
			(cb as () => void)();
		}
	});
}

describe("Bubble", () => {
	it("renders without crashing", () => {
		render(<Bubble />);
		const output = document.querySelector('output[aria-live="polite"]');
		expect(output).toBeTruthy();
	});

	it("renders recording visualizer bars by default", () => {
		render(<Bubble />);
		// Default mode is "recording", which shows 7 visualizer bar <span>s
		// inside a flex container with gap-0.75 (the Tailwind utility, was gap-[3px])
		const bars = document.querySelectorAll(".gap-0\\.75 > span");
		expect(bars.length).toBe(7);
	});

	it("shows transcribing state with text and animated dots when onSetState fires", () => {
		render(<Bubble />);

		setBubbleState("transcribing");

		expect(screen.getByText("Transcribing")).toBeTruthy();

		// Should show 3 animated bouncing dots
		const dots = document.querySelectorAll(".animate-bounce");
		expect(dots.length).toBe(3);
	});

	it("hides visualizer bars when in transcribing mode", () => {
		render(<Bubble />);

		// Bars should be visible initially
		const barsBefore = document.querySelectorAll(".gap-0\\.75 > span");
		expect(barsBefore.length).toBe(7);

		setBubbleState("transcribing");

		// Bars should no longer be rendered
		const barsAfter = document.querySelectorAll(".gap-0\\.75 > span");
		expect(barsAfter.length).toBe(0);
	});

	it("renders idle state as empty div", () => {
		render(<Bubble />);

		setBubbleState("idle");

		// Should render an empty flex container (h-6) but no bars and no text
		const emptyContainer = document.querySelector(".flex.h-6.items-center");
		expect(emptyContainer).toBeTruthy();
		expect(emptyContainer?.textContent?.trim()).toBe("");

		// No bars
		const bars = document.querySelectorAll(".gap-0\\.75 > span");
		expect(bars.length).toBe(0);

		// No transcribing text
		expect(screen.queryByText("Transcribing")).toBeNull();
	});

	it("transitions through all three modes in sequence", () => {
		render(<Bubble />);

		// Start: recording mode (bars visible)
		expect(document.querySelectorAll(".gap-0\\.75 > span").length).toBe(7);

		// Transcribing: text visible, bars hidden
		setBubbleState("transcribing");
		expect(screen.getByText("Transcribing")).toBeTruthy();
		expect(document.querySelectorAll(".gap-0\\.75 > span").length).toBe(0);

		// Idle: nothing visible
		setBubbleState("idle");
		expect(screen.queryByText("Transcribing")).toBeNull();
		expect(document.querySelectorAll(".gap-0\\.75 > span").length).toBe(0);

		// Back to recording: bars visible again
		setBubbleState("recording");
		expect(document.querySelectorAll(".gap-0\\.75 > span").length).toBe(7);
	});

	it("has accessible aria-label", () => {
		render(<Bubble />);
		const output = document.querySelector('output[aria-live="polite"]');
		expect(output?.getAttribute("aria-label")).toBe(
			"Voice Typer recording indicator",
		);
	});

	it("transcribing dots have staggered animation delays", () => {
		render(<Bubble />);

		setBubbleState("transcribing");

		const dots = document.querySelectorAll(".animate-bounce");
		expect(dots.length).toBe(3);

		// Each dot should have a different animation-delay
		const delays = Array.from(dots).map(
			(el) => (el as HTMLElement).style.animationDelay,
		);
		expect(new Set(delays).size).toBe(3); // All three delays are unique
	});

	it("switches back to transcribing from idle when called again", () => {
		render(<Bubble />);

		setBubbleState("idle");
		expect(screen.queryByText("Transcribing")).toBeNull();

		setBubbleState("transcribing");
		expect(screen.getByText("Transcribing")).toBeTruthy();
	});

	it("triggers enter animation on onShow callback", () => {
		render(<Bubble />);

		triggerCallback("show");

		const output = document.querySelector('output[aria-live="polite"]');
		expect(output?.className).toContain("animate-bubble-enter");
	});

	it("triggers exit animation on onHide callback", () => {
		render(<Bubble />);

		triggerCallback("hide");

		const output = document.querySelector('output[aria-live="polite"]');
		expect(output?.className).toContain("animate-bubble-exit");
	});

	// ── UX-10: mic button (always_visible + enabled) ──────────────

	it("does NOT show a mic button by default (no config received)", () => {
		render(<Bubble />);
		// Without a bubble:config push, the button must stay hidden.
		expect(screen.queryByLabelText("Start dictation")).toBeNull();
		expect(screen.queryByLabelText("Stop dictation")).toBeNull();
	});

	it("shows a mic button when always_visible + both toggles are on", () => {
		render(<Bubble />);

		pushBubbleConfig({
			bubble_behavior: "always_visible",
			bubble_click_to_toggle: true,
			bubble_mic_button: true,
		});

		// Default mode is "recording", so the stop affordance shows.
		const btn = screen.getByLabelText("Stop dictation");
		expect(btn).toBeTruthy();
		// It is clickable (not a dead pill).
		expect(btn.tagName).toBe("BUTTON");
	});

	it("hides the mic button when bubble_mic_button is false", () => {
		render(<Bubble />);

		pushBubbleConfig({
			bubble_behavior: "always_visible",
			bubble_click_to_toggle: true,
			bubble_mic_button: false,
		});

		expect(screen.queryByLabelText("Start dictation")).toBeNull();
		expect(screen.queryByLabelText("Stop dictation")).toBeNull();
	});

	it("hides the mic button when bubble_behavior is show_on_record", () => {
		render(<Bubble />);

		pushBubbleConfig({
			bubble_behavior: "show_on_record",
			bubble_click_to_toggle: true,
			bubble_mic_button: true,
		});

		expect(screen.queryByLabelText("Start dictation")).toBeNull();
		expect(screen.queryByLabelText("Stop dictation")).toBeNull();
	});

	it("clicking the mic button calls toggleDictation (UX-10 fix)", () => {
		render(<Bubble />);

		pushBubbleConfig({
			bubble_behavior: "always_visible",
			bubble_click_to_toggle: true,
			bubble_mic_button: true,
		});

		const btn = screen.getByLabelText("Stop dictation");
		act(() => {
			btn.click();
		});

		expect(mockBubble.toggleDictation).toHaveBeenCalledTimes(1);
	});

	it("toggles the aria label between start/stop as recording state changes", () => {
		render(<Bubble />);

		pushBubbleConfig({
			bubble_behavior: "always_visible",
			bubble_click_to_toggle: true,
			bubble_mic_button: true,
		});

		// Recording → stop affordance.
		expect(screen.getByLabelText("Stop dictation")).toBeTruthy();

		// Go idle (not recording) → start affordance.
		setBubbleState("idle");
		expect(screen.getByLabelText("Start dictation")).toBeTruthy();
		expect(screen.queryByLabelText("Stop dictation")).toBeNull();
	});

	// ── BG-95: state-aware aria-label on outer <output> ──────────

	it("BG-95: aria-label reflects recording mode by default", () => {
		render(<Bubble />);
		const output = document.querySelector('output[aria-live="polite"]');
		// Default mode is "recording".
		expect(output?.getAttribute("aria-label")).toBe(
			"Voice Typer recording indicator",
		);
	});

	it("BG-95: aria-label switches to transcribing indicator when mode changes", () => {
		render(<Bubble />);

		setBubbleState("transcribing");

		const output = document.querySelector('output[aria-live="polite"]');
		expect(output?.getAttribute("aria-label")).toBe(
			"Voice Typer transcribing indicator",
		);
	});

	it("BG-95: aria-label switches to error indicator in error mode", () => {
		render(<Bubble />);

		setBubbleState("error");

		const output = document.querySelector('output[aria-live="polite"]');
		expect(output?.getAttribute("aria-label")).toBe(
			"Voice Typer error indicator",
		);
	});

	it("BG-95: aria-label switches to idle indicator in idle mode", () => {
		render(<Bubble />);

		setBubbleState("idle");

		const output = document.querySelector('output[aria-live="polite"]');
		expect(output?.getAttribute("aria-label")).toBe(
			"Voice Typer idle indicator",
		);
	});

	// ── BG-96: dismiss '×' button ─────────────────────────────────

	it("BG-96: does NOT show a dismiss button by default (no config received)", () => {
		render(<Bubble />);
		// Without a bubble:config push, the dismiss button must stay hidden.
		expect(screen.queryByLabelText("Dismiss bubble")).toBeNull();
	});

	it("BG-96: shows a dismiss button when bubble_behavior is always_visible", () => {
		render(<Bubble />);

		pushBubbleConfig({
			bubble_behavior: "always_visible",
			bubble_click_to_toggle: true,
			bubble_mic_button: true,
		});

		const btn = screen.getByLabelText("Dismiss bubble");
		expect(btn).toBeTruthy();
		expect(btn.tagName).toBe("BUTTON");
	});

	it("BG-96: shows a dismiss button in always_visible mode even when mic_button is off", () => {
		render(<Bubble />);

		pushBubbleConfig({
			bubble_behavior: "always_visible",
			bubble_click_to_toggle: true,
			bubble_mic_button: false,
		});

		// Mic button should be hidden, but dismiss button still shown
		// (always_visible bubble needs a manual dismiss affordance).
		expect(screen.queryByLabelText("Start dictation")).toBeNull();
		expect(screen.queryByLabelText("Stop dictation")).toBeNull();
		expect(screen.getByLabelText("Dismiss bubble")).toBeTruthy();
	});

	it("BG-96: hides the dismiss button when bubble_behavior is show_on_record", () => {
		render(<Bubble />);

		pushBubbleConfig({
			bubble_behavior: "show_on_record",
			bubble_click_to_toggle: true,
			bubble_mic_button: true,
		});

		// show_on_record auto-hides when recording stops, so no
		// manual dismiss affordance is needed.
		expect(screen.queryByLabelText("Dismiss bubble")).toBeNull();
	});

	it("BG-96: clicking the dismiss button calls window.bubble.dismiss", () => {
		render(<Bubble />);

		pushBubbleConfig({
			bubble_behavior: "always_visible",
			bubble_click_to_toggle: true,
			bubble_mic_button: true,
		});

		const btn = screen.getByLabelText("Dismiss bubble");
		act(() => {
			btn.click();
		});

		expect(mockBubble.dismiss).toHaveBeenCalledTimes(1);
	});
});
