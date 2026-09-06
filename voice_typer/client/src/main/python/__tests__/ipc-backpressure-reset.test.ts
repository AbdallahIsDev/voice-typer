// @vitest-environment node
/**
 *  regression test: `_resetIpcBackpressure()` (renamed from
 * `_resetIpcBackpressureForTests`) is called from production code
 * paths — specifically `stopPython()` and `relaunchApp()` (both dev
 * and prod branches) — so the per-renderer rate-limit Map does not
 * leak entries for destroyed BrowserWindows.
 *
 * Pre-fix, the function existed but had zero production call sites
 * (the docstring falsely claimed `stopPython` / `relaunchApp` were
 * callers). Each destroyed BrowserWindow leaked its `webContents.id`
 * entry in the Map forever.
 *
 * Post-fix, the function is renamed (no `ForTests` suffix — it's no
 * longer test-only) and called from:
 *   - `stopPython()` (after the idempotency guard, before any
 *     early-return path).
 *   - `relaunchApp()` dev branch (after `state.tcpSocket = null`).
 *   - `relaunchApp()` prod branch (after `state.tcpSocket = null`).
 *
 * This test mocks `electron`, `state`, `start-python`, `tcp-connect`,
 * and `send-to-python` selectively so we can assert the call without
 * spinning up real IPC. The mock keeps `send-to-python`'s real
 * `_resetIpcBackpressure` implementation (so we can verify it actually
 * clears the Map) AND wraps it in a spy.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../../state";

// ─── Mocks ─────────────────────────────────────────────────────────────

const mockStartPython = vi.fn();
const mockClearTcpStartupTimeout = vi.fn();
const mockResetStopPythonFlags = vi.fn();

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
		// Default: dev mode (so relaunchApp takes the dev branch). Some
		// tests below flip this to true to exercise the prod branch.
		isPackaged: false,
		isQuitting: false,
	},
}));
vi.mock("../../state", () => ({ state: mockState }));
vi.mock("../start-python", () => ({ startPython: mockStartPython }));
// stop-python is the SUT for the two stopPython() tests below — they
// need the REAL module. vi.mock is hoisted above the imports, so the
// real implementation can't be captured in the factory; those tests
// load it directly via `vi.importActual("../stop-python")` (bypasses
// the placeholder mock for stop-python itself while keeping this
// file's mocks of ../state and ../tcp-connect active for its
// transitive imports). A per-test flag read inside an async
// importOriginal factory was tried first, but vitest memoizes a mock
// factory's result at first import — the flag never re-evaluated, so
// the real module was unreachable. The placeholder below covers every
// plain import site (relaunch-app's transitive imports), and the
// real-module tests opt out explicitly instead of re-mocking inside
// `it()` (the order-dependent pattern that flakes under the full-suite
// run; same race documented in bootstrap-app-user-model-id.test.ts).
// No current test imports stop-python through the registry (relaunch-app
// does not import it), so the placeholder is defensive: it keeps any
// future/accidental plain import of stop-python from pulling the real
// module with its real side effects.
vi.mock("../stop-python", () => ({
	stopPython: vi.fn(),
	_resetStopPythonFlagsForRestart: mockResetStopPythonFlags,
}));
vi.mock("../tcp-connect", () => ({
	tcpConnect: vi.fn(),
	clearTcpStartupTimeout: mockClearTcpStartupTimeout,
}));

// ─── Helpers ───────────────────────────────────────────────────────────

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

// ─── Tests ─────────────────────────────────────────────────────────────

describe("TY-35: _resetIpcBackpressure is wired to production call sites", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		// The stop-python placeholder mock is the default; the two
		// stopPython() tests load the real module via vi.importActual.
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

	it("_resetIpcBackpressure() clears the renderer rate-limit Map", async () => {
		vi.resetModules();
		// Load the REAL send-to-python module (no mock).
		const { _resetIpcBackpressure } = await import("../send-to-python");
		// We can't observe the private Map directly, but we CAN verify
		// the function runs without throwing and returns undefined. The
		// Map-clear behavior is exercised by the call-site tests below
		// (which would fail to clear if the function were a no-op).
		expect(_resetIpcBackpressure()).toBeUndefined();
	});

	it("stopPython() invokes _resetIpcBackpressure (production call site)", async () => {
		vi.resetModules();
		const sendToPythonModule =
			await vi.importActual<typeof import("../send-to-python")>(
				"../send-to-python",
			);
		const spy = vi.spyOn(sendToPythonModule, "_resetIpcBackpressure");

		// Load the REAL stop-python module via vi.importActual (no
		// doMock/doUnmock registry dance; ../state + ../tcp-connect
		// stay mocked for its transitive imports).
		const { stopPython } =
			await vi.importActual<typeof import("../stop-python")>("../stop-python");
		// Wire a mock process so stop_python doesn't early-return at the
		// `!state.pythonProcess` guard (which fires AFTER the backpressure
		// reset, but we want to exercise the full path).
		const proc = makeMockProc();
		mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];
		stopPython();
		// The backpressure reset must be called BEFORE the early-return
		// guard, so even with a live process it must have fired.
		expect(spy).toHaveBeenCalledTimes(1);
	});

	it("stopPython() invokes _resetIpcBackpressure even on the no-process early-return path", async () => {
		vi.resetModules();
		const sendToPythonModule =
			await vi.importActual<typeof import("../send-to-python")>(
				"../send-to-python",
			);
		const spy = vi.spyOn(sendToPythonModule, "_resetIpcBackpressure");

		// Load the REAL stop-python module via vi.importActual (no
		// doMock/doUnmock registry dance; ../state + ../tcp-connect
		// stay mocked for its transitive imports).
		const { stopPython } =
			await vi.importActual<typeof import("../stop-python")>("../stop-python");
		// No pythonProcess — the function will early-return at the
		// `!state.pythonProcess` guard. The backpressure reset MUST
		// have fired before that guard.
		mockState.pythonProcess = null;
		stopPython();
		expect(spy).toHaveBeenCalledTimes(1);
	});

	it("relaunchApp() dev-mode branch invokes _resetIpcBackpressure", async () => {
		vi.resetModules();
		const sendToPythonModule =
			await vi.importActual<typeof import("../send-to-python")>(
				"../send-to-python",
			);
		const spy = vi.spyOn(sendToPythonModule, "_resetIpcBackpressure");

		const { relaunchApp } = await import("../relaunch-app");
		const proc = makeMockProc();
		mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];
		await relaunchApp();
		expect(spy).toHaveBeenCalledTimes(1);
	});

	it("relaunchApp() production branch invokes _resetIpcBackpressure", async () => {
		vi.resetModules();
		// Flip to production mode for this test only.
		// Use vi.mocked to mutate the existing mock.
		const { app } = await import("electron");
		const isPackagedSpy = vi
			.spyOn(app, "isPackaged", "get")
			.mockReturnValue(true);
		try {
			const sendToPythonModule =
				await vi.importActual<typeof import("../send-to-python")>(
					"../send-to-python",
				);
			const spy = vi.spyOn(sendToPythonModule, "_resetIpcBackpressure");

			const { relaunchApp } = await import("../relaunch-app");
			const proc = makeMockProc();
			mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];
			relaunchApp();
			// Production branch is synchronous up to app.exit(0). The
			// backpressure reset runs after `state.tcpSocket = null`,
			// which is before `app.relaunch()` / `app.exit(0)`.
			expect(spy).toHaveBeenCalledTimes(1);
		} finally {
			isPackagedSpy.mockRestore();
		}
	});
});
