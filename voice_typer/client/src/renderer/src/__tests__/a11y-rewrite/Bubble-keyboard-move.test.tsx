/**
 *  vitest rewrite — behavioral tests for `Bubble.tsx` keyboard move.
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
 *
 *  (Sub-agent 16): the original  vitest tests below
 * asserted the renderer-side `window.addEventListener("keydown", ...)`
 * handler in `Bubble.tsx` was called on arrow keys.  That handler was
 * DEAD CODE in production because the bubble BrowserWindow is created
 * with `focusable: false` (see
 * `voice_typer/client/src/main/windows/bubble-window.ts`), so the
 * renderer never receives keyboard focus and window-level `keydown`
 * events never fire in the shipped app.  Agent 12 ( + )
 * removed the handler from `Bubble.tsx` entirely.
 *
 *  DECISION (option b — document as mouse-drag-only): the
 * keyboard-move feature was DELIBERATELY NOT RE-IMPLEMENTED.  The
 * bubble is now documented in user-facing help as mouse-drag-only.
 * This is a deliberate product decision (see the  comment block
 * at the top of `Bubble.tsx` for the rationale).  The main-process
 * `bubble:move-by` IPC handler is preserved so a future product
 * change can wire a global hotkey without renderer work.
 *
 * The original 7 vitest tests are kept below as `it.skip` placeholders
 * so the test names still appear in the runner output as a historical
 * record of what the  rewrite covered.  Two NEW test blocks
 * replace them:
 *
 *   1. `: Bubble keyboard-move is dead code in production`
 *      — scans `bubble-window.ts` and asserts `focusable: false` is
 *        still set, prints a loud warning, and verifies `Bubble.tsx`
 *        carries the dead-code comment block.  This guards against a
 *        future refactor that flips `focusable` to `true` without
 *        also re-adding the keyboard-move handler.
 *
 *   2. `Item 7: Bubble renders sr-only 'Transcription complete.'`
 *      — mounts the Bubble in idle mode and asserts the sr-only span
 *        containing `t("a11y.transcriptionComplete")` is rendered to
 *        the DOM, so screen-reader users hear the completion
 *        announcement when the bubble transitions from
 *        transcribing → idle.
 */
import fs from "node:fs";
import path from "node:path";
import { act, cleanup, render, screen } from "@testing-library/react";
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

