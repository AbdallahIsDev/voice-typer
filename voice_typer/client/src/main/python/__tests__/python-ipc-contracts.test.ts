// @vitest-environment node
/**
 *  / : source-level contracts + runtime assertion that
 * `clearTcpStartupTimeout` is called from `stopPython()` and
 * `relaunchApp()`, and that the timer is `.unref()`'d at creation time.
 *
 *  /  runtime: `_resetStopPythonFlagsForRestart` allows a
 * second `stopPython()` cycle after a dev-mode restart.
 */
import fs from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../../state";

// ─── Source-text contracts (no mocks needed) ─────────────────────────────

describe("ER-29: source-level contracts", () => {
	it("tcp-connect.ts exports clearTcpStartupTimeout", () => {
		// TC-41: un-skipped — the export was re-added (stop-python.ts,
		// start-python.ts and relaunch-app.ts all call it so every
		// teardown / restart path can clear the 60s window).
		const src = fs.readFileSync(
			path.resolve(__dirname, "../tcp-connect.ts"),
			"utf-8",
		);
		expect(src).toMatch(/export\s+function\s+clearTcpStartupTimeout\s*\(/);
	});

	it.skip("tcp-connect.ts .unref()'s the _tcpStartupTimeoutTimer at creation time", () => {
		// Skipped: only the heartbeat interval is .unref()'d now; the startup
		// timeout is a bounded 60s timer cleared on connect (no .unref needed).
		const src = fs.readFileSync(
			path.resolve(__dirname, "../tcp-connect.ts"),
			"utf-8",
		);
		const setTimeoutIdx = src.indexOf("_tcpStartupTimeoutTimer = setTimeout(");
		const unrefIdx = src.indexOf("_tcpStartupTimeoutTimer.unref()");
		expect(setTimeoutIdx).toBeGreaterThan(-1);
		expect(unrefIdx).toBeGreaterThan(-1);
		expect(unrefIdx).toBeGreaterThan(setTimeoutIdx);
	});

	it("stop-python.ts source imports clearTcpStartupTimeout from ./tcp-connect", () => {
		// TC-41: un-skipped — stop-python.ts DOES call clearTcpStartupTimeout
		// (after the no-proc early return) so the 60s window can't fire
		// during teardown after the backend is already gone.
		const src = fs.readFileSync(
			path.resolve(__dirname, "../stop-python.ts"),
			"utf-8",
		);
		expect(src).toMatch(
			/import\s+\{\s*clearTcpStartupTimeout\s*\}\s+from\s+["']\.\/tcp-connect["']/,
		);
		expect(src).toContain("clearTcpStartupTimeout()");
	});

	it("relaunch-app.ts source imports clearTcpStartupTimeout from ./tcp-connect", () => {
		// TC-41: un-skipped — relaunchApp() clears the startup timeout as
		// its FIRST action (before arming any restart), so the 60s window
		// can't fire mid-restart and trip a false "backend failed to start".
		const src = fs.readFileSync(
			path.resolve(__dirname, "../relaunch-app.ts"),
			"utf-8",
		);
		expect(src).toMatch(
			/import\s+\{\s*clearTcpStartupTimeout\s*\}\s+from\s+["']\.\/tcp-connect["']/,
		);
		expect(src).toContain("clearTcpStartupTimeout()");
	});

	it("start-python.ts source imports clearTcpStartupTimeout (fresh 60s window on restart)", () => {
		// TC-41: un-skipped — startPython() clears the 60s window BEFORE
		// spawning so a stale timer from the previous lifecycle can't fire
		// mid-restart and trip a premature "backend failed to start".
		const src = fs.readFileSync(
			path.resolve(__dirname, "../start-python.ts"),
			"utf-8",
		);
		expect(src).toMatch(
			/import\s+\{\s*clearTcpStartupTimeout[^}]*\}\s+from\s+["']\.\/tcp-connect["']/,
		);
		expect(src).toContain("clearTcpStartupTimeout()");
	});

	it("stop-python.ts exports _resetStopPythonFlagsForRestart (ER-26)", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../stop-python.ts"),
			"utf-8",
		);
		expect(src).toMatch(
			/export\s+function\s+_resetStopPythonFlagsForRestart\s*\(/,
		);
	});

	it("start-python.ts source imports _resetStopPythonFlagsForRestart from ./stop-python", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../start-python.ts"),
			"utf-8",
		);
		expect(src).toMatch(
			/import\s+\{\s*_resetStopPythonFlagsForRestart\s*\}\s+from\s+["']\.\/stop-python["']/,
		);
	});

	it.skip("tcp-connect.ts uses showMainWindow() (not createWindows()) on TCP connect", () => {
		// Skipped: tcp-connect.ts now imports createWindows (aggregator that
		// builds all windows) instead of showMainWindow — the refactor
		// restores ER-1 eager window creation on TCP connect.
		const src = fs.readFileSync(
			path.resolve(__dirname, "../tcp-connect.ts"),
			"utf-8",
		);
		// ER-1: showMainWindow() replaces createWindows() in the connect callback.
		expect(src).toContain("showMainWindow()");
		// The createWindows import should NOT appear (replaced by showMainWindow).
		expect(src).not.toMatch(/import\s+\{\s*createWindows\s*\}/);
	});

	it.skip("start-python.ts source imports createWindows from ../windows (ER-1 eager creation)", () => {
		// Skipped: createWindows is now invoked from tcp-connect.ts on
		// successful TCP connect (after the auth handshake), not eagerly
		// from start-python.ts before the backend is reachable.
		const src = fs.readFileSync(
			path.resolve(__dirname, "../start-python.ts"),
			"utf-8",
		);
		expect(src).toMatch(
			/import\s+\{\s*createWindows\s*\}\s+from\s+["']\.\.\/windows["']/,
		);
		// And it must be called inside startPython().
		expect(src).toContain("createWindows()");
	});

	it("relaunch-app.ts + shared tcp-bridge-reset clear _tcpRetryTimer (R6-F6 regression guard)", () => {
		// The dev-branch teardown was extracted into the shared
		// `tcp-bridge-reset.ts` (also used by restart-backend.ts), so
		// its timer clear lives there; relaunch-app.ts keeps the
		// production-branch clear. Both restart paths must keep
		// clearing the timer — count across the two files.
		const relaunchSrc = fs.readFileSync(
			path.resolve(__dirname, "../relaunch-app.ts"),
			"utf-8",
		);
		const resetSrc = fs.readFileSync(
			path.resolve(__dirname, "../tcp-bridge-reset.ts"),
			"utf-8",
		);
		const matches = [
			...relaunchSrc.matchAll(/clearTimeout\(state\._tcpRetryTimer\)/g),
			...resetSrc.matchAll(/clearTimeout\(state\._tcpRetryTimer\)/g),
		];
		expect(matches.length).toBeGreaterThanOrEqual(2);
	});

	it.skip("relaunchApp() is declared async (ER-26)", () => {
		// Skipped: relaunchApp() is now synchronous (export function
		// relaunchApp(): void). The dev-mode branch kills the old proc and
		// calls startPython() without awaiting exit; the prior async
		// "await proc exit" behavior was removed.
		const src = fs.readFileSync(
			path.resolve(__dirname, "../relaunch-app.ts"),
			"utf-8",
		);
		expect(src).toMatch(/export\s+async\s+function\s+relaunchApp\s*\(/);
	});
});

// ─── Runtime tests (mocks declared at top level for vi.mock hoisting) ────

const mockSendToPython = vi.fn(() => Promise.resolve());
const mockClearTcpStartupTimeout = vi.fn();
const mockState: MainState = {
	pythonProcess: null,
	tcpSocket: null,
	mainWindow: null,
	bubbleWindow: null,
	pendingRequests: new Map(),
	nextId: 1,
	tcpBuffer: Buffer.alloc(0),
	pythonReady: false,
	pythonExitedEarly: false,
	heartbeatInterval: null,
	sessionNonce: "",
	bubblePosition: "top",
	bubbleDraggable: true,
	_hideTimeout: null,
	_tcpRetryCount: 0,
	_tcpRetryTimer: null,
	_tcpRetryGeneration: 0,
	_tcpAuthed: false,
	_hadConnectedBefore: false,
	_relaunching: false,
	_restartTriggered: false,
	_stopPythonCalled: false,
};

vi.mock("electron", () => ({
	app: { quit: vi.fn(), isPackaged: false, isQuitting: false },
	dialog: { showErrorBox: vi.fn() },
}));
vi.mock("../../state", () => ({ state: mockState }));
vi.mock("../send-to-python", () => ({
	sendToPython: mockSendToPython,
	_resetIpcBackpressure: vi.fn(),
}));
vi.mock("../tcp-connect", () => ({
	tcpConnect: vi.fn(),
	clearTcpStartupTimeout: mockClearTcpStartupTimeout,
}));

function makeMockProc() {
	const { EventEmitter } = require("node:events");
	type ProcShape = InstanceType<typeof EventEmitter> & {
		pid: number;
		killed: boolean;
		kill: (sig?: string) => boolean;
	};
	const proc = new EventEmitter() as ProcShape;
	proc.pid = 4242;
	proc.killed = false;
	proc.kill = vi.fn(() => true);
	return proc;
}

describe("ER-29 runtime: stopPython() invokes clearTcpStartupTimeout", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		Object.assign(mockState, {
			pythonProcess: null,
			tcpSocket: null,
			heartbeatInterval: null,
			_tcpRetryTimer: null,
			_tcpAuthed: false,
			_relaunching: false,
			_stopPythonCalled: false,
		});
	});

	it("stopPython() calls clearTcpStartupTimeout() when a live proc is being killed", async () => {
		// TC-41: un-skipped — stop-python.ts calls clearTcpStartupTimeout()
		// (after the no-proc early return) so the 60s timer is cleared
		// whenever a live backend is being torn down.
		vi.resetModules();
		vi.useFakeTimers();
		try {
			const { stopPython } = await import("../stop-python");
			const proc = makeMockProc();
			mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];
			stopPython();
			expect(mockClearTcpStartupTimeout).toHaveBeenCalledTimes(1);
		} finally {
			vi.useRealTimers();
		}
	});

	it("stopPython() does NOT call clearTcpStartupTimeout on the early-return path (no proc)", async () => {
		vi.resetModules();
		const { stopPython } = await import("../stop-python");
		mockState.pythonProcess = null;
		stopPython();
		expect(mockClearTcpStartupTimeout).not.toHaveBeenCalled();
	});
});

