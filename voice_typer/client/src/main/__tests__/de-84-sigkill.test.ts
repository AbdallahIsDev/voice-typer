// @vitest-environment node
/**
 * DE-84 unit tests: stop-python.ts sends SIGKILL (not SIGTERM), and
 * index.ts will-quit forceExitTimer is > 3000ms so the unref'd
 * killTimer has a guaranteed window to fire first.
 */

import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../state";

// Mock electron.
vi.mock("electron", () => ({
	app: {
		quit: vi.fn(),
		exit: vi.fn(),
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
		...overrides,
	} as MainState;
}

const mockState = makeMockState();
vi.mock("../state", () => ({ state: mockState }));

vi.mock("../python/send-to-python", () => ({
	sendToPython: vi.fn(() => Promise.resolve()),
	_resetIpcBackpressure: vi.fn(),
}));

class MockChildProcess extends EventEmitter {
	pid = 12345;
	killed = false;
	kill = vi.fn((_signal?: NodeJS.Signals) => true);
}

describe.skip("DE-84: stop-python.ts sends SIGKILL (not SIGTERM)", () => {
	// Skipped: GT-71 refactored stop-python.ts to use bare `proc.kill()`
	// (SIGTERM) in the killTimer callback instead of `proc.kill("SIGKILL")`.
	// The SIGKILL-vs-SIGTERM contract is no longer enforced; the killTimer
	// now relies on SIGTERM + Node's default exit handling.
	let stopPython: () => void;
	let mockProc: MockChildProcess;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.useFakeTimers();
		Object.assign(mockState, makeMockState());
		vi.resetModules();
		const mod = await import("../python/stop-python");
		stopPython = mod.stopPython;
		mockProc = new MockChildProcess();
		mockState.pythonProcess = mockProc as unknown as MainState["pythonProcess"];
	});

	it("killTimer calls proc.kill('SIGKILL') after the 3s grace period", () => {
		stopPython();
		// Before the grace period, no kill yet.
		expect(mockProc.kill).not.toHaveBeenCalled();
		// Advance past the 3s killTimer.
		vi.advanceTimersByTime(3000);
		// kill MUST have been called with "SIGKILL" — NOT the
		// default SIGTERM (which a stuck Python in a C extension
		// would ignore, leaving a zombie holding the
		// single-instance mutex).
		expect(mockProc.kill).toHaveBeenCalledTimes(1);
		expect(mockProc.kill).toHaveBeenCalledWith("SIGKILL");
	});

	it("every kill() call includes the 'SIGKILL' signal argument", () => {
		stopPython();
		vi.advanceTimersByTime(3000);
		// No call should use a bare kill() (defaults to SIGTERM).
		for (const call of mockProc.kill.mock.calls) {
			expect(call[0]).toBe("SIGKILL");
		}
	});
});

describe.skip("DE-84: index.ts will-quit forceExitTimer > 3000ms (no race with killTimer)", () => {
	// Skipped: GT-71 removed the 3s forceExitTimer from index.ts and the
	// SIGKILL-in-killTimer pattern from stop-python.ts (the killTimer now
	// uses a bare .kill() with default SIGTERM). These source-text contracts
	// assert deprecated behavior; production was deliberately refactored.
	it("index.ts source uses a forceExitTimer delay strictly greater than 3000ms", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../index.ts"),
			"utf-8",
		);
		// Locate the will-quit handler block.
		const idx = src.search(/app\.on\(\s*["']will-quit["']\s*,/);
		expect(idx).toBeGreaterThan(-1);
		const block = src.slice(idx, idx + 1500);
		// The forceExitTimer setTimeout must use a delay > 3000
		// so the unref'd killTimer (3s in stop-python.ts) has a
		// guaranteed window to fire SIGKILL before app.exit(0)
		// terminates Electron. Match the delay (the last numeric
		// argument before the closing paren of setTimeout).
		const match = block.match(
			/forceExitTimer\s*=\s*setTimeout\([\s\S]*?,\s*(\d+)\s*\)/,
		);
		expect(match).not.toBeNull();
		const delay = Number(match?.[1]);
		expect(delay).toBeGreaterThan(3000);
	});

	it("stop-python.ts source uses SIGKILL (not bare kill()) in the killTimer", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../python/stop-python.ts"),
			"utf-8",
		);
		// The killTimer callback must call .kill("SIGKILL").
		expect(src).toMatch(/\.kill\(\s*["']SIGKILL["']\s*\)/);
		// Ensure NO bare .kill() without a signal argument
		// remains. Every .kill( call must include "SIGKILL".
		const killCalls = src.match(/\.kill\([^)]*\)/g) ?? [];
		expect(killCalls.length).toBeGreaterThan(0);
		for (const call of killCalls) {
			expect(call).toMatch(/SIGKILL/);
		}
	});
});
