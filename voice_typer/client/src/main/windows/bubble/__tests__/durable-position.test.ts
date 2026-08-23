// @vitest-environment node
/**
 * Behavioral tests for the durable (cross-restart) bubble-position
 * persistence in `bubble/positioning.ts`.
 *
 * Covers the full in-main lifecycle:
 *   - `setPersistedBubblePosition` — the cache write path fed by
 *     `handle-message.ts` from `bubble_config` pushes.
 *   - `resolveRestoredBubblePosition` — restore priority: in-session
 *     drag position first, durable config pair second; off-screen
 *     candidates are rejected either way.
 *   - `recordBubbleMoved` + the debounced durable persist — a user drag
 *     schedules exactly one `set_config` write ~500ms after the last
 *     move; off-screen moves and suppressed windows (programmatic
 *     placements) never write; failures are logged, never thrown.
 *   - `cancelScheduledDurablePersist` — the Settings edge-toggle reset
 *     must be able to cancel a pending write so it can't race the
 *     server-side clear.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const logSpies = vi.hoisted(() => ({
	info: vi.fn(),
	warn: vi.fn(),
	error: vi.fn(),
	debug: vi.fn(),
}));

const sendToPythonSpy = vi.hoisted(() => vi.fn(() => Promise.resolve()));

vi.mock("electron", () => ({
	BrowserWindow: vi.fn(),
	screen: {
		getAllDisplays: () => [
			{ workArea: { x: 0, y: 0, width: 1920, height: 1080 } },
		],
		getPrimaryDisplay: () => ({
			workArea: { x: 0, y: 0, width: 1920, height: 1080 },
		}),
		getCursorScreenPoint: () => ({ x: 100, y: 100 }),
		getDisplayMatching: () => ({
			workArea: { x: 0, y: 0, width: 1920, height: 1080 },
		}),
	},
}));

vi.mock("../../../constants", () => ({
	BUBBLE_WIDTH: 74,
	BUBBLE_HEIGHT: 27,
}));

vi.mock("../../../logging", () => ({
	BUBBLE_CLR: "",
	RESET: "",
	log: logSpies,
}));

vi.mock("../../../state", () => ({ state: { bubblePosition: "bottom" } }));

vi.mock("../../../python/send-to-python", () => ({
	sendToPython: sendToPythonSpy,
}));

import {
	_resetDurablePersistStateForTest,
	cancelScheduledDurablePersist,
	getPersistedBubblePosition,
	getSavedBubblePosition,
	recordBubbleMoved,
	resetSavedBubblePosition,
	resolveRestoredBubblePosition,
	setPersistedBubblePosition,
	suppressDurablePersistFor,
} from "../positioning";

describe("durable bubble position cache", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		setPersistedBubblePosition(null);
		resetSavedBubblePosition();
	});

	it("stores a valid pair and reports it via the getter", () => {
		setPersistedBubblePosition({ x: -1920, y: 1040 });
		expect(getPersistedBubblePosition()).toEqual({ x: -1920, y: 1040 });
	});

	it("clears on null (edge-toggle reset propagation)", () => {
		setPersistedBubblePosition({ x: 10, y: 20 });
		setPersistedBubblePosition(null);
		expect(getPersistedBubblePosition()).toBeNull();
	});
});

describe("resolveRestoredBubblePosition", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		setPersistedBubblePosition(null);
		resetSavedBubblePosition();
	});

	it("returns null when nothing is saved", () => {
		expect(resolveRestoredBubblePosition()).toBeNull();
	});

	it("prefers the in-session drag position over the durable pair", () => {
		setPersistedBubblePosition({ x: 10, y: 10 });
		recordBubbleMoved({ x: 500, y: 300 });
		// recordBubbleMoved scheduled a persist — neutralize it for this
		// assertion's isolation.
		cancelScheduledDurablePersist();

		const resolved = resolveRestoredBubblePosition();
		expect(resolved).toEqual({ x: 500, y: 300 });
	});

	it("falls back to the durable pair when no in-session position exists", () => {
		setPersistedBubblePosition({ x: 120, y: 80 });
		expect(resolveRestoredBubblePosition()).toEqual({ x: 120, y: 80 });
	});

	it("rejects an off-screen durable pair (monitor unplug)", () => {
		setPersistedBubblePosition({ x: 50_000, y: 50_000 });
		expect(resolveRestoredBubblePosition()).toBeNull();
	});
});

describe("recordBubbleMoved + debounced durable persist", () => {
	beforeEach(() => {
		vi.useFakeTimers();
		vi.clearAllMocks();
		// The suppression window lives in positioning.ts module state and
		// outlives individual tests (the fake clock resets per test, so
		// a window armed by an earlier test would otherwise still be
		// "active"). Start every test from a clean slate.
		_resetDurablePersistStateForTest();
		setPersistedBubblePosition(null);
		resetSavedBubblePosition();
	});
	afterEach(() => {
		cancelScheduledDurablePersist();
		vi.useRealTimers();
	});

	it("schedules exactly one set_config after the debounce window", async () => {
		recordBubbleMoved({ x: 100, y: 200 });
		recordBubbleMoved({ x: 110, y: 210 });
		recordBubbleMoved({ x: 120, y: 220 });

		expect(sendToPythonSpy).not.toHaveBeenCalled();

		await vi.advanceTimersByTimeAsync(600);

		expect(sendToPythonSpy).toHaveBeenCalledTimes(1);
		expect(sendToPythonSpy).toHaveBeenCalledWith({
			type: "set_config",
			data: { bubble_x: 120, bubble_y: 220 },
		});
		// The last drag also becomes the in-session fast path.
		expect(getSavedBubblePosition()).toEqual({ x: 120, y: 220 });
	});

	it("does not persist an off-screen move and clears the in-session slot", async () => {
		recordBubbleMoved({ x: 99_999, y: -5_000 });

		await vi.advanceTimersByTimeAsync(1000);

		expect(sendToPythonSpy).not.toHaveBeenCalled();
		expect(getSavedBubblePosition()).toBeNull();
	});

	it("a suppressed window (programmatic placement) never persists", async () => {
		suppressDurablePersistFor();
		recordBubbleMoved({ x: 130, y: 240 });

		await vi.advanceTimersByTimeAsync(2000);

		expect(sendToPythonSpy).not.toHaveBeenCalled();
		// The in-session slot still updates — only the durable write is
		// suppressed.
		expect(getSavedBubblePosition()).toEqual({ x: 130, y: 240 });
	});

	it("cancelScheduledDurablePersist drops the pending write", async () => {
		recordBubbleMoved({ x: 140, y: 250 });
		cancelScheduledDurablePersist();

		await vi.advanceTimersByTimeAsync(2000);

		expect(sendToPythonSpy).not.toHaveBeenCalled();
	});

	it("a rejected persist is logged, not thrown", async () => {
		sendToPythonSpy.mockRejectedValueOnce(new Error("backend gone"));

		recordBubbleMoved({ x: 150, y: 260 });
		await vi.advanceTimersByTimeAsync(600);
		// Drain the fire-and-forget promise chain (.then → .catch) —
		// each link is one microtask tick.
		for (let i = 0; i < 10; i++) await Promise.resolve();

		expect(logSpies.warn).toHaveBeenCalledWith(
			expect.stringContaining("persisting bubble position failed"),
			expect.any(Error),
		);
	});
});
