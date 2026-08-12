// @vitest-environment node
/**
 * stop-python.ts SIGTERM→SIGKILL escalation + index.ts race-free
 * will-quit teardown.
 *
 * (TC-40) — this file's two describe blocks were previously
 * `describe.skip` because they asserted the OLD contract:
 *   - stop-python.ts's killTimer sent SIGKILL directly at 3s, and
 *   - index.ts's will-quit armed a 3s forceExitTimer.
 *
 * Production deliberately refactored both:
 *   - stop-python.ts now sends SIGTERM at `KILL_TIMER_MS` (graceful —
 *     Python's signal handlers flush history_db, close audio streams,
 *     release the single-instance mutex) and escalates to SIGKILL at
 *     `KILL_TIMER_MS + ESCALATE_TIMER_MS` when the proc has NOT exited
 *     (stuck in a C extension holding the GIL). On Windows it sends
 *     `taskkill /T /PID` (graceful tree kill) then `taskkill /F /T /PID`
 *     (force tree kill) instead of `proc.kill()`.
 *   - index.ts removed the 3s forceExitTimer that raced the killTimer;
 *     the will-quit handler now defers to stopPython()'s escalation and
 *     `pythonProcess.once("exit")` → `app.exit(0)` (the SIGTERM backstop
 *     in index.ts is `KILL_TIMER_MS + ESCALATE_TIMER_MS + 500`,
 *     `.unref()`'d — pinned behaviorally by sigterm-backstop.test.ts).
 *
 * These tests are the "un-skipped" replacement: they assert the CURRENT
 * contract so the file is live again. The POSIX behavioral tests run on
 * Linux/macOS CI; the Windows `taskkill` tests run on win32 (the
 * existing `python/__tests__/stop-python-sigkill-escalation.test.ts`
 * skips on win32, so the Windows branch has NO other behavioral
 * coverage); the source-text tests run everywhere.
 */

import fs from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../state";

const IS_WIN = process.platform === "win32";

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

// stop-python.ts imports clearTcpStartupTimeout from tcp-connect.
vi.mock("../python/tcp-connect", () => ({
	clearTcpStartupTimeout: vi.fn(),
}));

// `_treeKillWindows` uses spawnSync — spied for the Windows taskkill tests.
const { mockSpawnSync } = vi.hoisted(() => ({
	mockSpawnSync: vi.fn(() => ({ status: 0 })),
}));
vi.mock("node:child_process", () => ({ spawnSync: mockSpawnSync }));

/**
 * Mock ChildProcess mirroring Node's real `exitCode` / `signalCode`
 * semantics (`null` while alive). `autoExitOnKill: true` emits an
 * "exit" event on kill (graceful); `false` ignores the signal
 * (stuck-in-C-extension case). Mirrors the harness in
 * `python/__tests__/stop-python-sigkill-escalation.test.ts`.
 */
