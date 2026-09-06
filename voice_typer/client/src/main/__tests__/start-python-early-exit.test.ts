// @vitest-environment node
/**
 *  unit tests for the Python early-exit path in `start-python.ts`.
 *
 * Verifies that on early exit (Python backend exited before the first
 * TCP connect), `state.mainWindow.destroy()` is called (NOT `.close()`).
 * The previous `.close()` call was intercepted by the close-to-tray
 * handler in `main-window.ts`, which left a hidden BrowserWindow
 * orphaned with `state.mainWindow = null` pointing nowhere.
 */
import { EventEmitter } from "node:events";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../state";

// Build a fresh mock mainWindow for each test. The mock tracks whether
//`.close()` or `.destroy()` was called so we can assert
function makeMockMainWindow() {
	const win = {
		close: vi.fn(),
		destroy: vi.fn(),
		isDestroyed: vi.fn(() => false),
	};
	return win;
}

// Mock electron's `app` and `dialog`.
const mockAppQuit = vi.fn();
vi.mock("electron", () => ({
	app: {
		quit: mockAppQuit,
		isQuitting: false,
		isPackaged: false,
	},
	dialog: { showErrorBox: vi.fn() },
}));

// Mock constants so IPC_PORT/IPC_TOKEN don't matter.
vi.mock("../constants", () => ({
	IPC_PORT: 12345,
	IPC_TOKEN: "test-token",
	HEARTBEAT_INTERVAL_MS: 5000,
}));

// Mock i18n so mainT returns a string.
vi.mock("../i18n", () => ({ mainT: (key: string) => key }));

// Mock state — we install a fresh `state` object per test so the
// early-exit branch sees `state.mainWindow` set.
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
		_hideTimeout: null,
		_tcpRetryCount: 0,
		_tcpRetryTimer: null,
		_tcpRetryGeneration: 0,
		_tcpAuthed: false,
		_hadConnectedBefore: false,
		_relaunching: false,
		_restartTriggered: false,
		...overrides,
	} as MainState;
}

const mockState = makeMockState();
vi.mock("../state", () => ({ state: mockState }));

// Mock python-args / relaunch-app / tcp-connect so we can isolate the
// exit-handler logic without spawning real processes.
const mockPythonArgs = vi.fn(() => ["/fake/python", ["-m", "fake"]]);
vi.mock("../python/python-args", () => ({ pythonArgs: mockPythonArgs }));

const mockRelaunchApp = vi.fn();
vi.mock("../python/relaunch-app", () => ({ relaunchApp: mockRelaunchApp }));

const mockTcpConnect = vi.fn();
vi.mock("../python/tcp-connect", () => ({
	tcpConnect: mockTcpConnect,
	clearTcpStartupTimeout: vi.fn(),
}));

// Mock node:child_process spawn so startPython doesn't actually fork.
class MockChildProcess extends EventEmitter {
	pid = 12345;
	killed = false;
	kill = vi.fn(() => true);
}
const mockSpawn = vi.fn(() => new MockChildProcess());
vi.mock("node:child_process", () => ({ spawn: mockSpawn }));

describe("CR-34: start-python early-exit uses destroy() not close()", () => {
	let startPython: () => void;
	let mockProc: MockChildProcess;

	beforeEach(async () => {
		vi.clearAllMocks();
		// Reset state between tests.
		Object.assign(mockState, makeMockState());
		// Re-import start-python so it picks up fresh state.
		vi.resetModules();
		const mod = await import("../python/start-python");
		startPython = mod.startPython;
		// Make spawn return a fresh mock process each call.
		mockSpawn.mockImplementation(() => {
			mockProc = new MockChildProcess();
			return mockProc;
		});
	});

	it("calls state.mainWindow.destroy() (NOT .close()) on early exit", () => {
		const win = makeMockMainWindow();
		mockState.mainWindow = win as unknown as MainState["mainWindow"];
		mockState.pythonReady = false;

		startPython();
		// Simulate Python exiting early (before first connect).
		mockProc.emit("exit", 1);

		expect(win.destroy).toHaveBeenCalledTimes(1);
		expect(win.close).not.toHaveBeenCalled();
		expect(mockState.mainWindow).toBeNull();
	});

	it("clears state.pythonExitedEarly flag and rejects pendingRequests on early exit", () => {
		const win = makeMockMainWindow();
		mockState.mainWindow = win as unknown as MainState["mainWindow"];
		mockState.pythonReady = false;

		const rejectSpy = vi.fn();
		mockState.pendingRequests.set(42, {
			resolve: vi.fn(),
			reject: rejectSpy,
		});

		startPython();
		mockProc.emit("exit", 1);

		expect(mockState.pythonExitedEarly).toBe(true);
		expect(mockState.pendingRequests.size).toBe(0);
		expect(rejectSpy).toHaveBeenCalledWith(expect.any(Error));
	});

	it("calls app.quit() after destroying the window on early exit", () => {
		const win = makeMockMainWindow();
		mockState.mainWindow = win as unknown as MainState["mainWindow"];
		mockState.pythonReady = false;

		startPython();
		mockProc.emit("exit", 1);

		expect(mockAppQuit).toHaveBeenCalledTimes(1);
	});

	it("does NOT call destroy() if state.mainWindow is already null", () => {
		mockState.mainWindow = null;
		mockState.pythonReady = false;

		startPython();
		// Should not throw — just no-ops the window-destroy block.
		expect(() => mockProc.emit("exit", 1)).not.toThrow();
	});
});
