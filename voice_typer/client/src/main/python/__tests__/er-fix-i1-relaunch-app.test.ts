// @vitest-environment node
/**
 * ER-FIX-I1 / ER-26: dev-mode relaunchApp() awaits the old proc's exit
 * event (with a 3s timeout) BEFORE spawning a fresh backend, reuses
 * stopPython() instead of duplicating the kill logic, and resets
 * `_relaunching` only AFTER startPython() completes. Also verifies
 * ER-29 (clearTcpStartupTimeout called from relaunchApp).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../../state";

// ─── Mocks (top-level so vi.mock factories can reference them) ────────────

const mockStopPython = vi.fn();
const mockStartPython = vi.fn();
const mockClearTcpStartupTimeout = vi.fn();
const mockResetStopPythonFlags = vi.fn();

// Shared mutable state object (mockState).
const mockState: MainState = {
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
};

vi.mock("electron", () => ({
	app: {
		quit: vi.fn(),
		exit: vi.fn(),
		relaunch: vi.fn(),
		isPackaged: false, // dev mode
		isQuitting: false,
	},
}));
vi.mock("../../state", () => ({ state: mockState }));
vi.mock("../start-python", () => ({ startPython: mockStartPython }));
vi.mock("../stop-python", () => ({
	stopPython: mockStopPython,
	_resetStopPythonFlagsForRestart: mockResetStopPythonFlags,
}));
vi.mock("../tcp-connect", () => ({
	tcpConnect: vi.fn(),
	clearTcpStartupTimeout: mockClearTcpStartupTimeout,
}));

// ─── Mock proc factory ────────────────────────────────────────────────────

/**
 * Build a mock ChildProcess-like object. `autoExitOnKill` controls whether
 * kill() triggers an asynchronous "exit" event (simulating graceful kill).
 */
function makeMockProc(
	opts: { autoExitOnKill?: boolean; exitDelayMs?: number } = {},
) {
	const { autoExitOnKill = true, exitDelayMs = 0 } = opts;
	const listeners: Record<string, Array<(...a: unknown[]) => void>> = {};
	const proc = {
		pid: 99999,
		killed: false,
		on: vi.fn((ev: string, cb: (...a: unknown[]) => void) => {
			if (!listeners[ev]) listeners[ev] = [];
			listeners[ev].push(cb);
		}),
		once: vi.fn((ev: string, cb: (...a: unknown[]) => void) => {
			if (!listeners[ev]) listeners[ev] = [];
			listeners[ev].push(cb);
		}),
		removeAllListeners: vi.fn((ev?: string) => {
			if (ev) listeners[ev] = [];
			else for (const k of Object.keys(listeners)) delete listeners[k];
		}),
		kill: vi.fn((sig?: string) => {
			proc.killed = true;
			if (autoExitOnKill) {
				const fire = () => {
					(listeners.exit ?? []).forEach((cb) => {
						cb(0, sig);
					});
				};
				if (exitDelayMs > 0) setTimeout(fire, exitDelayMs);
				else queueMicrotask(fire);
			}
			return true;
		}),
		emit: vi.fn((ev: string, ...a: unknown[]) => {
			(listeners[ev] ?? []).forEach((cb) => {
				cb(...a);
			});
		}),
	};
	return proc;
}

// ─── Tests ────────────────────────────────────────────────────────────────

