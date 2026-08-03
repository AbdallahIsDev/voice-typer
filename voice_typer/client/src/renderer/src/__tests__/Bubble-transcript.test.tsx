/**
 * Tests for the bubble overlay's live-transcript display (XA-6-2) +
 * in-bubble stop / retry affordances (XA-6-1, XA-6-13, XA-6-19).
 *
 * Coverage:
 *   - Live partial-transcript text rendered inside the pill when the
 *     `bubble:set-state` payload carries a `transcript` field and the
 *     bubble is in `transcribing` (or `fading`) mode. Forward-
 *     compatible with the existing string-only payload — when no
 *     `transcript` is provided, the pill renders only the
 *     "Transcribing" label + animated dots (existing tests still pass).
 *   - Transcript truncation at 60 characters with an ellipsis.
 *   - Transcript preserved across the transcribing → fading transition
 *     so the partial text fades out smoothly with the pill.
 *   - Transcript cleared when leaving transcribing mode for any other
 *     non-fading state (recording / idle / error).
 *   - Stop button (recording mode) calls `toggleDictation` IPC.
 *   - Retry button (error mode) calls `toggleDictation` IPC.
 *
 * Mock pattern mirrors `Bubble.test.tsx` / `bubble-fixes.test.tsx` —
 * the listener arrays are populated by the `onSetState` / `onShow` /
 * `onHide` / `onConfig` mock subscriptions and driven by the helpers
 * below.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Bubble } from "@/Bubble";

// ── Mock window.bubble API ──────────────────────────────────────────
function makeMockBubble() {
	const listeners: {
		show: Array<() => void>;
		hide: Array<() => void>;
		setState: Array<(state: unknown) => void>;
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
		onConfig: vi.fn((cb: (cfg: Record<string, unknown>) => void) => {
			listeners.config.push(cb);
			return () => {
				listeners.config = listeners.config.filter((l) => l !== cb);
			};
		}),
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
		configurable: true,
	});

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

// ── Helpers ─────────────────────────────────────────────────────────

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

// ── Live transcript display (XA-6-2) ────────────────────────────────

describe("bubble: live transcript preview (XA-6-2)", () => {
	it("renders the transcript text in transcribing mode when the payload carries one", () => {
		render(<Bubble />);

		setBubbleState({ state: "transcribing", transcript: "hello world" });

		// The "Transcribing" status label is still present.
		expect(screen.getByText("Transcribing")).toBeTruthy();
		// The partial transcript is rendered as a sibling span.
		expect(screen.getByText("hello world")).toBeTruthy();
	});

	it("does NOT render a transcript span when the payload is a plain string", () => {
		render(<Bubble />);

		setBubbleState("transcribing");

		// Legacy string-only payload — no transcript text.
		expect(screen.getByText("Transcribing")).toBeTruthy();
		// No aria-labelled transcript region.
		expect(screen.queryByLabelText("Live transcript preview")).toBeNull();
	});

	it("does NOT render a transcript span when transcript is empty string", () => {
		render(<Bubble />);

		setBubbleState({ state: "transcribing", transcript: "" });

		expect(screen.getByText("Transcribing")).toBeTruthy();
		expect(screen.queryByLabelText("Live transcript preview")).toBeNull();
	});

	it("exposes the transcript span with a localised aria-label", () => {
		render(<Bubble />);

		setBubbleState({ state: "transcribing", transcript: "hello" });

		const region = screen.getByLabelText("Live transcript preview");
		expect(region).toBeTruthy();
		expect(region.textContent).toBe("hello");
	});

	it("truncates a long transcript to 60 characters with an ellipsis", () => {
		render(<Bubble />);

		const long = "a".repeat(120);
		setBubbleState({ state: "transcribing", transcript: long });

		const region = screen.getByLabelText("Live transcript preview");
		// 59 chars + 1 ellipsis = 60 chars total.
		expect(region.textContent?.length).toBe(60);
		expect(region.textContent?.endsWith("…")).toBe(true);
	});

	it("preserves the transcript across the transcribing → fading transition", () => {
		render(<Bubble />);

		setBubbleState({ state: "transcribing", transcript: "fading text" });
		expect(screen.getByText("fading text")).toBeTruthy();

		// Trigger hide → transcribing switches to fading. The transcript
		// should still be rendered so it fades out smoothly with the pill.
		triggerHide();
		expect(screen.getByText("fading text")).toBeTruthy();
	});

	it("clears the transcript when transitioning to recording mode", () => {
		render(<Bubble />);

		setBubbleState({ state: "transcribing", transcript: "stale text" });
		expect(screen.getByText("stale text")).toBeTruthy();

		setBubbleState("recording");
		expect(screen.queryByText("stale text")).toBeNull();
		expect(screen.queryByLabelText("Live transcript preview")).toBeNull();
	});

	it("clears the transcript when transitioning to idle mode", () => {
		render(<Bubble />);

		setBubbleState({ state: "transcribing", transcript: "stale text" });
		expect(screen.getByText("stale text")).toBeTruthy();

		setBubbleState("idle");
		expect(screen.queryByText("stale text")).toBeNull();
	});

	it("clears the transcript when transitioning to error mode", () => {
		render(<Bubble />);

		setBubbleState({ state: "transcribing", transcript: "stale text" });
		expect(screen.getByText("stale text")).toBeTruthy();

		setBubbleState("error");
		expect(screen.queryByText("stale text")).toBeNull();
	});

	it("updates the transcript when a new partial arrives mid-transcription", () => {
		render(<Bubble />);

		setBubbleState({ state: "transcribing", transcript: "first" });
		expect(screen.getByText("first")).toBeTruthy();

		setBubbleState({ state: "transcribing", transcript: "first second" });
		expect(screen.getByText("first second")).toBeTruthy();
		expect(screen.queryByText("first")).toBeNull();
	});
});

// ── Stop button (XA-6-1) ────────────────────────────────────────────

describe("bubble: in-bubble stop button (XA-6-1)", () => {
	it("renders a stop button in recording mode by default", () => {
		render(<Bubble />);

		// Default mode is "recording" — the stop affordance renders
		// independent of `always_visible` config.
		const btn = screen.getByLabelText("Stop recording");
		expect(btn).toBeTruthy();
		expect(btn.tagName).toBe("BUTTON");
	});

	it("clicking the stop button calls toggleDictation IPC", () => {
		render(<Bubble />);

		const btn = screen.getByLabelText("Stop recording");
		act(() => {
			btn.click();
		});

		expect(mockBubble.toggleDictation).toHaveBeenCalledTimes(1);
	});
});

// ── Error retry affordance (XA-6-13, XA-6-19) ───────────────────────

describe("bubble: error retry affordance (XA-6-13)", () => {
	it("renders a retry button in error mode", () => {
		render(<Bubble />);

		setBubbleState("error");

		const btn = screen.getByLabelText("Retry transcription");
		expect(btn).toBeTruthy();
		expect(btn.tagName).toBe("BUTTON");
	});

	it("clicking the retry button calls toggleDictation IPC", () => {
		render(<Bubble />);

		setBubbleState("error");

		const btn = screen.getByLabelText("Retry transcription");
		act(() => {
			btn.click();
		});

		expect(mockBubble.toggleDictation).toHaveBeenCalledTimes(1);
	});

	it("surfaces the error message alongside the retry button", () => {
		render(<Bubble />);

		setBubbleState({ state: "error", message: "Mic permission denied" });

		expect(screen.getByText(/⚠ Error/)).toBeTruthy();
		expect(screen.getByText(/Mic permission denied/)).toBeTruthy();
		expect(screen.getByLabelText("Retry transcription")).toBeTruthy();
	});

	it("clears the error message when transitioning out of error mode", () => {
		render(<Bubble />);

		setBubbleState({ state: "error", message: "Mic permission denied" });
		expect(screen.getByText(/Mic permission denied/)).toBeTruthy();

		setBubbleState("recording");
		expect(screen.queryByText(/Mic permission denied/)).toBeNull();
	});
});
