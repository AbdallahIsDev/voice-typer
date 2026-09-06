// @vitest-environment node
/**
 *  / : dev-mode relaunchApp() awaits the old proc's exit
 * event (with a 3s timeout) BEFORE spawning a fresh backend, reuses
 * stopPython() instead of duplicating the kill logic, and resets
 * `_relaunching` only AFTER startPython() completes. Also verifies
 *  (clearTcpStartupTimeout called from relaunchApp).
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
		//Node sets `exitCode` / `signalCode` to `null` until
		// the child actually exits. The mock mirrors that so the
		// SIGKILL-fallback liveness check
		// (`proc.exitCode === null && proc.signalCode === null`) works
		// the same way it does against a real ChildProcess.
		exitCode: null as number | null,
		signalCode: null as string | null,
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
					// Mirror Node: when a signal kills the proc,
					// `signalCode = sig` and `exitCode = null`. The
					// previous mock passed `(0, sig)` to listeners
					// which conflated exit code + signal. Modeling the
					// signal-kill case here lets the SIGKILL-fallback
					// liveness guard see `signalCode != null` after the
					// proc has actually exited (autoExitOnKill=true),
					// and `signalCode === null` when it hasn't
					// (autoExitOnKill=false).
					proc.signalCode = sig ?? "SIGTERM";
					proc.exitCode = null;
					(listeners.exit ?? []).forEach((cb) => {
						cb(null, proc.signalCode);
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
			tcpBuffer: Buffer.alloc(0),
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

	it("kills the old proc via the shared SIGTERM+SIGKILL helper (no duplicated kill logic)", async () => {
		// ER-26 dedupe contract: relaunchApp() must NOT carry its own
		// inline SIGTERM/SIGKILL escalation — the kill goes through
		// killPythonProcessWithSigkillFallback (the same helper
		// stop-python.ts uses). Dev mode sends SIGTERM first.
		vi.resetModules();
		const { relaunchApp } = await import("../relaunch-app");
		const proc = makeMockProc({ autoExitOnKill: false });
		mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];
		await relaunchApp();
		expect(proc.kill).toHaveBeenCalledWith("SIGTERM");
	});

	it("calls clearTcpStartupTimeout() before startPython() (ER-29 fresh 60s window)", async () => {
		// TC-41: un-skipped — relaunchApp() clears the 60s startup timeout
		// as its FIRST action, before any restart spawn, so a stale timer
		// can't fire mid-restart and trip a false "backend failed to start".
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

	it("awaits the old proc exit before calling startPython()", async () => {
		// ER-26 implemented: relaunchApp()'s dev branch awaits the old
		// proc's "exit" event (bounded at 3.5s) BEFORE startPython(), so
		// the fresh backend doesn't race the dying one for IPC_PORT.
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

	it("SIGKILL fallback fires if proc doesn't exit within 3s timeout", async () => {
		//(Critical): previously skipped because the production
		// guard `if (!proc.killed)` was always false after the SIGTERM
		// (Node sets `subprocess.killed = true` synchronously inside
		// `subprocess.kill()`), making the SIGKILL fallback dead code.
		// The fix replaces the guard with
		// `if (proc.exitCode === null && proc.signalCode === null)` so
		// it fires whenever the proc is genuinely still alive. The
		// mock now models `exitCode` / `signalCode` to match Node's
		// real semantics, so this test exercises the new liveness
		// check instead of the misleading `killed` flag.
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

	it("deletes VT_PYTHON_PORT/VT_IPC_TOKEN before startPython() so the spawn branch runs", async () => {
		// Standalone/terminal mode: the original Python CLI set these env
		// vars when spawning Electron. If they survive into the dev-mode
		// restart, startPython() takes the "connect to existing backend"
		// branch and never spawns a new Python — the app is left headless.
		vi.resetModules();
		// Set the env vars as the Python CLI would have.
		process.env.VT_PYTHON_PORT = "9876";
		process.env.VT_IPC_TOKEN = "test-token";
		try {
			const { relaunchApp } = await import("../relaunch-app");
			const proc = makeMockProc();
			mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];
			await relaunchApp();
			expect(process.env.VT_PYTHON_PORT).toBeUndefined();
			expect(process.env.VT_IPC_TOKEN).toBeUndefined();
			expect(mockStartPython).toHaveBeenCalledTimes(1);
		} finally {
			delete process.env.VT_PYTHON_PORT;
			delete process.env.VT_IPC_TOKEN;
		}
	});
});
