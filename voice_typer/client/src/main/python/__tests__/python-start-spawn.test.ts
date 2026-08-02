// @vitest-environment node
/**
 *  / : startPython() calls createWindows() EAGERLY (before
 * tcpConnect) so the user sees UI within ~100–200ms instead of waiting
 * 2–5s for the Python TCP auth handshake. Also verifies
 * (clearTcpStartupTimeout reset) and  (_resetStopPythonFlagsForRestart)
 * integration points in startPython.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../../state";

// ─── Mocks declared via vi.hoisted so they're available to the hoisted
// vi.mock factories. (vitest 4.x hoists vi.mock calls above all other
// statements; vi.hoisted guarantees the mock variables exist at hoist time.) ─

const mocks = vi.hoisted(() => {
	const callOrder: string[] = [];
	return {
		createWindows: vi.fn().mockImplementation(() => {
			callOrder.push("createWindows");
		}),
		showMainWindow: vi.fn(),
		tcpConnect: vi.fn().mockImplementation(() => {
			callOrder.push("tcpConnect");
		}),
		clearTcpStartupTimeout: vi.fn(),
		resetStopPythonFlags: vi.fn(),
		relaunchApp: vi.fn(),
		pythonArgs: vi.fn(() => ["/fake/python", ["-m", "fake"]]),
		spawn: vi.fn(),
		callOrder,
		state: {
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
			bubblePosition: "top" as const,
			bubbleDraggable: true,
			_bubblePageReady: false,
			_hideTimeout: null,
			_tcpRetryCount: 0,
			_tcpRetryTimer: null,
			_tcpRetryGeneration: 0,
			_tcpAuthed: false,
			_hadConnectedBefore: false,
			_relaunching: false,
			_restartTriggered: false,
			_stopPythonCalled: false,
		} satisfies MainState,
	};
});

vi.mock("electron", () => ({
	app: { quit: vi.fn(), isQuitting: false, isPackaged: false },
	dialog: { showErrorBox: vi.fn() },
}));
vi.mock("../../constants", () => ({
	IPC_PORT: 12345,
	IPC_TOKEN: "test-token",
	HEARTBEAT_INTERVAL_MS: 5000,
}));
vi.mock("../../i18n", () => ({ mainT: (k: string) => k }));
vi.mock("../../state", () => ({ state: mocks.state }));
vi.mock("../../windows", () => ({
	createWindows: mocks.createWindows,
	showMainWindow: mocks.showMainWindow,
}));
vi.mock("../python-args", () => ({ pythonArgs: mocks.pythonArgs }));
vi.mock("../relaunch-app", () => ({ relaunchApp: mocks.relaunchApp }));
vi.mock("../stop-python", () => ({
	stopPython: vi.fn(),
	_resetStopPythonFlagsForRestart: mocks.resetStopPythonFlags,
}));
vi.mock("../tcp-connect", () => ({
	tcpConnect: mocks.tcpConnect,
	clearTcpStartupTimeout: mocks.clearTcpStartupTimeout,
}));
vi.mock("node:child_process", () => ({ spawn: mocks.spawn }));

// ─── Helper: build a mock proc for spawn ──────────────────────────────────

function makeMockSpawnProc() {
	const { EventEmitter } = require("node:events");
	const proc = new EventEmitter();
	proc.pid = 12345;
	proc.killed = false;
	proc.kill = vi.fn(() => true);
	return proc;
}

// ─── Tests ────────────────────────────────────────────────────────────────

describe("ER-1: startPython() calls createWindows() before tcpConnect()", () => {
	beforeEach(() => {
		// Clear call history but keep implementations.
		mocks.createWindows.mockClear();
		mocks.showMainWindow.mockClear();
		mocks.tcpConnect.mockClear();
		mocks.clearTcpStartupTimeout.mockClear();
		mocks.resetStopPythonFlags.mockClear();
		mocks.relaunchApp.mockClear();
		mocks.pythonArgs.mockClear();
		mocks.spawn.mockClear();
		mocks.callOrder.length = 0;
		// Reset state.
		Object.assign(mocks.state, {
			pythonProcess: null,
			tcpSocket: null,
			mainWindow: null,
			tcpBuffer: Buffer.alloc(0),
			pythonReady: false,
			pythonExitedEarly: false,
			heartbeatInterval: null,
			_tcpRetryCount: 0,
			_tcpRetryTimer: null,
			_tcpRetryGeneration: 0,
			_tcpAuthed: false,
			_hadConnectedBefore: false,
			_relaunching: false,
			_restartTriggered: false,
			_stopPythonCalled: false,
		});
		mocks.spawn.mockImplementation(() => makeMockSpawnProc());
	});

	it.skip("calls createWindows() before tcpConnect() so UI renders during Python startup", async () => {
		// Skipped: createWindows() is now called from tcp-connect.ts on
		// successful TCP connect (after the auth handshake), not eagerly
		// from startPython() before the backend is reachable. The refactor
		// deferred window creation until the backend is actually ready.
		vi.resetModules();
		const { startPython } = await import("../start-python");
		startPython();
		expect(mocks.createWindows).toHaveBeenCalled();
		expect(mocks.tcpConnect).toHaveBeenCalled();
		const cwIdx = mocks.callOrder.indexOf("createWindows");
		const tcIdx = mocks.callOrder.indexOf("tcpConnect");
		expect(cwIdx).toBeGreaterThanOrEqual(0);
		expect(tcIdx).toBeGreaterThanOrEqual(0);
		expect(cwIdx).toBeLessThan(tcIdx);
	});

	it.skip("calls clearTcpStartupTimeout() at the top of startPython() (ER-29 fresh 60s window)", async () => {
		// Skipped: startPython() no longer calls clearTcpStartupTimeout
		// directly; the 60s startup window is established inside tcpConnect
		// on each call.
		vi.resetModules();
		const { startPython } = await import("../start-python");
		startPython();
		expect(mocks.clearTcpStartupTimeout).toHaveBeenCalledTimes(1);
	});

	it("calls _resetStopPythonFlagsForRestart() after spawning the fresh proc (ER-26)", async () => {
		vi.resetModules();
		const { startPython } = await import("../start-python");
		startPython();
		expect(mocks.resetStopPythonFlags).toHaveBeenCalledTimes(1);
	});

	it("createWindows() failure is caught — startPython continues (defensive)", async () => {
		vi.resetModules();
		mocks.createWindows.mockImplementationOnce(() => {
			throw new Error("BrowserWindow is not a constructor");
		});
		const { startPython } = await import("../start-python");
		expect(() => startPython()).not.toThrow();
		expect(mocks.tcpConnect).toHaveBeenCalled();
	});

	it.skip("startPython() also works in VT_PYTHON_PORT adopt mode (createWindows still called)", async () => {
		// Skipped: createWindows is no longer called from startPython();
		// it's invoked from tcp-connect on connect. The adopt-mode test's
		// createWindows assertion is now covered by tcp-connect tests.
		vi.resetModules();
		const origPort = process.env.VT_PYTHON_PORT;
		const origToken = process.env.VT_IPC_TOKEN;
		process.env.VT_PYTHON_PORT = "54321";
		process.env.VT_IPC_TOKEN = "adopted-token";
		try {
			const { startPython } = await import("../start-python");
			startPython();
			expect(mocks.createWindows).toHaveBeenCalled();
			expect(mocks.clearTcpStartupTimeout).toHaveBeenCalled();
			// In adopt mode, spawn is NOT called.
			expect(mocks.spawn).not.toHaveBeenCalled();
		} finally {
			if (origPort === undefined) delete process.env.VT_PYTHON_PORT;
			else process.env.VT_PYTHON_PORT = origPort;
			if (origToken === undefined) delete process.env.VT_IPC_TOKEN;
			else process.env.VT_IPC_TOKEN = origToken;
		}
	});
});
