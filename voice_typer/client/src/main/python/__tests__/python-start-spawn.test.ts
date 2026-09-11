// @vitest-environment node
/**
 *  / : startPython() calls createWindows() EAGERLY (before
 * tcpConnect) so the user sees UI within ~100–200ms instead of waiting
 * 2–5s for the Python TCP auth handshake. Also verifies
 * (clearTcpStartupTimeout reset) and  (_resetStopPythonFlagsForRestart)
 * integration points in startPython.
 */

import type { Mock } from "vitest";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../../state";

// ─── Mocks declared via vi.hoisted so they're available to the hoisted
// vi.mock factories. (vitest 4.x hoists vi.mock calls above all other
// statements; vi.hoisted guarantees the mock variables exist at hoist time.) ─

const mocks = vi.hoisted(() => {
	const callOrder: string[] = [];
	// Annotated as `MainState` (not `satisfies`) so boolean literals
	// like `pythonReady: false` contextually widen to `boolean` —
	// tests assign them directly (`mocks.state.pythonReady = true`).
	const state: MainState = {
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
		state,
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

	it("calls clearTcpStartupTimeout() at the top of startPython() (ER-29 fresh 60s window)", async () => {
		// TC-41: un-skipped, startPython() clears the 60s window before
		// spawning so a stale timer from the previous lifecycle can't fire
		// mid-restart and trip a premature "backend failed to start" dialog.
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

	it("createWindows() failure is caught, startPython continues (defensive)", async () => {
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

// ─── Idempotence guard: never double-spawn while the backend is alive ─────
//
// The suspend/resume handler calls startPython() on every `resume` event.
// Node's ChildProcess reports `exitCode: null` while the process runs and
// `signalCode: null` until it is terminated by a signal, so startPython()
// must no-op when the previously-spawned backend is still alive. A second
// spawn cannot acquire the Python-side single-instance mutex, exits early,
// and the early-exit handler quits the whole app with a misleading
// "only one instance" dialog, the app killing itself over its own
// double-spawn.

describe("startPython() idempotence guard (live backend → no-op)", () => {
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

	it("no-ops when the previous backend process is still alive (no spawn, no generation bump)", async () => {
		// Model a live child exactly as Node does: exitCode/signalCode
		// stay null from spawn until the process exits or is signalled.
		const liveProc = makeMockSpawnProc();
		liveProc.exitCode = null;
		liveProc.signalCode = null;
		Object.assign(mocks.state, { pythonProcess: liveProc });
		mocks.state._tcpRetryGeneration = 7;

		vi.resetModules();
		const { startPython } = await import("../start-python");
		startPython();

		expect(mocks.spawn).not.toHaveBeenCalled();
		// The no-op path must return BEFORE any spawn-side state churn:
		// the retry generation stays pinned (stale retry loops keep
		// their epoch) and no stop-flag / startup-timeout resets run.
		expect(mocks.state._tcpRetryGeneration).toBe(7);
		expect(mocks.clearTcpStartupTimeout).not.toHaveBeenCalled();
		expect(mocks.resetStopPythonFlags).not.toHaveBeenCalled();
		expect(mocks.tcpConnect).not.toHaveBeenCalled();
	});

	it("proceeds to spawn when the previous process exited cleanly (exitCode 0)", async () => {
		const deadProc = makeMockSpawnProc();
		deadProc.exitCode = 0;
		deadProc.signalCode = null;
		Object.assign(mocks.state, { pythonProcess: deadProc });

		vi.resetModules();
		const { startPython } = await import("../start-python");
		startPython();

		expect(mocks.spawn).toHaveBeenCalledTimes(1);
		expect(mocks.state.pythonProcess).toBe(mocks.spawn.mock.results[0]?.value);
		expect(mocks.state._tcpRetryGeneration).toBe(1);
	});

	it("proceeds to spawn when the previous process was killed by a signal (signalCode set)", async () => {
		const signalledProc = makeMockSpawnProc();
		signalledProc.exitCode = null;
		signalledProc.signalCode = "SIGTERM";
		Object.assign(mocks.state, { pythonProcess: signalledProc });

		vi.resetModules();
		const { startPython } = await import("../start-python");
		startPython();

		expect(mocks.spawn).toHaveBeenCalledTimes(1);
		expect(mocks.state.pythonProcess).not.toBe(signalledProc);
	});
});

// ─── Exit-handler typed rejections ────────────────────────────────────────
//
// When the backend process dies with IPC calls in flight, the exit handler
// must reject them with a typed PythonIpcError so the python-call bridge
// classifies them with the SAME code the pre-flight checks use:
//   - early exit (never connected)      → backend_exited_early
//   - crash after a successful connect  → backend_not_connected
// A bare Error would degrade both to the generic "command failed".

describe("startPython() exit handler: typed pending-request rejections", () => {
	// Typed as `(reason: unknown) => void` so the mock satisfies
	// `PendingRequest.reject` (the bare `vi.fn()` default Mock signature
	// is not assignable to it).
	let rejectMock: Mock<(reason: unknown) => void>;

	beforeEach(() => {
		vi.clearAllMocks();
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
		rejectMock = vi.fn<(reason: unknown) => void>();
		mocks.state.pendingRequests.set(7, {
			resolve: vi.fn(),
			reject: rejectMock,
		});
	});

	it("rejects pending IPC with PythonIpcError code backend_exited_early when the backend exits before its first connect", async () => {
		vi.resetModules();
		const { startPython } = await import("../start-python");
		const { PythonIpcError } = await import("../errors");
		// pythonReady stays false, the backend died during startup.
		startPython();
		const proc = mocks.spawn.mock.results[0]?.value as {
			emit: (ev: string, code: number) => boolean;
		};

		// Non-zero exit before the first connect → early-exit branch.
		proc.emit("exit", 1);

		expect(rejectMock).toHaveBeenCalledTimes(1);
		const err = rejectMock.mock.calls[0]?.[0];
		expect(err).toBeInstanceOf(PythonIpcError);
		// `PythonIpcError` above is the runtime class VALUE from the
		// dynamic import (instanceof identity against the fresh
		// module registry), so the instance TYPE is derived from it.
		expect((err as InstanceType<typeof PythonIpcError>).code).toBe(
			"backend_exited_early",
		);
		expect((err as InstanceType<typeof PythonIpcError>).message).toBe(
			"Python backend exited early",
		);
		expect(mocks.state.pendingRequests.size).toBe(0);
	});

	it("rejects pending IPC with PythonIpcError code backend_not_connected when the backend crashes mid-flight", async () => {
		vi.resetModules();
		const { startPython } = await import("../start-python");
		const { PythonIpcError } = await import("../errors");
		startPython();
		// The backend had connected and was ready, a crash now is a
		// mid-flight disconnect, not an early exit.
		mocks.state.pythonReady = true;
		const proc = mocks.spawn.mock.results[0]?.value as {
			emit: (ev: string, code: number) => boolean;
		};

		proc.emit("exit", 3);

		expect(rejectMock).toHaveBeenCalledTimes(1);
		const err = rejectMock.mock.calls[0]?.[0];
		expect(err).toBeInstanceOf(PythonIpcError);
		// `PythonIpcError` above is the runtime class VALUE from the
		// dynamic import (instanceof identity against the fresh
		// module registry), so the instance TYPE is derived from it.
		expect((err as InstanceType<typeof PythonIpcError>).code).toBe(
			"backend_not_connected",
		);
		expect((err as InstanceType<typeof PythonIpcError>).message).toBe(
			"Python backend disconnected",
		);
		expect(mocks.state.pendingRequests.size).toBe(0);
	});
});
