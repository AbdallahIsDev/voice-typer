// @vitest-environment node
/**
 * : behavioral tests for `src/main/ipc/bubble-handlers.ts`.
 *
 * Covers the testable surface of the bubble IPC handlers:
 *   - `BUBBLE_ONLY_TYPES` set membership (used by `handle-message.ts`
 *     to filter Python push events so transcription / history never
 *     leak to the bubble window).
 *   - `MIN_BUBBLE_W` / `MAX_BUBBLE_W` / `MIN_BUBBLE_H` / `MAX_BUBBLE_H`
 *     boundary constants (used by the `bubble:resize` clamp logic).
 *   - `registerBubbleHandlers()` registers the full set of expected
 *     `ipcMain.on` channels (so a future contributor dropping one
 *     surfaces here, not in a renderer regression).
 *
 * The actual `bubble:move-by` screen-clamp logic lives inside the
 * `ipcMain.on("bubble:move-by", ...)` closure and depends on
 * `state.bubbleWindow.getPosition()` / `screen.getDisplayMatching()`.
 * Exercising it would require mocking the full Electron `BrowserWindow`
 * + `screen` surface, which is fragile. The clamp is verified at the
 * source level via the `MIN_BUBBLE_*` / `MAX_BUBBLE_*` constants
 * exported from this module — any code calling `clampBubbleSize` (which
 * uses those constants) is bounded by them.
 */
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
	return {
		ipcOn: vi.fn(),
	};
});

// Mock electron with ipcMain.on so we can capture the registrations.
vi.mock("electron", () => ({
	ipcMain: { on: mocks.ipcOn, handle: vi.fn() },
	screen: {
		getDisplayMatching: vi.fn(() => ({
			workArea: { x: 0, y: 0, width: 1920, height: 1080 },
		})),
	},
}));

vi.mock("../constants", () => ({
	BUBBLE_WIDTH: 74,
	BUBBLE_HEIGHT: 27,
}));

vi.mock("../logging", () => ({
	log: {
		warn: vi.fn(),
		info: vi.fn(),
		error: vi.fn(),
		debug: vi.fn(),
	},
}));

vi.mock("../python", () => ({
	sendToPython: vi.fn(),
}));

vi.mock("../state", () => ({
	state: {
		bubbleWindow: null,
		bubbleDraggable: true,
		bubblePosition: "top",
		_bubblePageReady: false,
	},
}));

vi.mock("../windows/bubble-window", () => ({
	centerOnActiveDisplay: vi.fn(() => ({ x: 0, y: 0 })),
	consumeHideAnimationCallback: vi.fn(() => null),
	hideBubbleWindow: vi.fn(),
	resetSavedBubblePosition: vi.fn(),
	showBubbleWindow: vi.fn(),
}));

import {
	BUBBLE_ONLY_TYPES,
	MAX_BUBBLE_H,
	MAX_BUBBLE_W,
	MIN_BUBBLE_H,
	MIN_BUBBLE_W,
	registerBubbleHandlers,
} from "../ipc/bubble-handlers";

