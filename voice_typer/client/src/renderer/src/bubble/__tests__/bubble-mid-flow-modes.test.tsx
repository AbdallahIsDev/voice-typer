import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Bubble } from "@/Bubble";

// ── Mock window.bubble API ──────────────────────────────────────────
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

function setBubbleState(state: string) {
	const cbs = mockBubble._listeners.setState ?? [];
	act(() => {
		for (const cb of cbs) {
			(cb as (s: string) => void)(state);
		}
	});
}

describe("bubble mid-flow modes (blocked / cancelling / permission_revoked / paste_failed)", () => {
	it("renders the 'Blocked' label when state becomes 'blocked'", () => {
		render(<Bubble />);

		// Default mode is recording (visualizer bars), no Blocked label.
		expect(screen.queryByText("Blocked")).toBeNull();

		setBubbleState("blocked");

		expect(screen.getByText("Blocked")).toBeTruthy();
	});

	it("renders the 'Cancelling…' label when state becomes 'cancelling'", () => {
		render(<Bubble />);

		expect(screen.queryByText("Cancelling…")).toBeNull();

		setBubbleState("cancelling");

		expect(screen.getByText("Cancelling…")).toBeTruthy();
	});

	it("renders the 'Mic permission revoked' label when state becomes 'permission_revoked'", () => {
		render(<Bubble />);

		expect(screen.queryByText("Mic permission revoked")).toBeNull();

		setBubbleState("permission_revoked");

		expect(screen.getByText("Mic permission revoked")).toBeTruthy();
	});

	it("renders the 'Paste failed' label when state becomes 'paste_failed'", () => {
		render(<Bubble />);

		expect(screen.queryByText("Paste failed")).toBeNull();

		setBubbleState("paste_failed");

		expect(screen.getByText("Paste failed")).toBeTruthy();
	});

	it("sets a distinctive aria-label for each new mode", () => {
		render(<Bubble />);

		const output = document.querySelector('output[aria-live="polite"]');
		expect(output).toBeTruthy();

		// blocked
		setBubbleState("blocked");
		expect(output?.getAttribute("aria-label")).toBe("Lausu blocked indicator");

		// cancelling
		setBubbleState("cancelling");
		expect(output?.getAttribute("aria-label")).toBe(
			"Lausu cancelling indicator",
		);

		// permission_revoked
		setBubbleState("permission_revoked");
		expect(output?.getAttribute("aria-label")).toBe(
			"Lausu microphone permission revoked indicator",
		);

		// paste_failed
		setBubbleState("paste_failed");
		expect(output?.getAttribute("aria-label")).toBe(
			"Lausu paste failed indicator",
		);
	});

	it("falls back to recording mode when state becomes 'recording' after a mid-flow mode", () => {
		render(<Bubble />);

		// Enter a mid-flow mode.
		setBubbleState("blocked");
		expect(screen.getByText("Blocked")).toBeTruthy();

		// Back to recording, Blocked label should disappear.
		setBubbleState("recording");
		expect(screen.queryByText("Blocked")).toBeNull();
	});

	it("does NOT render bars in any of the new mid-flow modes", () => {
		render(<Bubble />);

		// Default recording mode → 7 bars visible.
		expect(document.querySelectorAll(".gap-0\\.75 > span").length).toBe(7);

		// Each new mode should hide the bars (no visualizer in mid-flow).
		for (const state of [
			"blocked",
			"cancelling",
			"permission_revoked",
			"paste_failed",
		]) {
			setBubbleState(state);
			expect(document.querySelectorAll(".gap-0\\.75 > span").length).toBe(0);
		}
	});
});
