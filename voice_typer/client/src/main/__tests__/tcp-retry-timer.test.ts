// @vitest-environment node
/**
 * R6-F6 unit tests for the TCP retry timer cleanup in `tcp-connect.ts`,
 * `stop-python.ts`, `relaunch-app.ts`, and `start-python.ts`.
 *
 * Verifies that the pending TCP retry timer is stored on
 * `state._tcpRetryTimer` and cleared by `stopPython()`,
 * `relaunchApp()` (dev + prod branches), and `startPython()`
 * before bumping the retry generation.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../state";

// We don't import the real tcp-connect.ts (it calls `new net.Socket()`
// at call-time). Instead we assert the state contract: when a retry is
// scheduled, state._tcpRetryTimer is set; when stopPython/relaunch/start
// run, it's cleared.

// Mock electron.
vi.mock("electron", () => ({
	app: {
		quit: vi.fn(),
		exit: vi.fn(),
		relaunch: vi.fn(),
		isPackaged: false,
		isQuitting: false,
	},
	dialog: { showErrorBox: vi.fn() },
}));
function makeMockState(overrides: Partial<MainState> = {}): MainState {
	return {
		pythonProcess: null,
		tcpSocket: null,
		mainWindow: null,
		bubbleWindow: null,
		pendingRequests: new Map(),
		nextId: 1,
		tcpBuffer: "",
		pythonReady: false,
		pythonExitedEarly: false,
		heartbeatInterval: null,
		sessionNonce: "",
		bubblePosition: "top",
		bubbleDraggable: true,
		bubbleDragging: false,
		_bubblePageReady: false,
		_hideTimeout: null,
		_tcpRetryCount: 0,
		_tcpRetryTimer: null,
		_tcpRetryGeneration: 0,
		_tcpAuthed: false,
		_hadConnectedBefore: false,
		_relaunching: false,
		_restartTriggered: false,
		preMaximizeBounds: null,
		...overrides,
	} as MainState;
}

const mockState = makeMockState();
vi.mock("../state", () => ({ state: mockState }));

// Mock send-to-python so stop-python doesn't actually write to a socket.
vi.mock("../python/send-to-python", () => ({
	sendToPython: vi.fn(() => Promise.resolve()),
}));

// Mock start-python so relaunch-app's dev-mode branch doesn't actually spawn.
vi.mock("../python/start-python", () => ({
	startPython: vi.fn(),
}));

describe("R6-F6: source-level contract for start-python.ts timer cleanup", () => {
	it("start-python.ts source clears _tcpRetryTimer before bumping _tcpRetryGeneration", () => {
		// We can't `import` start-python here (the top-level
		// `vi.mock("../python/start-python", ...)` mocks it for the
		// relaunch-app test). Instead we assert on the source text:
		// the cleanup must appear BEFORE the generation bump.
		const src = fs.readFileSync(
			path.resolve(__dirname, "../python/start-python.ts"),
			"utf-8",
		);
		const clearIdx = src.indexOf("clearTimeout(state._tcpRetryTimer)");
		const bumpIdx = src.indexOf("state._tcpRetryGeneration++");
		expect(clearIdx).toBeGreaterThan(-1);
		expect(bumpIdx).toBeGreaterThan(-1);
		expect(clearIdx).toBeLessThan(bumpIdx);
	});

	it("stop-python.ts source clears _tcpRetryTimer", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../python/stop-python.ts"),
			"utf-8",
		);
		expect(src).toContain("clearTimeout(state._tcpRetryTimer)");
		expect(src).toContain("state._tcpRetryTimer = null");
	});

	it("relaunch-app.ts source clears _tcpRetryTimer in BOTH branches", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../python/relaunch-app.ts"),
			"utf-8",
		);
		// Count occurrences — there should be at least 2 (dev + prod).
		const matches = src.match(/clearTimeout\(state\._tcpRetryTimer\)/g);
		expect(matches?.length ?? 0).toBeGreaterThanOrEqual(2);
	});

	it("tcp-connect.ts source stores the timer on state._tcpRetryTimer", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../python/tcp-connect.ts"),
			"utf-8",
		);
		expect(src).toContain("state._tcpRetryTimer = setTimeout(");
		// The setter branch must also clear the previous timer before
		// installing the new one (otherwise we leak the old handle).
		expect(src).toMatch(
			/if\s*\(\s*state\._tcpRetryTimer\s*\)\s*\{[\s\S]*?clearTimeout\(state\._tcpRetryTimer\)/,
		);
	});

	it("state.ts declares _tcpRetryTimer as a nullable field", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../state.ts"),
			"utf-8",
		);
		expect(src).toMatch(
			/_tcpRetryTimer:\s*ReturnType<typeof setTimeout>\s*\|\s*null/,
		);
		// Initial value must be null.
		expect(src).toMatch(/_tcpRetryTimer:\s*null,/);
	});
});

describe("R6-F6: state._tcpRetryTimer contract", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		vi.useFakeTimers();
		Object.assign(mockState, makeMockState());
	});

	it("state has an _tcpRetryTimer field initialized to null", () => {
		expect(mockState._tcpRetryTimer).toBeNull();
	});

	describe("stopPython() clears _tcpRetryTimer", () => {
		it("clears the timer when it was set", async () => {
			vi.resetModules();
			const { stopPython } = await import("../python/stop-python");

			const timer = setTimeout(() => {}, 10000);
			mockState._tcpRetryTimer = timer;
			mockState.pythonProcess = null; // early-return guard

			stopPython();

			expect(mockState._tcpRetryTimer).toBeNull();
			// Advance time — the cleared timer should NOT fire any
			// callback (we never attached one, but the assertion is
			// that state was reset).
		});

		it("is robust when _tcpRetryTimer is already null (no throw)", async () => {
			vi.resetModules();
			const { stopPython } = await import("../python/stop-python");
			mockState._tcpRetryTimer = null;
			mockState.pythonProcess = null;

			expect(() => stopPython()).not.toThrow();
			expect(mockState._tcpRetryTimer).toBeNull();
		});
	});

	describe("relaunchApp() clears _tcpRetryTimer (dev branch)", () => {
		it("clears the timer before bumping the generation", async () => {
			vi.resetModules();
			const { relaunchApp } = await import("../python/relaunch-app");

			// Dev mode: app.isPackaged === false (set in the electron mock)
			const timer = setTimeout(() => {}, 10000);
			mockState._tcpRetryTimer = timer;

			relaunchApp();

			expect(mockState._tcpRetryTimer).toBeNull();
		});
	});
});
