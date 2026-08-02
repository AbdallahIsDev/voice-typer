// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Spies for the `screen` mock. Hoisted so the mock factory can close over
// them and the tests can read their call history after positioning.ts has
// been imported.
const screenSpies = vi.hoisted(() => ({
	getAllDisplays: vi.fn(),
	getCursorScreenPoint: vi.fn(),
	getDisplayMatching: vi.fn(),
	getPrimaryDisplay: vi.fn(),
}));

const focusedWindowSpy = vi.hoisted(() => ({
	getFocusedWindow: vi.fn(),
}));

const logSpies = vi.hoisted(() => ({
	info: vi.fn(),
	warn: vi.fn(),
	error: vi.fn(),
	debug: vi.fn(),
}));

const mockState = vi.hoisted(() => ({
	bubblePosition: "bottom" as "top" | "bottom",
}));

vi.mock("electron", () => ({
	BrowserWindow: { getFocusedWindow: focusedWindowSpy.getFocusedWindow },
	screen: {
		getAllDisplays: screenSpies.getAllDisplays,
		getCursorScreenPoint: screenSpies.getCursorScreenPoint,
		getDisplayMatching: screenSpies.getDisplayMatching,
		getPrimaryDisplay: screenSpies.getPrimaryDisplay,
	},
}));

vi.mock("../../../constants", () => ({
	BUBBLE_WIDTH: 74,
	BUBBLE_HEIGHT: 46,
}));

vi.mock("../../../logging", () => ({ log: logSpies }));

vi.mock("../../../state", () => ({ state: mockState }));

import {
	centerOnActiveDisplay,
	centerOnPrimaryDisplay,
	getActiveDisplay,
	getSavedBubblePosition,
	isForegroundFullscreen,
	isPositionOnAnyDisplay,
	resetSavedBubblePosition,
	setSavedBubblePosition,
} from "../positioning";

describe("positioning.ts: saved-position state", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		resetSavedBubblePosition();
		mockState.bubblePosition = "bottom";
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("getSavedBubblePosition returns null before any position is saved", () => {
		expect(getSavedBubblePosition()).toBeNull();
	});

	it("setSavedBubblePosition / getSavedBubblePosition round-trip", () => {
		setSavedBubblePosition({ x: 10, y: 20 });
		expect(getSavedBubblePosition()).toEqual({ x: 10, y: 20 });
	});

	it("resetSavedBubblePosition clears the saved position", () => {
		setSavedBubblePosition({ x: 10, y: 20 });
		resetSavedBubblePosition();
		expect(getSavedBubblePosition()).toBeNull();
	});
});

describe("positioning.ts: display validation", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("isPositionOnAnyDisplay returns true when the position is inside a work area", () => {
		screenSpies.getAllDisplays.mockReturnValue([
			{ workArea: { x: 0, y: 0, width: 100, height: 100 } },
		]);
		expect(isPositionOnAnyDisplay({ x: 50, y: 50 })).toBe(true);
	});

	it("isPositionOnAnyDisplay returns false when the position is outside every work area", () => {
		screenSpies.getAllDisplays.mockReturnValue([
			{ workArea: { x: 0, y: 0, width: 100, height: 100 } },
		]);
		expect(isPositionOnAnyDisplay({ x: 150, y: 150 })).toBe(false);
	});

	it("isPositionOnAnyDisplay is permissive when screen.getAllDisplays throws", () => {
		screenSpies.getAllDisplays.mockImplementation(() => {
			throw new Error("no screen");
		});
		expect(isPositionOnAnyDisplay({ x: 9999, y: 9999 })).toBe(true);
	});
});

