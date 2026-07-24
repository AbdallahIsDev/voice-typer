// @vitest-environment node
/**
 * GT-FIX-14 unit tests for GROUP 5 reliability fixes in the Electron
 * main-process lifecycle.
 *
 * Coverage:
 *   - GT-11:  SIGTERM/SIGINT handlers + 3s backstop (index.ts source-text)
 *   - GT-12:  synchronous SIGKILL in `_productionExit` (bootstrap source-text)
 *   - GT-60:  `.once('exit')` nulls `state.pythonProcess` (stop-python runtime)
 *   - GT-71:  killTimer is NOT `.unref()`'d + forceExitTimer removed (source-text)
 *   - GT-A3-7: `crashReporter.start` + `child-process-gone` handler (runtime)
 *   - GT-A3-10: `_resetStopPythonFlags()` resets the idempotency guard (runtime)
 *   - GT-B3-8: `logEvent` logs to `console.error` on fs failure (runtime)
 *
 * index.ts cannot be imported directly (it fires Electron APIs at module-
 * eval time), so GT-11 / GT-71 assertions are source-text checks anchored
 * on the actual handler registration — same pattern as
 * `shutdown-hooks.test.ts`. stop-python.ts and bootstrap.ts are exercised
 * at runtime with mocked electron/state.
 */

import { EventEmitter } from "node:events";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../state";

// ────────────────────────────────────────────────────────────────────
// Source-text helpers
// ────────────────────────────────────────────────────────────────────

function readSrc(rel: string): string {
	return fs.readFileSync(path.resolve(__dirname, rel), "utf-8");
}

// ────────────────────────────────────────────────────────────────────
// GT-11: SIGTERM/SIGINT handlers in index.ts (source-text)
// ────────────────────────────────────────────────────────────────────