describe("XS-78: bubble-handlers.ts", () => {
	describe("BUBBLE_ONLY_TYPES (ER-22)", () => {
		it("is a ReadonlySet<string>", () => {
			expect(BUBBLE_ONLY_TYPES).toBeInstanceOf(Set);
		});

		it("contains exactly the 5 bubble-only event types", () => {
			expect(BUBBLE_ONLY_TYPES.size).toBe(5);
			expect(BUBBLE_ONLY_TYPES.has("bubble_show")).toBe(true);
			expect(BUBBLE_ONLY_TYPES.has("bubble_hide")).toBe(true);
			expect(BUBBLE_ONLY_TYPES.has("bubble_set_state")).toBe(true);
			expect(BUBBLE_ONLY_TYPES.has("bubble_level")).toBe(true);
			expect(BUBBLE_ONLY_TYPES.has("bubble_config")).toBe(true);
		});

		it("does NOT contain non-bubble event types (SEC-017 filter boundary)", () => {
			// These event types must be broadcast to the main window
			// (they are not bubble-only). If any of them appeared in
			// BUBBLE_ONLY_TYPES, `handle-message.ts` would filter them
			// out of the main window's `python-event` channel and the
			// renderer would silently stop receiving them.
			expect(BUBBLE_ONLY_TYPES.has("transcription_final")).toBe(false);
			expect(BUBBLE_ONLY_TYPES.has("transcription_partial")).toBe(false);
			expect(BUBBLE_ONLY_TYPES.has("history_appended")).toBe(false);
			expect(BUBBLE_ONLY_TYPES.has("show_window")).toBe(false);
			expect(BUBBLE_ONLY_TYPES.has("quit_app")).toBe(false);
			expect(BUBBLE_ONLY_TYPES.has("relaunch_app")).toBe(false);
		});
	});

	describe("resize bounds (MIN/MAX constants)", () => {
		it("MIN_BUBBLE_W < MAX_BUBBLE_W (non-degenerate width range)", () => {
			expect(MIN_BUBBLE_W).toBeGreaterThan(0);
			expect(MAX_BUBBLE_W).toBeGreaterThan(MIN_BUBBLE_W);
		});

		it("MIN_BUBBLE_H < MAX_BUBBLE_H (non-degenerate height range)", () => {
			expect(MIN_BUBBLE_H).toBeGreaterThan(0);
			expect(MAX_BUBBLE_H).toBeGreaterThan(MIN_BUBBLE_H);
		});

		it("MAX_BUBBLE_W is small enough that the bubble cannot cover the screen (phishing-overlay guard)", () => {
			// SEC-016 rationale: a compromised renderer that sends a
			// runaway `bubble:resize` measurement must not be able to
			// grow the always-on-top bubble into a full-screen phishing
			// overlay. The MAX bounds keep the bubble pill-shaped.
			expect(MAX_BUBBLE_W).toBeLessThanOrEqual(400);
			expect(MAX_BUBBLE_H).toBeLessThanOrEqual(200);
		});

		it("MIN_BUBBLE_W is large enough that the bubble cannot disappear (invisible-pill guard)", () => {
			// A runaway measurement (or a CSS bug) that shrinks the
			// bubble to 0×0 would make it disappear. The MIN bounds
			// keep the pill visible.
			expect(MIN_BUBBLE_W).toBeGreaterThanOrEqual(20);
			expect(MIN_BUBBLE_H).toBeGreaterThanOrEqual(16);
		});
	});

	describe("registerBubbleHandlers() registers all expected channels", () => {
		it("registers the 7 documented bubble:* channels", () => {
			// Reset the mock so we only see registrations from this call.
			mocks.ipcOn.mockClear();
			registerBubbleHandlers();

			const channels = mocks.ipcOn.mock.calls.map((c: unknown[]) => c[0]);
			// The 7 channels documented in bubble-handlers.ts:
			// bubble:move-by, bubble:draggable, bubble:resize,
			// bubble:show-from-renderer, bubble:toggle-dictation,
			// bubble:set-position, bubble:ready, bubble:dismiss,
			// bubble:hidden.
			// (9 total — the doc lists 7 in the header but the module
			// also registers toggle-dictation, dismiss, hidden.)
			const expected = [
				"bubble:move-by",
				"bubble:draggable",
				"bubble:resize",
				"bubble:show-from-renderer",
				"bubble:toggle-dictation",
				"bubble:set-position",
				"bubble:ready",
				"bubble:dismiss",
				"bubble:hidden",
			];
			for (const ch of expected) {
				expect(channels).toContain(ch);
			}
		});

		it("does NOT register non-bubble channels (e.g. python-call)", () => {
			mocks.ipcOn.mockClear();
			registerBubbleHandlers();
			const channels = mocks.ipcOn.mock.calls.map((c: unknown[]) => c[0]);
			expect(channels).not.toContain("python-call");
			expect(channels).not.toContain("window:minimize");
		});
	});
});