function makeMockProc(opts: { autoExitOnKill?: boolean } = {}) {
	const { autoExitOnKill = true } = opts;
	const listeners: Record<string, Array<(...a: unknown[]) => void>> = {};
	const proc = {
		pid: 99999,
		killed: false,
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
				queueMicrotask(() => {
					proc.signalCode = sig ?? "SIGTERM";
					proc.exitCode = null;
					(listeners.exit ?? []).forEach((cb) => {
						cb(null, proc.signalCode);
					});
				});
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
describe("stop-python.ts SIGTERM→SIGKILL escalation (current contract)", () => {
	let stopPython: () => void;
	let KILL_TIMER_MS: number;
	let ESCALATE_TIMER_MS: number;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.useFakeTimers();
		Object.assign(mockState, makeMockState());
		vi.resetModules();
		const mod = await import("../python/stop-python");
		stopPython = mod.stopPython;
		KILL_TIMER_MS = mod.KILL_TIMER_MS;
		ESCALATE_TIMER_MS = mod.ESCALATE_TIMER_MS;
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	// The old skipped test asserted the killTimer sent SIGKILL at 3s.
	// The current contract is graceful-first: SIGTERM at KILL_TIMER_MS.
	it.skipIf(IS_WIN)(
		"killTimer sends SIGTERM (graceful) at KILL_TIMER_MS — NOT SIGKILL",
		async () => {
			const proc = makeMockProc({ autoExitOnKill: false });
			mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];

			stopPython();
			// Before the grace period, no kill yet.
			expect(proc.kill).not.toHaveBeenCalled();
			// Advance past the 3s killTimer.
			await vi.advanceTimersByTimeAsync(KILL_TIMER_MS);
			expect(proc.kill).toHaveBeenCalledTimes(1);
			// Graceful-first: the FIRST signal is SIGTERM, not SIGKILL.
			expect(proc.kill).toHaveBeenCalledWith("SIGTERM");
		},
	);

	it.skipIf(IS_WIN)(
		"escalateTimer sends SIGKILL at KILL_TIMER_MS + ESCALATE_TIMER_MS when the proc ignores SIGTERM",
		async () => {
			// autoExitOnKill: false — proc stuck in a C extension holding
			// the GIL; SIGTERM is queued but never delivered.
			const proc = makeMockProc({ autoExitOnKill: false });
			mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];

			stopPython();
			await vi.advanceTimersByTimeAsync(KILL_TIMER_MS);
			expect(proc.kill).toHaveBeenCalledWith("SIGTERM");

			// Cross the escalation threshold — SIGKILL must fire (the
			// proc never exited, so exitCode/signalCode are still null).
			await vi.advanceTimersByTimeAsync(ESCALATE_TIMER_MS);
			expect(proc.kill).toHaveBeenCalledWith("SIGKILL");
			expect(proc.kill).toHaveBeenCalledTimes(2);
		},
	);

	it.skipIf(IS_WIN)(
		"no SIGKILL when the proc exits gracefully on SIGTERM",
		async () => {
			const proc = makeMockProc({ autoExitOnKill: true });
			mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];

			stopPython();
			await vi.advanceTimersByTimeAsync(KILL_TIMER_MS);
			expect(proc.kill).toHaveBeenCalledWith("SIGTERM");
			// Drain the queueMicrotask that emits "exit" → clears the
			// escalateTimer via proc.once("exit").
			await Promise.resolve();
			await Promise.resolve();

			await vi.advanceTimersByTimeAsync(ESCALATE_TIMER_MS);
			expect(proc.kill).toHaveBeenCalledTimes(1);
			expect(proc.kill).not.toHaveBeenCalledWith("SIGKILL");
		},
	);

	// Windows branch — the existing escalation test file skips on win32,
	// so the taskkill tree-kill behavior has NO other behavioral coverage.
	it.skipIf(!IS_WIN)(
		"killTimer runs taskkill /T /PID (graceful tree kill) on Windows",
		async () => {
			const proc = makeMockProc({ autoExitOnKill: false });
			mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];

			stopPython();
			await vi.advanceTimersByTimeAsync(KILL_TIMER_MS);
			// Graceful attempt: NO /F flag (WM_CLOSE-style, not force).
			expect(mockSpawnSync).toHaveBeenCalledWith(
				"taskkill",
				["/T", "/PID", "99999"],
				expect.objectContaining({ stdio: "ignore" }),
			);
		},
	);

	it.skipIf(!IS_WIN)(
		"escalateTimer runs taskkill /F /T /PID (force tree kill) on Windows when the tree survives",
		async () => {
			const proc = makeMockProc({ autoExitOnKill: false });
			mockState.pythonProcess = proc as unknown as MainState["pythonProcess"];

			stopPython();
			await vi.advanceTimersByTimeAsync(KILL_TIMER_MS + ESCALATE_TIMER_MS);
			// Force escalation: /F must be present.
			expect(mockSpawnSync).toHaveBeenCalledWith(
				"taskkill",
				["/F", "/T", "/PID", "99999"],
				expect.objectContaining({ stdio: "ignore" }),
			);
		},
	);

	it("killTimer is NOT .unref()'d (must keep Electron alive until Python is dead)", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../python/stop-python.ts"),
			"utf-8",
		);
		expect(src).not.toMatch(/killTimer\.unref\(\)/);
	});

	it("stop-python.ts source: SIGTERM appears BEFORE SIGKILL (escalation order)", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../python/stop-python.ts"),
			"utf-8",
		);
		const termIdx = src.indexOf('proc.kill("SIGTERM")');
		const killIdx = src.indexOf('proc.kill("SIGKILL")');
		expect(termIdx).toBeGreaterThan(-1);
		expect(killIdx).toBeGreaterThan(termIdx);
	});
});

describe("index.ts will-quit: race-free teardown (current contract)", () => {
	it("will-quit handler does NOT define a forceExitTimer (the pre-fix race is removed)", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../index.ts"),
			"utf-8",
		);
		const idx = src.search(/app\.on\(\s*["']will-quit["']\s*,/);
		expect(idx).toBeGreaterThan(-1);
		const block = src.slice(idx, idx + 1200);
		expect(block).not.toMatch(/forceExitTimer/);
		// The handler defers to stopPython() + pythonProcess.once("exit")
		// → app.exit(0) instead of arming its own exit timer.
		expect(block).toContain("stopPython()");
		expect(block).toMatch(/pythonProcess\.once\(\s*["']exit["']/);
	});

	it("index.ts imports KILL_TIMER_MS/ESCALATE_TIMER_MS from ./python/stop-python (no redefinition)", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../index.ts"),
			"utf-8",
		);
		expect(src).toMatch(
			/import\s*\{[^}]*\bKILL_TIMER_MS\b[^}]*\}\s*from\s*["']\.\/python\/stop-python["']/,
		);
		expect(src).toMatch(
			/import\s*\{[^}]*\bESCALATE_TIMER_MS\b[^}]*\}\s*from\s*["']\.\/python\/stop-python["']/,
		);
		expect(src).not.toMatch(/\b(?:const|let|var)\s+KILL_TIMER_MS\s*=/);
	});
});