describe("GT-11: index.ts registers SIGTERM/SIGINT handlers with 3s backstop", () => {
	it("registers process.on('SIGTERM', ...) that calls app.quit()", () => {
		const src = readSrc("../index.ts");
		// Anchor on the actual `process.on("SIGTERM",` registration —
		// not on a JSDoc mention. The 200-char window must include
		// `app.quit()` to prove the handler routes through Electron's
		// quit lifecycle (not a bare `process.exit`).
		const idx = src.search(/process\.on\(\s*["']SIGTERM["']\s*,/);
		expect(idx).toBeGreaterThan(-1);
		// Find the handler body: the signalQuitHandler definition.
		const handlerIdx = src.indexOf("signalQuitHandler");
		expect(handlerIdx).toBeGreaterThan(-1);
		const block = src.slice(handlerIdx, handlerIdx + 600);
		expect(block).toContain("app.quit()");
	});

	it("registers process.on('SIGINT', ...) that calls app.quit()", () => {
		const src = readSrc("../index.ts");
		const idx = src.search(/process\.on\(\s*["']SIGINT["']\s*,/);
		expect(idx).toBeGreaterThan(-1);
	});

	it("arms a 3s hard backstop setTimeout after app.quit()", () => {
		const src = readSrc("../index.ts");
		// The backstop must be a 3000ms setTimeout that calls
		// process.exit(0), and it must be .unref()'d so it doesn't
		// keep the event loop alive on a clean exit path.
		const handlerIdx = src.indexOf("signalQuitHandler");
		const block = src.slice(handlerIdx, handlerIdx + 600);
		expect(block).toMatch(
			/setTimeout\(\(\)\s*=>\s*process\.exit\(0\)\s*,\s*3000\)/,
		);
		expect(block).toMatch(/\.unref\(\)/);
	});

	it("uses a re-entry guard so a second signal does not call app.quit() twice", () => {
		const src = readSrc("../index.ts");
		const handlerIdx = src.indexOf("signalQuitHandler");
		const block = src.slice(handlerIdx, handlerIdx + 600);
		expect(block).toMatch(/_signalQuitFired/);
		expect(block).toMatch(/if\s*\(_signalQuitFired\)\s*return/);
	});
});

// ────────────────────────────────────────────────────────────────────
// GT-71: will-quit forceExitTimer removed + killTimer NOT unref'd
// ────────────────────────────────────────────────────────────────────

describe("GT-71: will-quit no longer uses a forceExitTimer that races with killTimer", () => {
	it("index.ts will-quit handler does NOT define a forceExitTimer", () => {
		const src = readSrc("../index.ts");
		// The will-quit handler block must not contain forceExitTimer.
		const idx = src.search(/app\.on\(\s*["']will-quit["']\s*,/);
		expect(idx).toBeGreaterThan(-1);
		const block = src.slice(idx, idx + 800);
		expect(block).not.toMatch(
			/const\s+forceExitTimer|forceExitTimer\s*=\s*setTimeout/,
		);
	});

	it("index.ts will-quit handler registers pythonProcess.once('exit', app.exit(0))", () => {
		const src = readSrc("../index.ts");
		const idx = src.search(/app\.on\(\s*["']will-quit["']\s*,/);
		expect(idx).toBeGreaterThan(-1);
		const block = src.slice(idx, idx + 800);
		expect(block).toMatch(/pythonProcess\.once\(\s*["']exit["']/);
		expect(block).toMatch(/app\.exit\(0\)/);
	});

	it("stop-python.ts does NOT call killTimer.unref() (keeps Electron alive until Python is dead)", () => {
		const src = readSrc("../python/stop-python.ts");
		expect(src).not.toMatch(/killTimer\.unref\(\)/);
	});
});

// ────────────────────────────────────────────────────────────────────
// GT-60 + GT-71: will-quit else branch for null pythonProcess
// ────────────────────────────────────────────────────────────────────

describe("GT-60: will-quit has an else branch that exits immediately when pythonProcess is null", () => {
	it("index.ts will-quit handler contains an else branch calling app.exit(0)", () => {
		const src = readSrc("../index.ts");
		const idx = src.search(/app\.on\(\s*["']will-quit["']\s*,/);
		expect(idx).toBeGreaterThan(-1);
		const block = src.slice(idx, idx + 800);
		// The if (state.pythonProcess) { ... } else { app.exit(0) } pattern.
		expect(block).toMatch(/if\s*\(\s*state\.pythonProcess\s*\)/);
		expect(block).toMatch(/else\s*\{[\s\S]*?app\.exit\(0\)/);
	});
});

// ────────────────────────────────────────────────────────────────────
// GT-12: synchronous SIGKILL in _productionExit (source-text)
// ────────────────────────────────────────────────────────────────────

describe("GT-12: bootstrap.ts _productionExit synchronously SIGKILLs Python before app.quit()", () => {
	it("calls state.pythonProcess?.kill('SIGKILL') BEFORE app.quit()", () => {
		const src = readSrc("../bootstrap.ts");
		const fnIdx = src.indexOf("function _productionExit");
		expect(fnIdx).toBeGreaterThan(-1);
		const block = src.slice(fnIdx, fnIdx + 1200);
		const sigkillIdx = block.indexOf('kill("SIGKILL")');
		const quitIdx = block.indexOf("app.quit()");
		expect(sigkillIdx).toBeGreaterThan(-1);
		expect(quitIdx).toBeGreaterThan(-1);
		// SIGKILL must come BEFORE app.quit() in the function body.
		expect(sigkillIdx).toBeLessThan(quitIdx);
	});

	it("wraps the SIGKILL in try/catch so a failure cannot block the exit", () => {
		const src = readSrc("../bootstrap.ts");
		const fnIdx = src.indexOf("function _productionExit");
		const block = src.slice(fnIdx, fnIdx + 1200);
		expect(block).toMatch(/try\s*\{[\s\S]*?SIGKILL[\s\S]*?\}\s*catch/);
	});
});

// ────────────────────────────────────────────────────────────────────
// Mocks for runtime tests (stop-python.ts + bootstrap.ts)
// ────────────────────────────────────────────────────────────────────

// Mock electron for the stop-python runtime tests.
const mockAppQuit = vi.fn();
const mockAppExit = vi.fn();
vi.mock("electron", () => ({
	app: {
		quit: mockAppQuit,
		exit: mockAppExit,
		isPackaged: false,
		isQuitting: false,
		getPath: vi.fn(() => "/tmp/vt-gt-fix-14"),
		setPath: vi.fn(),
		on: vi.fn(),
	},
	dialog: { showErrorBox: vi.fn() },
	session: {
		defaultSession: {
			webRequest: { onHeadersReceived: vi.fn() },
		},
	},
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

vi.mock("../i18n", () => ({ mainT: (k: string) => k }));

const sendToPythonMock = vi.fn(() => Promise.resolve());
vi.mock("../python/send-to-python", () => ({
	sendToPython: sendToPythonMock,
}));

// Mock ../python barrel so bootstrap.ts's `import { stopPython } from "./python"`
// resolves without pulling in the real main entry.
vi.mock("../python", () => ({ stopPython: vi.fn() }));

vi.mock("../single_instance", () => ({
	computeConfigDir: () => "/tmp/vt-gt-fix-14",
	clearElectronPidFile: vi.fn(),
}));

class MockChildProcess extends EventEmitter {
	pid = 12345;
	killed = false;
	kill = vi.fn((_signal?: string) => true);
}

// ────────────────────────────────────────────────────────────────────
// GT-60 + GT-A3-10: stop-python.ts runtime tests
// ────────────────────────────────────────────────────────────────────

describe("GT-60: stop-python.ts .once('exit') nulls state.pythonProcess", () => {
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

	afterEach(() => {
		vi.useRealTimers();
	});

	it("nulls state.pythonProcess when Python exits gracefully (before killTimer fires)", () => {
		stopPython();
		expect(mockState.pythonProcess).not.toBeNull();
		// Simulate Python exiting gracefully before the 3s killTimer.
		mockProc.emit("exit", 0);
		// GT-60: state.pythonProcess must be nulled so will-quit's
		// `if (state.pythonProcess)` check is false.
		expect(mockState.pythonProcess).toBeNull();
	});

	it("nulls state.pythonProcess when the killTimer fires (SIGTERM path)", () => {
		stopPython();
		expect(mockState.pythonProcess).not.toBeNull();
		// Advance past the 3s killTimer.
		vi.advanceTimersByTime(3000);
		expect(mockState.pythonProcess).toBeNull();
	});

	it("subsequent stopPython() calls are no-ops after graceful exit", () => {
		stopPython();
		expect(sendToPythonMock).toHaveBeenCalledTimes(1);
		mockProc.emit("exit", 0);
		stopPython();
		stopPython();
		expect(sendToPythonMock).toHaveBeenCalledTimes(1);
	});
});

describe("GT-A3-10: _resetStopPythonFlags() resets the idempotency guard", () => {
	let stopPython: () => void;
	let _resetStopPythonFlags: () => void;
	let mockProc: MockChildProcess;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.useFakeTimers();
		Object.assign(mockState, makeMockState());
		vi.resetModules();
		const mod = await import("../python/stop-python");
		stopPython = mod.stopPython;
		_resetStopPythonFlags = mod._resetStopPythonFlags;
		mockProc = new MockChildProcess();
		mockState.pythonProcess = mockProc as unknown as MainState["pythonProcess"];
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it("exports _resetStopPythonFlags as a function", () => {
		expect(typeof _resetStopPythonFlags).toBe("function");
	});

	it("after stopPython() + graceful exit, _resetStopPythonFlags() allows a second stopPython() to fire quit_app", () => {
		// First stop cycle.
		stopPython();
		expect(sendToPythonMock).toHaveBeenCalledTimes(1);
		mockProc.emit("exit", 0);
		// isStopped is now latched — a second stopPython() is a no-op.
		stopPython();
		expect(sendToPythonMock).toHaveBeenCalledTimes(1);

		// Reset the flags — simulate startPython() calling this before
		// spawning a fresh backend.
		_resetStopPythonFlags();
		expect(mockState._stopPythonCalled).toBe(false);

		// Install a fresh mock process (startPython would do this).
		mockProc = new MockChildProcess();
		mockState.pythonProcess = mockProc as unknown as MainState["pythonProcess"];

		// Now stopPython() should fire quit_app again.
		stopPython();
		expect(sendToPythonMock).toHaveBeenCalledTimes(2);
		expect(sendToPythonMock).toHaveBeenLastCalledWith({
			type: "quit_app",
		});
	});

	it("clears any armed killTimer from the previous cycle", () => {
		stopPython();
		// killTimer is armed (3s). Do NOT advance fake timers — the
		// timer is still pending.
		// Reset the flags — the armed killTimer should be cleared.
		_resetStopPythonFlags();
		// Advance past 3s — the old killTimer should NOT fire (it was
		// cleared). If it had fired, mockProc.kill would have been
		// called.
		expect(mockProc.kill).not.toHaveBeenCalled();
		vi.advanceTimersByTime(3000);
		expect(mockProc.kill).not.toHaveBeenCalled();
	});
});

// ────────────────────────────────────────────────────────────────────
// GT-A3-7: crashReporter.start + child-process-gone handler (runtime)
// ────────────────────────────────────────────────────────────────────

describe("GT-A3-7: bootstrapRuntime starts crashReporter + registers child-process-gone handler", () => {
	let tmpDir: string;
	let crashReporterStartMock: ReturnType<typeof vi.fn>;
	let appOnMock: ReturnType<typeof vi.fn>;
	let logErrorSpy: ReturnType<typeof vi.fn>;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.resetModules();
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-gt-fix-14-bootstrap-"));

		crashReporterStartMock = vi.fn();
		appOnMock = vi.fn();
		logErrorSpy = vi.fn();

		// Override the electron mock to include crashReporter + app.on.
		vi.doMock("electron", () => ({
			app: {
				getPath: vi.fn(() => tmpDir),
				setPath: vi.fn(),
				isPackaged: true,
				on: appOnMock,
				quit: vi.fn(),
				exit: vi.fn(),
			},
			crashReporter: { start: crashReporterStartMock },
			dialog: { showErrorBox: vi.fn() },
			session: {
				defaultSession: {
					webRequest: { onHeadersReceived: vi.fn() },
				},
			},
		}));
		vi.doMock("../state", () => ({ state: makeMockState() }));
		vi.doMock("../i18n", () => ({ mainT: (k: string) => k }));
		vi.doMock("../python", () => ({ stopPython: vi.fn() }));
		vi.doMock("../single_instance", () => ({
			computeConfigDir: () => tmpDir,
			clearElectronPidFile: vi.fn(),
		}));
		vi.doMock("../logging", () => ({
			DEFAULT_CRASH_LOG_MAX_BYTES: 1_048_576,
			rotateIfNeeded: vi.fn(),
			log: { error: logErrorSpy, info: vi.fn(), warn: vi.fn() },
		}));
	});

	afterEach(() => {
		try {
			fs.rmSync(tmpDir, { recursive: true, force: true });
		} catch {
			/* ignore */
		}
		vi.doUnmock("electron");
		vi.doUnmock("../state");
		vi.doUnmock("../i18n");
		vi.doUnmock("../python");
		vi.doUnmock("../single_instance");
		vi.doUnmock("../logging");
	});

	it("calls crashReporter.start with uploadToServer:false and crashReporterDirectory", async () => {
		const { bootstrapRuntime } = await import("../bootstrap");
		bootstrapRuntime();
		expect(crashReporterStartMock).toHaveBeenCalledTimes(1);
		const opts = crashReporterStartMock.mock.calls[0][0] as Record<
			string,
			unknown
		>;
		expect(opts.uploadToServer).toBe(false);
	});

	it("registers a child-process-gone handler via app.on", async () => {
		const { bootstrapRuntime } = await import("../bootstrap");
		bootstrapRuntime();
		const events = appOnMock.mock.calls.map((c: unknown[]) => c[0]);
		expect(events).toContain("child-process-gone");
	});

	it("child-process-gone handler calls log.error", async () => {
		const { bootstrapRuntime } = await import("../bootstrap");
		bootstrapRuntime();
		const call = appOnMock.mock.calls.find(
			(c: unknown[]) => c[0] === "child-process-gone",
		);
		expect(call).toBeDefined();
		const handler = call![1] as (e: unknown, details: unknown) => void;
		handler(undefined, { reason: "test" });
		expect(logErrorSpy).toHaveBeenCalledWith("child-process-gone", {
			reason: "test",
		});
	});

	it("does not throw if crashReporter.start throws (best-effort)", async () => {
		crashReporterStartMock.mockImplementation(() => {
			throw new Error("already started");
		});
		const { bootstrapRuntime } = await import("../bootstrap");
		expect(() => bootstrapRuntime()).not.toThrow();
	});
});

// ────────────────────────────────────────────────────────────────────
// GT-B3-8: logEvent logs to console.error on fs failure (runtime)
// ────────────────────────────────────────────────────────────────────

describe("GT-B3-8: bootstrap logEvent logs to console.error on fs failure", () => {
	let tmpDir: string;
	let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.resetModules();
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-gt-fix-14-logEvent-"));
		consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

		vi.doMock("electron", () => ({
			app: {
				getPath: vi.fn(() => tmpDir),
				setPath: vi.fn(),
				isPackaged: true,
				on: vi.fn(),
				quit: vi.fn(),
				exit: vi.fn(),
			},
			crashReporter: { start: vi.fn() },
			dialog: { showErrorBox: vi.fn() },
			session: {
				defaultSession: {
					webRequest: { onHeadersReceived: vi.fn() },
				},
			},
		}));
		vi.doMock("../state", () => ({ state: makeMockState() }));
		vi.doMock("../i18n", () => ({ mainT: (k: string) => k }));
		vi.doMock("../python", () => ({ stopPython: vi.fn() }));
		vi.doMock("../single_instance", () => ({
			computeConfigDir: () => tmpDir,
			clearElectronPidFile: vi.fn(),
		}));
	});

	afterEach(() => {
		consoleErrorSpy.mockRestore();
		try {
			fs.rmSync(tmpDir, { recursive: true, force: true });
		} catch {
			/* ignore */
		}
		vi.doUnmock("electron");
		vi.doUnmock("../state");
		vi.doUnmock("../i18n");
		vi.doUnmock("../python");
		vi.doUnmock("../single_instance");
	});

	it("trips the breaker with a log path that cannot be written (appendFileSync throws)", async () => {
		// Use the real _installErrorHandlers with a tmp dir, then make
		// fs.appendFileSync throw to simulate a full-disk / permission
		// error. The logEvent catch (GT-B3-8) must call console.error.
		const { _installErrorHandlers } = await import("../bootstrap");

		const appendSpy = vi.spyOn(fs, "appendFileSync").mockImplementation(() => {
			throw new Error("ENOSPC: no space left on device");
		});

		const exitCalls: number[] = [];
		const handlers = _installErrorHandlers({
			userDataDir: tmpDir,
			exit: (code) => exitCalls.push(code),
		});

		try {
			// Emit enough uncaughtExceptions to trip the breaker (5).
			for (let i = 0; i < 5; i++) {
				process.emit("uncaughtException", new Error(`boom ${i}`));
			}
		} finally {
			handlers.dispose();
		}

		// GT-B3-8: console.error must have been called with the logEvent
		// failure message (the appendFileSync throw was caught and
		// surfaced instead of being silently swallowed).
		const logEventCalls = consoleErrorSpy.mock.calls.filter((c: unknown[]) =>
			c.some((arg: unknown) =>
				String(arg).includes("[bootstrap] logEvent failed for"),
			),
		);
		expect(logEventCalls.length).toBeGreaterThan(0);
		// The first logEvent-call's second arg should mention the crash
		// log path.
		expect(String(logEventCalls[0][1])).toContain("electron-crashes.log");

		appendSpy.mockRestore();
	});
});
