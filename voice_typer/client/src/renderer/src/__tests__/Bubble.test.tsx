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
	const listeners: Record<string, Array<(...args: unknown[]) => void>> = {};
	return {
		onLevel: vi.fn(() => vi.fn()),
		onShow: vi.fn((cb: () => void) => {
			listeners.show = listeners.show ?? [];
			listeners.show.push(cb);
			return () => {
				listeners.show = listeners.show.filter((l) => l !== cb);
			};
		}),
		onHide: vi.fn((cb: () => void) => {
			listeners.hide = listeners.hide ?? [];
			listeners.hide.push(cb);
			return () => {
				listeners.hide = listeners.hide.filter((l) => l !== cb);
			};
		}),
		onSetState: vi.fn((cb: (state: string) => void) => {
			// biome-ignore lint/suspicious/noExplicitAny: mock setup — type flexibility needed for multi-callback list
			(listeners.setState as any) = listeners.setState ?? [];
			// biome-ignore lint/suspicious/noExplicitAny: mock setup — same reason
			(listeners.setState as any[]).push(cb as any);
			return () => {
				listeners.setState = (listeners.setState ?? []).filter((l) => l !== cb);
			};
		}),
		onDraggable: vi.fn(() => vi.fn()),
		signalReady: vi.fn(),
		hideComplete: vi.fn(),
		resizeTo: vi.fn(),
		moveBy: vi.fn(),
		_listeners: listeners,
	};
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
		// inside a flex container with gap-[3px]
		const bars = document.querySelectorAll(".gap-\\[3px\\] > span");
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
		const barsBefore = document.querySelectorAll(".gap-\\[3px\\] > span");
		expect(barsBefore.length).toBe(7);

		setBubbleState("transcribing");

		// Bars should no longer be rendered
		const barsAfter = document.querySelectorAll(".gap-\\[3px\\] > span");
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
		const bars = document.querySelectorAll(".gap-\\[3px\\] > span");
		expect(bars.length).toBe(0);

		// No transcribing text
		expect(screen.queryByText("Transcribing")).toBeNull();
	});

	it("transitions through all three modes in sequence", () => {
		render(<Bubble />);

		// Start: recording mode (bars visible)
		expect(document.querySelectorAll(".gap-\\[3px\\] > span").length).toBe(7);

		// Transcribing: text visible, bars hidden
		setBubbleState("transcribing");
		expect(screen.getByText("Transcribing")).toBeTruthy();
		expect(document.querySelectorAll(".gap-\\[3px\\] > span").length).toBe(0);

		// Idle: nothing visible
		setBubbleState("idle");
		expect(screen.queryByText("Transcribing")).toBeNull();
		expect(document.querySelectorAll(".gap-\\[3px\\] > span").length).toBe(0);

		// Back to recording: bars visible again
		setBubbleState("recording");
		expect(document.querySelectorAll(".gap-\\[3px\\] > span").length).toBe(7);
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
});
