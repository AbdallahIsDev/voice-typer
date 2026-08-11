// @vitest-environment node
/**
 * Tests for the `screen.on("display-removed", ...)` tracked-handle pattern
 * in `bubble/lifecycle.ts`.
 *
 * Background: the previous implementation called
 * `screen.removeAllListeners("display-removed")` before re-registering its
 * own handler to avoid stacking duplicate listeners across bubble window
 * re-creations. That anti-pattern was too aggressive — it evicted
 * listeners registered by OTHER parts of the app, not just the bubble's
 * own. The fix replaces it with a tracked-handle pattern:
 *
 *   - `attachDisplayRemovedHandler()` stores the listener in a module-level
 *     `_displayRemovedHandler` slot and uses `screen.off("display-removed",
 *     handler)` to remove ONLY that specific listener before re-registering.
 *   - the bubble window's `closed` handler calls `detachDisplayRemovedHandler()`
 *     to clean up the listener when the window goes away.
 *
 * These tests verify:
 *   1. `createBubbleWindow()` registers a `display-removed` listener via
 *      `screen.on` (NOT `removeAllListeners`).
 *   2. The tracked handle is populated after `createBubbleWindow()`.
 *   3. `removeAllListeners` is NEVER called on `screen` for the
 *      `display-removed` event.
 *   4. On `win.on("closed", ...)` the tracked handler is removed via
 *      `screen.off("display-removed", handler)` and the slot is cleared.
 *   5. Re-creating the bubble window re-registers a handler (and the
 *      previously tracked one is detached via `screen.off`).
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Spies for the `screen` mock. Hoisted so the mock factory can close over
// them and the tests can read their call history after the lifecycle
// module has been imported.
const screenSpies = vi.hoisted(() => {
	return {
		on: vi.fn(),
		off: vi.fn(),
		removeAllListeners: vi.fn(),
	};
});

// A minimal BrowserWindow-like mock. `createBubbleWindow` reads
// `state.bubbleWindow` first and short-circuits if it's already set and not
// destroyed — we set `state.bubbleWindow = null` in `beforeEach` to force
// the creation path.
const winSpies = vi.hoisted(() => {
	return {
		on: vi.fn(),
		setAlwaysOnTop: vi.fn(),
		setVisibleOnAllWorkspaces: vi.fn(),
		loadURL: vi.fn(() => Promise.resolve()),
		loadFile: vi.fn(() => Promise.resolve()),
		getPosition: vi.fn(() => [0, 0]),
		isDestroyed: vi.fn(() => false),
		webContents: {
			on: vi.fn(),
			send: vi.fn(),
		},
	};
});

// Hoisted mutable mock-state object. Hoisted so the `vi.mock("../../../state")`
// factory (which is itself hoisted above all top-level statements) closes
// over a stable reference. Mutations from `beforeEach` are visible to the
// lifecycle module because both sides read the same object.
const mockState = vi.hoisted(() => ({
	bubbleWindow: null as unknown,
	_bubblePageReady: false,
}));

vi.mock("electron", () => ({
	// `BrowserWindow` is invoked with `new` by `createBubbleWindow`.
	// The mock must therefore be a regular function (NOT an arrow
	// function — arrow functions cannot be used as constructors). When
	// a constructor returns an object, that object is used as the
	// result of `new`, so we return `winSpies` to give the lifecycle
	// code a stable, spied window instance.
	BrowserWindow: function BrowserWindow() {
		return winSpies;
	},
	dialog: { showErrorBox: vi.fn() },
	screen: {
		on: screenSpies.on,
		off: screenSpies.off,
		removeAllListeners: screenSpies.removeAllListeners,
		getPrimaryDisplay: () => ({
			workArea: { x: 0, y: 0, width: 1920, height: 1080 },
		}),
		getDisplayMatching: () => ({
			workArea: { x: 0, y: 0, width: 1920, height: 1080 },
		}),
	},
}));

vi.mock("../../../constants", () => ({
	BUBBLE_WIDTH: 74,
	BUBBLE_HEIGHT: 27,
	// lifecycle.ts uses the shared `RENDER_RELOAD_BACKOFF_MS`
	// constant instead of the literal `2000`. The mock returns the
	// real value so the render-process-gone reload setTimeout uses a
	// realistic delay (and so the test can assert the constant is
	// imported, not the literal).
	RENDER_RELOAD_BACKOFF_MS: 2000,
}));

vi.mock("../../../ipc/channels", () => ({
	BubbleChannels: { localeChanged: "bubble:locale-changed" },
}));

vi.mock("../../../logging", () => ({
	BUBBLE_CLR: "",
	RESET: "",
	log: { info: vi.fn(), warn: vi.fn(), error: vi.fn(), debug: vi.fn() },
}));

vi.mock("../../../state", () => ({ state: mockState }));

vi.mock("../console-forwarder", () => ({
	attachConsoleForwarder: vi.fn(),
}));

vi.mock("../crash-storm", () => ({
	// HU-29: the factory now takes a 4th `prefix` arg; the mock must
	// tolerate it (and any test that asserts the storm prefix would
	// import the real factory instead).
	createCrashStormTracker: () => ({
		record: () => false,
	}),
}));

vi.mock("../positioning", () => ({
	centerOnActiveDisplay: () => ({ x: 0, y: 0 }),
	getSavedBubblePosition: () => null,
	isForegroundFullscreen: () => false,
	isPositionOnAnyDisplay: () => true,
	setSavedBubblePosition: vi.fn(),
}));

import {
	__getDisplayRemovedHandlerForTest,
	createBubbleWindow,
	detachDisplayRemovedHandler,
} from "../lifecycle";

describe("bubble lifecycle.ts: display-removed tracked-handle pattern", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockState.bubbleWindow = null;
		mockState._bubblePageReady = false;
		// Defensive: ensure no tracked handler leaks between tests.
		detachDisplayRemovedHandler();
	});

	afterEach(() => {
		detachDisplayRemovedHandler();
	});

	it("createBubbleWindow registers a display-removed listener via screen.on", () => {
		createBubbleWindow();

		// screen.on must have been called for "display-removed" with a
		// function reference (the tracked handler).
		const calls = screenSpies.on.mock.calls.filter(
			(c: unknown[]) => c[0] === "display-removed",
		);
		expect(calls.length).toBeGreaterThanOrEqual(1);
		const handler = calls[0]?.[1];
		expect(typeof handler).toBe("function");
	});

	it("createBubbleWindow does NOT call screen.removeAllListeners for display-removed", () => {
		createBubbleWindow();

		// The tracked-handle pattern must replace the old
		// `removeAllListeners("display-removed")` anti-pattern.
		const ralCalls = screenSpies.removeAllListeners.mock.calls.filter(
			(c: unknown[]) => c[0] === "display-removed",
		);
		expect(ralCalls).toHaveLength(0);
	});

	it("the tracked handler slot is populated after createBubbleWindow", () => {
		createBubbleWindow();
		const handler = __getDisplayRemovedHandlerForTest();
		expect(handler).not.toBeNull();
		expect(typeof handler).toBe("function");
	});

	it("win 'closed' handler detaches the tracked listener via screen.off (NOT removeAllListeners)", () => {
		createBubbleWindow();

		// Find the registered "closed" handler on the win mock.
		const closedCalls = winSpies.on.mock.calls.filter(
			(c: unknown[]) => c[0] === "closed",
		);
		expect(closedCalls.length).toBe(1);
		const closedHandler = closedCalls[0]?.[1] as (() => void) | undefined;
		if (closedHandler === undefined) {
			throw new Error("expected a registered closed handler");
		}

		// Capture the tracked handler reference BEFORE the closed
		// handler clears the slot.
		const trackedBefore = __getDisplayRemovedHandlerForTest();
		expect(trackedBefore).not.toBeNull();

		// Invoke the closed handler (simulates the BrowserWindow
		// emitting "closed" on teardown).
		closedHandler();

		// screen.off must have been called with the SAME function
		// reference that was registered via screen.on — this is the
		// core guarantee of the tracked-handle pattern (vs the old
		// removeAllListeners which took no function argument).
		const offCalls = screenSpies.off.mock.calls.filter(
			(c: unknown[]) => c[0] === "display-removed",
		);
		expect(offCalls.length).toBeGreaterThanOrEqual(1);
		expect(offCalls[0]?.[1]).toBe(trackedBefore);

		// And removeAllListeners must STILL not have been called.
		const ralCalls = screenSpies.removeAllListeners.mock.calls.filter(
			(c: unknown[]) => c[0] === "display-removed",
		);
		expect(ralCalls).toHaveLength(0);

		// The tracked slot is cleared after close.
		expect(__getDisplayRemovedHandlerForTest()).toBeNull();
	});

	it("re-creating the bubble window detaches the previous handler via screen.off before re-registering", () => {
		createBubbleWindow();
		const firstHandler = __getDisplayRemovedHandlerForTest();
		expect(firstHandler).not.toBeNull();

		// Simulate the bubble window being destroyed (e.g. via
		// render-process-gone) so the next createBubbleWindow call
		// does not short-circuit on the cached window.
		mockState.bubbleWindow = null;

		// Re-create — attachDisplayRemovedHandler should call
		// screen.off for the FIRST handler before registering the
		// second one.
		createBubbleWindow();
		const secondHandler = __getDisplayRemovedHandlerForTest();
		expect(secondHandler).not.toBeNull();
		// The new handler must be a fresh function reference, not the
		// same one (otherwise the "tracked" slot would be a lie).
		expect(secondHandler).not.toBe(firstHandler);

		// screen.off must have been called with the first handler
		// during the second attach cycle.
		const offCalls = screenSpies.off.mock.calls.filter(
			(c: unknown[]) => c[0] === "display-removed",
		);
		const offArgs = offCalls.map((c: unknown[]) => c[1]);
		expect(offArgs).toContain(firstHandler);

		// And still no removeAllListeners.
		const ralCalls = screenSpies.removeAllListeners.mock.calls.filter(
			(c: unknown[]) => c[0] === "display-removed",
		);
		expect(ralCalls).toHaveLength(0);
	});

	it("detachDisplayRemovedHandler is a no-op when no handler is registered", () => {
		// Fresh state — no handler attached yet.
		expect(__getDisplayRemovedHandlerForTest()).toBeNull();
		expect(() => detachDisplayRemovedHandler()).not.toThrow();
		// screen.off must NOT be called when there is nothing to detach.
		expect(screenSpies.off).not.toHaveBeenCalled();
	});
});

// regression tests for the bubble lifecycle closed
// handler and the render-process-gone reload backoff constant.
describe("bubble lifecycle.ts: regressions", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockState.bubbleWindow = null;
		mockState._bubblePageReady = false;
		detachDisplayRemovedHandler();
	});

	afterEach(() => {
		detachDisplayRemovedHandler();
	});

	it("win 'closed' handler does NOT reset state._bubblePageReady (dead write removed)", () => {
		// Mark the field true BEFORE close so the test would fail if
		// the closed handler still wrote `false` to it.
		mockState._bubblePageReady = true;

		createBubbleWindow();

		// Find the registered "closed" handler on the win mock.
		const closedCalls = winSpies.on.mock.calls.filter(
			(c: unknown[]) => c[0] === "closed",
		);
		expect(closedCalls.length).toBe(1);
		const closedHandler = closedCalls[0]?.[1] as (() => void) | undefined;
		if (closedHandler === undefined) {
			throw new Error("expected a registered closed handler");
		}

		// Invoke the closed handler (simulates the BrowserWindow
		// emitting "closed" on teardown).
		closedHandler();

		// The closed handler must NOT have touched the dead
		// `_bubblePageReady` field — the matching read was never
		// implemented in `showBubbleWindow()` and the write in the
		// `bubble:ready` IPC handler was already removed. The
		// field stays at whatever value the test set (true).
		expect(mockState._bubblePageReady).toBe(true);
	});

	it("FZ-64: render-process-gone reload uses RENDER_RELOAD_BACKOFF_MS constant, not literal 2000", () => {
		// Source-text assertion: lifecycle.ts must import
		// `RENDER_RELOAD_BACKOFF_MS` from `../../constants` and use
		// it as the reload setTimeout delay (FZ-64 — magic-number
		// cleanup). The literal `2000` must NOT appear in the
		// render-process-gone reload setTimeout call.
		const src = fs.readFileSync(
			path.join(__dirname, "..", "lifecycle.ts"),
			"utf8",
		);

		// The constant must be imported.
		expect(src).toMatch(/RENDER_RELOAD_BACKOFF_MS[^A-Za-z_]/);
		// The render-process-gone reload setTimeout must use the
		// constant, not a literal `2000`.
		const reloadBlock = src.slice(src.indexOf("render-process-gone"));
		// Find the setTimeout inside the render-process-gone handler.
		const setTimeoutIdx = reloadBlock.indexOf("setTimeout");
		expect(setTimeoutIdx).toBeGreaterThan(-1);
		const setTimeoutSlice = reloadBlock.slice(
			setTimeoutIdx,
			setTimeoutIdx + 400,
		);
		expect(setTimeoutSlice).toContain("RENDER_RELOAD_BACKOFF_MS");
		// The literal `2000` must NOT appear inside the setTimeout
		// call. (Allowing `2000` elsewhere in the file is fine —
		// we only forbid it inside this specific setTimeout.)
		expect(setTimeoutSlice).not.toMatch(/,\s*2000\s*\)/);
	});
});