//DECISION: the original  tests below are SKIPPED because the
// keyboard-move feature was DELIBERATELY NOT RE-IMPLEMENTED.  The
// renderer-side `window.addEventListener("keydown", ...)` handler was
//removed by agent 12 ( + ) because it was dead code in
// production (the bubble BrowserWindow is `focusable: false`, so
// renderer keydown events never fire in the shipped app).
//
//option (b) was chosen: document the bubble as mouse-drag-only
// rather than add a MAIN-PROCESS global hotkey.  The `bubble:move-by`
// IPC handler in `main/ipc/bubble-handlers.ts` is preserved so a future
// product decision can wire a global hotkey without renderer work.
//
//The test names are preserved as `it.skip` placeholders so the
// coverage map stays readable; flip them back to `it` ONLY if a
// renderer-side keyboard-move handler is re-introduced (which also
// requires flipping `focusable: false` to `true` in `bubble-window.ts`
//see the  trade-off note in `bubble-components.tsx`).
describe.skip("Bubble keyboard move — RW-0 rewrite of test_bubble_calls_move_by (SKIPPED: BG-30 mouse-drag-only decision)", () => {
	it("calls moveBy with negative deltaX on ArrowLeft", () => {
		render(<Bubble />);
		mockBubble.moveBy.mockClear();

		dispatchArrowKey("ArrowLeft");

		expect(mockBubble.moveBy).toHaveBeenCalledTimes(1);
		const [deltaX, deltaY] = mockBubble.moveBy.mock.calls[0] ?? [];
		expect(deltaX).toBeLessThan(0);
		expect(deltaY).toBe(0);
	});

	it("calls moveBy with positive deltaX on ArrowRight", () => {
		render(<Bubble />);
		mockBubble.moveBy.mockClear();

		dispatchArrowKey("ArrowRight");

		expect(mockBubble.moveBy).toHaveBeenCalledTimes(1);
		const [deltaX, deltaY] = mockBubble.moveBy.mock.calls[0] ?? [];
		expect(deltaX).toBeGreaterThan(0);
		expect(deltaY).toBe(0);
	});

	it("calls moveBy with negative deltaY on ArrowUp", () => {
		render(<Bubble />);
		mockBubble.moveBy.mockClear();

		dispatchArrowKey("ArrowUp");

		expect(mockBubble.moveBy).toHaveBeenCalledTimes(1);
		const [deltaX, deltaY] = mockBubble.moveBy.mock.calls[0] ?? [];
		expect(deltaX).toBe(0);
		expect(deltaY).toBeLessThan(0);
	});

	it("calls moveBy with positive deltaY on ArrowDown", () => {
		render(<Bubble />);
		mockBubble.moveBy.mockClear();

		dispatchArrowKey("ArrowDown");

		expect(mockBubble.moveBy).toHaveBeenCalledTimes(1);
		const [deltaX, deltaY] = mockBubble.moveBy.mock.calls[0] ?? [];
		expect(deltaX).toBe(0);
		expect(deltaY).toBeGreaterThan(0);
	});

	it("uses a step of 1 when Shift is held (fine-grained move)", () => {
		render(<Bubble />);
		mockBubble.moveBy.mockClear();

		dispatchArrowKey("ArrowRight", { shiftKey: true });

		expect(mockBubble.moveBy).toHaveBeenCalledTimes(1);
		const [deltaX] = mockBubble.moveBy.mock.calls[0] ?? [];
		// Shift step is 1; default step is 10.  Either way the
		// sign is positive for ArrowRight, but the magnitude
		// must match the shift-step.
		expect(Math.abs(deltaX)).toBe(1);
	});
});