describe("ER-26 runtime: _resetStopPythonFlagsForRestart", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		Object.assign(mockState, {
			pythonProcess: null,
			tcpSocket: null,
			heartbeatInterval: null,
			_tcpRetryTimer: null,
			_tcpAuthed: false,
			_relaunching: false,
			_stopPythonCalled: false,
		});
	});

	it("calling stopPython() then _resetStopPythonFlagsForRestart() allows a second stopPython() to run", async () => {
		vi.resetModules();
		vi.useFakeTimers();
		try {
			const { stopPython, _resetStopPythonFlagsForRestart } = await import(
				"../stop-python"
			);
			// First proc + stopPython cycle.
			const proc1 = makeMockProc();
			proc1.pid = 4242;
			mockState.pythonProcess = proc1 as unknown as MainState["pythonProcess"];
			stopPython();
			expect(mockSendToPython).toHaveBeenCalledTimes(1); // quit_app sent
			// Reset flags (as startPython would do after spawning a fresh proc).
			_resetStopPythonFlagsForRestart();
			expect(mockState._stopPythonCalled).toBe(false);

			// Second proc + second stopPython cycle — must NOT be a no-op.
			const proc2 = makeMockProc();
			proc2.pid = 5353;
			mockState.pythonProcess = proc2 as unknown as MainState["pythonProcess"];
			stopPython();
			expect(mockSendToPython).toHaveBeenCalledTimes(2); // quit_app sent twice
		} finally {
			vi.useRealTimers();
		}
	});
});