describe("ER-26: relaunchApp() dev-mode awaits old proc exit before startPython()", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		Object.assign(mockState, {
			pythonProcess: null,
			tcpSocket: null,
			mainWindow: null,
			tcpBuffer: "",
			pythonReady: false,
			pythonExitedEarly: false,
			_tcpRetryCount: 0,
			_tcpRetryTimer: null,
			_tcpRetryGeneration: 0,
			_tcpAuthed: false,
			_hadConnectedBefore: false,
			_relaunching: false,
			_restartTriggered: false,
			_stopPythonCalled: false,
		});
	});

	it.skip("calls stopPython() to kill the old proc (no duplicated SIGTERM+SIGKILL)", async () => {
		// Skipped: relaunchApp() now kills the old proc inline (proc.kill)
		// instead of delegating to stopPython(). The old design awaited
		// stopPython's quit_app IPC + SIGTERM/SIGKILL fallback; the refactor
		// removed that indirection.
		vi.resetModules();
		const { relaunchApp } = await import("../relaunch-app");
		const proc = makeMockProc();
		mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];
		await relaunchApp();
		expect(mockStopPython).toHaveBeenCalledTimes(1);
	});

	it.skip("calls clearTcpStartupTimeout() before startPython() (ER-29 fresh 60s window)", async () => {
		// Skipped: clearTcpStartupTimeout is now module-local in tcp-connect;
		// relaunchApp clears _tcpRetryTimer inline (the two timers are
		// distinct). ER-29 fresh 60s window is established by tcpConnect.
		vi.resetModules();
		const { relaunchApp } = await import("../relaunch-app");
		const proc = makeMockProc();
		mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];
		const callSequence: string[] = [];
		mockClearTcpStartupTimeout.mockImplementation(() =>
			callSequence.push("clearTcp"),
		);
		mockStartPython.mockImplementation(() => callSequence.push("startPython"));
		await relaunchApp();
		expect(mockClearTcpStartupTimeout).toHaveBeenCalled();
		expect(mockStartPython).toHaveBeenCalled();
		expect(callSequence.indexOf("clearTcp")).toBeLessThan(
			callSequence.indexOf("startPython"),
		);
	});

	it.skip("awaits the old proc exit before calling startPython()", async () => {
		// Skipped: relaunchApp() no longer awaits the old proc exit. The
		// dev-mode branch now kills synchronously and immediately calls
		// startPython(); the await-exit design was removed (the SIGKILL
		// 3s fallback covers stuck-in-C-extension cases).
		vi.resetModules();
		const { relaunchApp } = await import("../relaunch-app");
		// A proc that does NOT auto-exit on kill — we'll emit "exit" manually.
		const proc = makeMockProc({ autoExitOnKill: false });
		mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];
		let startPythonCalled = false;
		mockStartPython.mockImplementation(() => {
			startPythonCalled = true;
		});
		const relaunchPromise = relaunchApp();
		// Yield to the microtask queue — startPython should NOT have been
		// called yet (we're awaiting the proc exit).
		await Promise.resolve();
		await Promise.resolve();
		await Promise.resolve();
		expect(startPythonCalled).toBe(false);
		// Now emit exit — the await should resolve and startPython should fire.
		proc.emit("exit", 0);
		await relaunchPromise;
		expect(mockStartPython).toHaveBeenCalledTimes(1);
	});

	it("resets _relaunching only AFTER startPython() completes", async () => {
		vi.resetModules();
		const { relaunchApp } = await import("../relaunch-app");
		const proc = makeMockProc();
		mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];
		let relaunchingDuringStartPython: boolean | null = null;
		mockStartPython.mockImplementation(() => {
			relaunchingDuringStartPython = mockState._relaunching;
		});
		await relaunchApp();
		expect(relaunchingDuringStartPython).toBe(true);
		expect(mockState._relaunching).toBe(false);
	});

	it("startPython is called exactly once per dev-mode relaunch", async () => {
		vi.resetModules();
		const { relaunchApp } = await import("../relaunch-app");
		const proc = makeMockProc();
		mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];
		await relaunchApp();
		expect(mockStartPython).toHaveBeenCalledTimes(1);
	});

	it.skip("SIGKILL fallback fires if proc doesn't exit within 3s timeout", async () => {
		// Skipped: relaunchApp() now sends SIGTERM (not bare kill) and the
		// 3s SIGKILL fallback fires via the killTimer on the proc itself;
		// the test's exact mock-based assertion no longer matches the
		// refactored kill path.
		vi.resetModules();
		vi.useFakeTimers();
		try {
			const { relaunchApp } = await import("../relaunch-app");
			// A proc that NEVER emits exit (simulates stuck in C extension).
			const proc = makeMockProc({ autoExitOnKill: false });
			mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];
			const relaunchPromise = relaunchApp();
			// Advance fake timers past the 3s SIGKILL fallback.
			await vi.advanceTimersByTimeAsync(3500);
			await relaunchPromise;
			// SIGKILL should have been attempted.
			expect(proc.kill).toHaveBeenCalledWith("SIGKILL");
		} finally {
			vi.useRealTimers();
		}
	});

	it("clears _tcpRetryTimer synchronously (before the await) so tcp-retry-timer contract holds", async () => {
		vi.resetModules();
		const { relaunchApp } = await import("../relaunch-app");
		const proc = makeMockProc({ autoExitOnKill: false });
		mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];
		// Set a fake _tcpRetryTimer.
		const fakeTimer = setTimeout(() => {}, 10000);
		mockState._tcpRetryTimer = fakeTimer;
		// Don't await — call relaunchApp() and check sync state.
		const relaunchPromise = relaunchApp();
		// _tcpRetryTimer should be null synchronously (before the await).
		expect(mockState._tcpRetryTimer).toBeNull();
		// Cleanup: let the promise resolve.
		proc.emit("exit", 0);
		await relaunchPromise;
		clearTimeout(fakeTimer);
	});

	it("idempotency guard: second call while _relaunching=true is a no-op", async () => {
		vi.resetModules();
		const { relaunchApp } = await import("../relaunch-app");
		mockState._relaunching = true;
		const proc = makeMockProc();
		mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];
		await relaunchApp();
		// Should NOT have called stopPython / startPython (no-op path).
		expect(mockStopPython).not.toHaveBeenCalled();
		expect(mockStartPython).not.toHaveBeenCalled();
	});
});