describe.skip("Bubble draggable gate — RW-0 rewrite of test_bubble_respects_draggable_gate (SKIPPED: BG-30 mouse-drag-only decision)", () => {
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

//the keyboard-move handler tested above was DEAD CODE in
// production (the bubble BrowserWindow is created with `focusable: false`,
// see `voice_typer/client/src/main/windows/bubble-window.ts`).  The
//feature was DELIBERATELY NOT RE-IMPLEMENTED ( option b — document
// as mouse-drag-only); see the comment block at the top of `Bubble.tsx`
// for the rationale.
//
// This test scans `bubble-window.ts` and asserts that `focusable: false`
// is still set.  If a future refactor flips it to `true` (or removes
// the option), this test will FAIL — at which point a renderer-side
//keyboard-move handler becomes reachable in production and the
// decision should be revisited.  The test also prints a warning to make
// the dead-code status loud in test output.
describe("BG-30: Bubble keyboard-move deliberately not implemented (focusable: false, mouse-drag-only)", () => {
	const bubbleWindowPath = path.resolve(
		__dirname,
		"..",
		"..",
		"..",
		"..",
		"..",
		"src",
		"main",
		"windows",
		"bubble-window.ts",
	);

	it("bubble-window.ts still sets `focusable: false` (keyboard-move deliberately not implemented — BG-30)", () => {
		// The path above resolves to
		//   voice_typer/client/src/main/windows/bubble-window.ts
		// (five ".." segments climb from
		// __tests__/a11y-rewrite/ back to voice_typer/client/,
		// then descend into src/main/windows/).
		const src = fs.readFileSync(bubbleWindowPath, "utf-8");

		// Look for the literal `focusable: false` BrowserWindow
		// option.  We allow whitespace around the colon and
		// tolerate it appearing on either side of a comment.
		const hasFocusableFalse = /focusable\s*:\s*false\b/.test(src);

		// Print a loud warning so the dead-code status is
		// visible in test output, not buried in a passing
		// assertion.  When this test starts FAILING (because
		// someone flipped focusable to true), the warning
		// message below explains exactly what to do: verify
		// the keyboard-move handler doesn't steal arrow keys
		// from the user's active app, then update the
		// dead-code comment in Bubble.tsx.
		if (hasFocusableFalse) {
			// eslint-disable-next-line no-console
			console.warn(
				"[BG-30] Bubble BrowserWindow is created with `focusable: false` — " +
					"the renderer-side keyboard arrow-move handler has been REMOVED from " +
					"Bubble.tsx (PVT-048 + PVT-067 fix by agent 12).  BG-30 DECISION: " +
					"keyboard-move was DELIBERATELY NOT re-implemented; the bubble is " +
					"documented in user-facing help as mouse-drag-only.  The main-process " +
					"`bubble:move-by` IPC handler (main/ipc/bubble-handlers.ts) is preserved " +
					"so a future product change can wire a global hotkey without renderer " +
					"work.  Do NOT re-add a window keydown handler unless `focusable: false` " +
					"is also flipped in bubble-window.ts.",
			);
		}

		expect(hasFocusableFalse).toBe(true);
	});

	it("Bubble.tsx documents the keyboard-move handler as deliberately not implemented (BG-30 comment block)", () => {
		// Companion assertion: Bubble.tsx itself must carry a
		// comment block explaining WHY the keyboard-move handler
		// was removed and HOW to re-implement it correctly.  This
		// guards against a refactor that removes the comment
		// (leaving future readers confused about why the handler
		// doesn't exist).
		const bubblePath = path.resolve(__dirname, "..", "..", "Bubble.tsx");
		const src = fs.readFileSync(bubblePath, "utf-8");

		// The comment block at the top of the new Bubble.tsx
		// explicitly mentions `focusable: false` and
		// `bubble-window.ts` and the global-hotkey migration path.
		expect(src).toMatch(/focusable:\s*false/i);
		expect(src).toContain("bubble-window.ts");
		// The comment must point at the main-process IPC handler
		// as the correct re-implementation target.
		expect(src).toContain("bubble:move-by");
	});
});

// Item 7 (Sub-agent 16): when the Bubble transitions from
// "transcribing" → "idle" (always_visible mode), it renders an
// sr-only `<span>` containing the i18n string "Transcription
// complete." (t("a11y.transcriptionComplete")) so screen-reader
// users hear the completion announcement.  Without this span, AT
// users would only know a transcription is happening (the
// "Transcribing…" label) but never hear when it's done — the
// visible bubble simply fades out, which is invisible to non-sighted
// users.
//
// This test mounts the Bubble in idle mode and asserts the sr-only
// span exists and contains the expected English text.  The Bubble's
// `<output aria-live="polite">` wrapper means the sr-only span's
// text is also announced to AT when the bubble transitions to idle.
describe("Item 7: Bubble renders sr-only 'Transcription complete.' announcement in idle mode", () => {
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

	it("renders an sr-only span with 'Transcription complete.' when mode is idle", () => {
		render(<Bubble />);

		// Drive the Bubble into idle mode via the onSetState
		// callback captured at mount.  The Bubble subscribes to
		// `window.bubble.onSetState` and updates its internal
		// `mode` state accordingly (via the useBubbleStateMachine
		// hook in bubble-components.tsx).
		const onSetStateCb = mockBubble.onSetState.mock.calls[0]?.[0] as
			| ((state: string) => void)
			| undefined;
		expect(onSetStateCb).toBeTruthy();
		act(() => onSetStateCb?.("idle"));

		// The sr-only span lives inside the Bubble's
		// `<output aria-live="polite">` wrapper, so its text
		// is announced to AT.  We assert the literal English
		// text (the default locale's translation of
		// `a11y.transcriptionComplete`).
		const srOnly = screen.getByText("Transcription complete.");
		expect(srOnly).toBeTruthy();

		// The span must have the `sr-only` class so it's
		// invisible to sighted users but read by screen
		// readers.
		expect(srOnly.className).toContain("sr-only");
	});
});