describe("positioning.ts: fullscreen detection", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("isForegroundFullscreen returns false on non-darwin platforms", () => {
		const platformSpy = vi.spyOn(process, "platform", "get");
		platformSpy.mockReturnValue("win32");
		screenSpies.getAllDisplays.mockReturnValue([
			{ workArea: { x: 0, y: 0, width: 100, height: 100 } },
		]);
		expect(isForegroundFullscreen()).toBe(false);
	});

	it("isForegroundFullscreen returns true when the focused window is fullscreen on darwin", () => {
		const platformSpy = vi.spyOn(process, "platform", "get");
		platformSpy.mockReturnValue("darwin");
		screenSpies.getAllDisplays.mockReturnValue([{ workArea: {} }]);
		focusedWindowSpy.getFocusedWindow.mockReturnValue({
			isFullScreen: () => true,
		});
		expect(isForegroundFullscreen()).toBe(true);
	});

	it("isForegroundFullscreen returns false when the focused window is not fullscreen on darwin", () => {
		const platformSpy = vi.spyOn(process, "platform", "get");
		platformSpy.mockReturnValue("darwin");
		screenSpies.getAllDisplays.mockReturnValue([{ workArea: {} }]);
		focusedWindowSpy.getFocusedWindow.mockReturnValue({
			isFullScreen: () => false,
		});
		expect(isForegroundFullscreen()).toBe(false);
	});

	it("isForegroundFullscreen logs a warning and returns false when detection throws", () => {
		screenSpies.getAllDisplays.mockImplementation(() => {
			throw new Error("gpu process gone");
		});
		expect(isForegroundFullscreen()).toBe(false);
		expect(logSpies.warn).toHaveBeenCalled();
	});
});

describe("positioning.ts: active display resolution + centering", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockState.bubblePosition = "bottom";
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("getActiveDisplay uses the cursor point to resolve the matching display", () => {
		screenSpies.getCursorScreenPoint.mockReturnValue({ x: 800, y: 600 });
		const display = { workArea: { x: 0, y: 0, width: 1920, height: 1080 } };
		screenSpies.getDisplayMatching.mockReturnValue(display);
		expect(getActiveDisplay()).toBe(display);
		expect(screenSpies.getDisplayMatching).toHaveBeenCalledWith(
			expect.objectContaining({ x: 800, y: 600, width: 1, height: 1 }),
		);
	});

	it("getActiveDisplay falls back to the primary display when the cursor API throws", () => {
		screenSpies.getCursorScreenPoint.mockImplementation(() => {
			throw new Error("headless");
		});
		const primary = { workArea: { x: 0, y: 0, width: 1920, height: 1080 } };
		screenSpies.getPrimaryDisplay.mockReturnValue(primary);
		expect(getActiveDisplay()).toBe(primary);
	});

	it("centerOnPrimaryDisplay centers horizontally and places the bubble at the bottom by default", () => {
		screenSpies.getPrimaryDisplay.mockReturnValue({
			workArea: { x: 100, y: 200, width: 1920, height: 1080 },
		});
		expect(centerOnPrimaryDisplay()).toEqual({ x: 1023, y: 1186 });
	});

	it("centerOnPrimaryDisplay places the bubble near the top when bubblePosition is top", () => {
		mockState.bubblePosition = "top";
		screenSpies.getPrimaryDisplay.mockReturnValue({
			workArea: { x: 100, y: 200, width: 1920, height: 1080 },
		});
		expect(centerOnPrimaryDisplay()).toEqual({ x: 1023, y: 248 });
	});

	it("centerOnActiveDisplay centers on the display under the cursor", () => {
		screenSpies.getCursorScreenPoint.mockReturnValue({ x: 800, y: 600 });
		screenSpies.getDisplayMatching.mockReturnValue({
			workArea: { x: 100, y: 200, width: 1920, height: 1080 },
		});
		expect(centerOnActiveDisplay()).toEqual({ x: 1023, y: 1186 });
	});

	it("centerOnActiveDisplay respects the top placement", () => {
		mockState.bubblePosition = "top";
		screenSpies.getCursorScreenPoint.mockReturnValue({ x: 800, y: 600 });
		screenSpies.getDisplayMatching.mockReturnValue({
			workArea: { x: 100, y: 200, width: 1920, height: 1080 },
		});
		expect(centerOnActiveDisplay()).toEqual({ x: 1023, y: 248 });
	});
});
