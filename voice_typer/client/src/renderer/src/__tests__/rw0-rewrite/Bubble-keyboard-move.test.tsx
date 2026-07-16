/**
 * RW-0 vitest rewrite — behavioral tests for `Bubble.tsx` keyboard move.
 *
 * Replaces the following string-pattern Python tests from
 * `tests/test_ux_components.py`:
 *   - TestBubbleSupportsKeyboardArrowMove::test_bubble_calls_move_by
 *   - TestBubbleSupportsKeyboardArrowMove::test_bubble_respects_draggable_gate
 *
 * The Python tests asserted on substring presence inside `Bubble.tsx`
 * (e.g. `"moveBy" in bubble`, `"if (!draggable) return" in bubble`).
 * These pass even when the handler is dead code, and they fail on
 * innocent refactors.  The vitest versions below mount the real
 * Bubble component, dispatch realistic KeyboardEvents to `window`,
 * and assert the `window.bubble.moveBy` mock is called (or not).
 *
 * The corresponding Python tests are skipped via `@pytest.mark.skip`
 * with a pointer back to this file.  They are NOT deleted.
 */
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Bubble } from "@/Bubble";

// ── Mock window.bubble API ──────────────────────────────────────────
// Bubble.tsx subscribes to window.bubble.onLevel, onShow, onHide,
// onSetState, onDraggable and calls window.bubble.moveBy / resizeTo /
// hideComplete.  We provide stubs so the component mounts without
// crashing and so we can observe moveBy calls.

function makeMockBubble() {
	return {
		onLevel: vi.fn(() => vi.fn()),
		onShow: vi.fn(() => vi.fn()),
		onHide: vi.fn(() => vi.fn()),
		onSetState: vi.fn(() => vi.fn()),
		onDraggable: vi.fn((cb: (draggable: boolean) => void) => {
			// Default to draggable=true so the keyboard handler
			// is active by default.  Tests that need to flip
			// draggable off call _setDraggable(false).
			cb(true);
			return vi.fn();
		}),
		signalReady: vi.fn(),
		hideComplete: vi.fn(),
		resizeTo: vi.fn(),
		moveBy: vi.fn(),
	};
}

interface MockBubble {
	onLevel: ReturnType<typeof vi.fn>;
	onShow: ReturnType<typeof vi.fn>;
	onHide: ReturnType<typeof vi.fn>;
	onSetState: ReturnType<typeof vi.fn>;
	onDraggable: ReturnType<typeof vi.fn>;
	signalReady: ReturnType<typeof vi.fn>;
	hideComplete: ReturnType<typeof vi.fn>;
	resizeTo: ReturnType<typeof vi.fn>;
	moveBy: ReturnType<typeof vi.fn>;
}

let mockBubble: MockBubble;

beforeEach(() => {
	mockBubble = makeMockBubble();
	(window as unknown as Record<string, unknown>).bubble = mockBubble;

	// Stub window.matchMedia for jsdom (used by useThemeSync in Bubble.tsx).
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

function dispatchArrowKey(key: string, opts: { shiftKey?: boolean } = {}) {
	const ev = new KeyboardEvent("keydown", {
		key,
		bubbles: true,
		cancelable: true,
		shiftKey: opts.shiftKey ?? false,
	});
	act(() => {
		window.dispatchEvent(ev);
	});
}

describe("Bubble keyboard move — RW-0 rewrite of test_bubble_calls_move_by", () => {
	it("calls moveBy with negative deltaX on ArrowLeft", () => {
		render(<Bubble />);
		mockBubble.moveBy.mockClear();

		dispatchArrowKey("ArrowLeft");

		expect(mockBubble.moveBy).toHaveBeenCalledTimes(1);
		const [deltaX, deltaY] = mockBubble.moveBy.mock.calls[0];
		expect(deltaX).toBeLessThan(0);
		expect(deltaY).toBe(0);
	});

	it("calls moveBy with positive deltaX on ArrowRight", () => {
		render(<Bubble />);
		mockBubble.moveBy.mockClear();

		dispatchArrowKey("ArrowRight");

		expect(mockBubble.moveBy).toHaveBeenCalledTimes(1);
		const [deltaX, deltaY] = mockBubble.moveBy.mock.calls[0];
		expect(deltaX).toBeGreaterThan(0);
		expect(deltaY).toBe(0);
	});

	it("calls moveBy with negative deltaY on ArrowUp", () => {
		render(<Bubble />);
		mockBubble.moveBy.mockClear();

		dispatchArrowKey("ArrowUp");

		expect(mockBubble.moveBy).toHaveBeenCalledTimes(1);
		const [deltaX, deltaY] = mockBubble.moveBy.mock.calls[0];
		expect(deltaX).toBe(0);
		expect(deltaY).toBeLessThan(0);
	});

	it("calls moveBy with positive deltaY on ArrowDown", () => {
		render(<Bubble />);
		mockBubble.moveBy.mockClear();

		dispatchArrowKey("ArrowDown");

		expect(mockBubble.moveBy).toHaveBeenCalledTimes(1);
		const [deltaX, deltaY] = mockBubble.moveBy.mock.calls[0];
		expect(deltaX).toBe(0);
		expect(deltaY).toBeGreaterThan(0);
	});

	it("uses a step of 1 when Shift is held (fine-grained move)", () => {
		render(<Bubble />);
		mockBubble.moveBy.mockClear();

		dispatchArrowKey("ArrowRight", { shiftKey: true });

		expect(mockBubble.moveBy).toHaveBeenCalledTimes(1);
		const [deltaX] = mockBubble.moveBy.mock.calls[0];
		// Shift step is 1; default step is 10.  Either way the
		// sign is positive for ArrowRight, but the magnitude
		// must match the shift-step.
		expect(Math.abs(deltaX)).toBe(1);
	});
});

describe("Bubble draggable gate — RW-0 rewrite of test_bubble_respects_draggable_gate", () => {
	it("does NOT call moveBy when draggable is false", () => {
		render(<Bubble />);
		mockBubble.moveBy.mockClear();

		// Flip draggable to false via the onDraggable callback
		// captured at mount.
		const onDraggableCb = mockBubble.onDraggable.mock.calls[0]?.[0] as
			| ((d: boolean) => void)
			| undefined;
		expect(onDraggableCb).toBeTruthy();
		act(() => onDraggableCb?.(false));

		dispatchArrowKey("ArrowLeft");
		dispatchArrowKey("ArrowRight");
		dispatchArrowKey("ArrowUp");
		dispatchArrowKey("ArrowDown");

		expect(mockBubble.moveBy).not.toHaveBeenCalled();
	});

	it("resumes calling moveBy when draggable flips back to true", () => {
		render(<Bubble />);
		mockBubble.moveBy.mockClear();

		const onDraggableCb = mockBubble.onDraggable.mock.calls[0]?.[0] as
			| ((d: boolean) => void)
			| undefined;
		act(() => onDraggableCb?.(false));
		dispatchArrowKey("ArrowLeft");
		expect(mockBubble.moveBy).not.toHaveBeenCalled();

		act(() => onDraggableCb?.(true));
		dispatchArrowKey("ArrowLeft");
		expect(mockBubble.moveBy).toHaveBeenCalledTimes(1);
	});
});
