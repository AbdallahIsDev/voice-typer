// @vitest-environment node
/**
 *  unit tests for the render-process-gone crash-storm tracking
 * added in main-window.ts ().
 *
 * Verifies that:
 *   - recordBubbleRenderCrash() returns false for the first 5 crashes
 *   - recordBubbleRenderCrash() returns true on the 6th crash (storm)
 *   - the sliding window drops entries older than 60s
 *   - _resetRenderCrashTrackingForTest() clears the window arrays
 *
 * We test the bubble helper because it's the only storm-tracking function
 * exported from main-window.ts (the main-window helper is module-private
 * and wraps the same recordRenderCrash core; the bubble wrapper shares
 * the exact same logic, just with a different label + timestamp array).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock electron's `app`, `BrowserWindow`, `Menu`, `nativeTheme`, `dialog`.
const nativeThemeListeners: Array<() => void> = [];
vi.mock("electron", () => ({
	app: { isQuitting: false, isPackaged: false },
	BrowserWindow: vi.fn(),
	Menu: { setApplicationMenu: vi.fn() },
	nativeTheme: {
		shouldUseDarkColors: false,
		on: (_e: string, h: () => void) => nativeThemeListeners.push(h),
		off: (_e: string, h: () => void) => {
			const i = nativeThemeListeners.indexOf(h);
			if (i >= 0) nativeThemeListeners.splice(i, 1);
		},
	},
	dialog: { showErrorBox: vi.fn() },
}));

vi.mock("../constants", () => ({
	START_HIDDEN: false,
	BUBBLE_WIDTH: 74,
	BUBBLE_HEIGHT: 27,
}));
vi.mock("../logging", () => ({
	cleanConsoleMsg: (s: string) => s,
	RENDERER_CLR: "",
	RESET: "",
	BUBBLE_CLR: "",
	ts: () => "",
	log: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
	appendLogLine: vi.fn(),
	rendererErrorsLogPath: vi.fn(() => ""),
}));
vi.mock("../state", () => ({
	state: { mainWindow: null, bubbleWindow: null, bubblePosition: "bottom" },
}));

import type { recordBubbleRenderCrash as _RBRC } from "../windows/main-window";

describe("GT-10: render-process-gone crash-storm tracking", () => {
	let recordBubbleRenderCrash: typeof _RBRC;
	let _resetRenderCrashTrackingForTest: () => void;

	beforeEach(async () => {
		vi.clearAllMocks();
		nativeThemeListeners.length = 0;
		vi.resetModules();
		const mod = await import("../windows/main-window");
		recordBubbleRenderCrash = mod.recordBubbleRenderCrash;
		_resetRenderCrashTrackingForTest = mod._resetRenderCrashTrackingForTest;
		_resetRenderCrashTrackingForTest();
	});

	it("returns false for the first 5 crashes (under threshold)", () => {
		for (let i = 0; i < 5; i++) {
			expect(recordBubbleRenderCrash()).toBe(false);
		}
	});

	it("returns true on the 6th crash (storm detected)", () => {
		for (let i = 0; i < 5; i++) {
			expect(recordBubbleRenderCrash()).toBe(false);
		}
		// 6th crash trips the threshold (length > 5)
		expect(recordBubbleRenderCrash()).toBe(true);
	});

	it("keeps returning true for subsequent crashes after storm", () => {
		for (let i = 0; i < 6; i++) {
			recordBubbleRenderCrash();
		}
		// Already in storm state — further crashes still return true
		expect(recordBubbleRenderCrash()).toBe(true);
	});

	it("sliding window: 60s+ old entries are dropped, recovering from storm", () => {
		const baseTime = 1_000_000;
		let currentTime = baseTime;
		const spy = vi.spyOn(Date, "now").mockImplementation(() => currentTime);

		// 5 crashes at t=baseTime (under threshold)
		for (let i = 0; i < 5; i++) {
			expect(recordBubbleRenderCrash()).toBe(false);
		}

		// Advance 61 seconds — old entries should be dropped on the next push
		currentTime = baseTime + 61_000;

		// Now we can have 5 more crashes without tripping the threshold
		for (let i = 0; i < 5; i++) {
			expect(recordBubbleRenderCrash()).toBe(false);
		}

		spy.mockRestore();
	});

	it("_resetRenderCrashTrackingForTest clears the window", () => {
		// Trip the storm
		for (let i = 0; i < 6; i++) {
			recordBubbleRenderCrash();
		}
		// Reset
		_resetRenderCrashTrackingForTest();
		// Should be back under threshold
		expect(recordBubbleRenderCrash()).toBe(false);
	});
});
