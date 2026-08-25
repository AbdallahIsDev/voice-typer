// @vitest-environment node
/**
 * R6-F7 unit tests for the belt-and-suspenders `stopPython()` shutdown
 * hooks in `index.ts` (will-quit) and `bootstrap.ts` (uncaughtException).
 *
 * These tests verify the static contract: the modules register handlers
 * that call `stopPython()`. Since we can't import `index.ts` (it calls
 * Electron APIs at module top level), we assert the registration logic
 * indirectly by reading the source and importing only the testable
 * pieces (bootstrap's `setupErrorHandlers`).
 */

import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../state";

// Mock electron.
const mockAppOn = vi.fn();
const mockAppQuit = vi.fn();
vi.mock("electron", () => ({
	app: {
		on: mockAppOn,
		quit: mockAppQuit,
		exit: vi.fn(),
		isPackaged: false,
		isQuitting: false,
		getPath: vi.fn(() => "/tmp"),
		setPath: vi.fn(),
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
		...overrides,
	} as MainState;
}

const mockState = makeMockState();
vi.mock("../state", () => ({ state: mockState }));

vi.mock("../i18n", () => ({ mainT: (k: string) => k }));
//+ : bootstrap.ts imports `stopPython` from
// `../python` (the index). The index re-exports `stopPython` from
// `./python/stop-python`, but it also pulls in `./send-to-python` →
// `../index` (the heavy main entry that fires Electron APIs at
// module-eval time). Mocking `../python` short-circuits the whole chain
// so the test can import `bootstrap.ts` without triggering the main
// entry's side effects. Both the barrel and the leaf module are mocked
// because bootstrap.ts's value import resolves through the barrel.
vi.mock("../python", () => ({ stopPython: vi.fn() }));
vi.mock("../python/stop-python", () => ({ stopPython: vi.fn() }));
vi.mock("../single_instance", () => ({
	computeConfigDir: () => "/mock",
	//bootstrap.ts also imports `clearElectronPidFile` for the
	// production exit hook. The test never exercises that hook (it
	// injects its own `exit` mock), but the import + symbol binding
	// still needs to resolve.
	clearElectronPidFile: vi.fn(),
}));

describe("R6-F7: index.ts registers app.on('will-quit', stopPython)", () => {
	it("index.ts source contains an app.on('will-quit', ...) block that calls stopPython", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../index.ts"),
			"utf-8",
		);
		//(R6-F7): the will-quit handler must exist as a
		// belt-and-suspenders shutdown hook (before-quit can be
		// suppressed by event.preventDefault(), macOS logout paths,
		// or tray close-to-tray on some platforms).
		//
		// Anchor the search on the ACTUAL handler registration
		// (`app.on("will-quit",` — note the trailing comma). A naive
		// `src.indexOf("will-quit")` would match the JSDoc summary
		// near the top of the file (`app.on("before-quit" |
		// "will-quit" | …)`), and the subsequent 500-char window
		// would include the `stopPython` import statement —
		// producing a false pass even if the handler body were
		// empty. The trailing comma is present in the real
		// `app.on("will-quit", (event) => {` call but absent in
		// the JSDoc pipe-list, so it cleanly distinguishes them.
		const idx = src.search(/app\.on\(\s*["']will-quit["']\s*,/);
		expect(idx).toBeGreaterThan(-1);
		const block = src.slice(idx, idx + 500);
		// ...and it must call stopPython() (possibly inside a try/catch).
		expect(block).toContain("stopPython");
	});

	it("index.ts source contains an app.on('before-quit', ...) block that calls stopPython", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../index.ts"),
			"utf-8",
		);
		// The before-quit handler is the PRIMARY shutdown hook
		// (fires first on normal quit paths). It must call
		// stopPython() so the Python backend is cleaned up even
		// when will-quit is suppressed (event.preventDefault(),
		// macOS logout paths, tray close-to-tray on some
		// platforms). Asserting only the registration — without
		// verifying the stopPython call — would let a regression
		// that empties the handler body pass silently (the
		// handler is "registered" but does nothing).
		//
		// Same trailing-comma anchoring as the will-quit test
		// above: the JSDoc summary line also mentions
		// `app.on("before-quit"`, so a bare `indexOf` would land
		// on the comment and the 500-char window would reach the
		// `stopPython` import — a false pass.
		const idx = src.search(/app\.on\(\s*["']before-quit["']\s*,/);
		expect(idx).toBeGreaterThan(-1);
		const block = src.slice(idx, idx + 500);
		expect(block).toContain("stopPython");
	});
});

describe("R6-F7: bootstrap.ts uncaughtException calls stopPython", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		Object.assign(mockState, makeMockState());
		vi.resetModules();
	});

	it("bootstrap.ts source imports stopPython", () => {
		// The error-handler implementation lives in the
		// `bootstrap/error-handlers.ts` leaf (split out of `bootstrap.ts`);
		// the pin follows the moved body.
		const src = fs.readFileSync(
			path.resolve(__dirname, "../bootstrap/error-handlers.ts"),
			"utf-8",
		);
		expect(src).toMatch(
			/import\s+\{[^}]*stopPython[^}]*\}\s+from\s+["']\.\.\/python["']/,
		);
	});

	it("bootstrap.ts source calls stopPython() inside the uncaughtException handler", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../bootstrap/error-handlers.ts"),
			"utf-8",
		);
		//locate the onUncaught handler definition
		// (more precise than searching for the "uncaughtException"
		// string, which also appears in JSDoc comments above the
		// handler). Assert stopPython is called within the
		// handler body.
		const handlerIdx = src.indexOf("const onUncaught");
		expect(handlerIdx).toBeGreaterThan(-1);
		const block = src.slice(handlerIdx, handlerIdx + 2500);
		expect(block).toContain("stopPython");
	});

	it("bootstrap.ts source calls stopPython() inside the unhandledRejection handler", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../bootstrap/error-handlers.ts"),
			"utf-8",
		);
		//same check for the onRejection handler.
		const handlerIdx = src.indexOf("const onRejection");
		expect(handlerIdx).toBeGreaterThan(-1);
		const block = src.slice(handlerIdx, handlerIdx + 2500);
		expect(block).toContain("stopPython");
	});

	it("bootstrapRuntime registers the uncaughtException + unhandledRejection handlers", async () => {
		const onSpy = vi.spyOn(process, "on");
		const restore: Array<() => void> = [];
		// Avoid clobbering the real process listeners — capture only.
		const originalOn = process.on.bind(process);
		const captured = new Set<string>();
		onSpy.mockImplementation(
			(event: string | symbol, handler: (...args: unknown[]) => void) => {
				captured.add(String(event));
				restore.push(() => originalOn(event, handler));
				return process;
			},
		);
		try {
			const { bootstrapRuntime } = await import("../bootstrap");
			// Avoid actually firing the CSP setup (which calls session.webRequest)
			// — it's already mocked. Just call bootstrap.
			expect(() => bootstrapRuntime()).not.toThrow();
			expect(captured).toContain("uncaughtException");
			expect(captured).toContain("unhandledRejection");
		} finally {
			onSpy.mockRestore();
		}
	});
});

// ────────────────────────────────────────────────────────────────────
// SIGKILL escalation contract for stop-python.ts.
//
// stop-python.ts sends `quit_app` over TCP, then escalates to a
// process kill if Python doesn't exit gracefully. The escalation has
// two stages:
//   1. killTimer  (KILL_TIMER_MS, currently 3s)   → SIGTERM / taskkill /T
//   2. escalateTimer (ESCALATE_TIMER_MS, 3s)      → SIGKILL / taskkill /F /T
//
// The Windows path uses `taskkill /F /T /PID` (force-kill the entire
// process tree) instead of `proc.kill("SIGKILL")` because
// `proc.kill()` on Windows is `TerminateProcess` on the IMMEDIATE
// process only — it would orphan the native hotkey binary child
// spawned by the Python sidecar.
//
// These tests pin the contract via BOTH source-text assertions (which
// always work, regardless of mock setup) and runtime assertions
// (which import the actual stop-python.ts via `vi.importActual` to
// verify the timer-firing behavior under fake timers).
// ────────────────────────────────────────────────────────────────────

describe("stop-python.ts: SIGKILL escalation contract (source-text)", () => {
	const stopPythonSrc = fs.readFileSync(
		path.resolve(__dirname, "../python/stop-python.ts"),
		"utf-8",
	);

	it("exports KILL_TIMER_MS and ESCALATE_TIMER_MS constants", () => {
		// The contract is parameterized by these two constants so
		// the runtime test can pin the firing schedule without
		// hardcoding magic numbers.
		expect(stopPythonSrc).toMatch(/export\s+const\s+KILL_TIMER_MS\s*=/);
		expect(stopPythonSrc).toMatch(/export\s+const\s+ESCALATE_TIMER_MS\s*=/);
	});

	it("ESCALATE_TIMER_MS is 3000 (extended from the prior 1500)", () => {
		// The finding specified a 3s escalation timer (was 1.5s).
		// Pin the value so a regression to the old 1.5s is caught.
		const match = stopPythonSrc.match(
			/export\s+const\s+ESCALATE_TIMER_MS\s*=\s*(\d+)/,
		);
		expect(match).not.toBeNull();
		expect(Number(match?.[1])).toBe(3000);
	});

	it("POSIX path: sends SIGTERM then escalates to SIGKILL", () => {
		// The POSIX escalation: SIGTERM (graceful) → SIGKILL (force).
		// Both signals must be present in the source.
		expect(stopPythonSrc).toMatch(/proc\.kill\(\s*["']SIGTERM["']\s*\)/);
		expect(stopPythonSrc).toMatch(/proc\.kill\(\s*["']SIGKILL["']\s*\)/);
	});

	it("Windows path: uses taskkill /F /T /PID for tree kill", () => {
		// On Windows, proc.kill() orphans children. The source
		// must invoke `taskkill` with /T (tree) and /F (force) on
		// the escalation path. /F only appears on the force-kill
		// path (the graceful attempt uses /T without /F).
		expect(stopPythonSrc).toMatch(/taskkill/);
		expect(stopPythonSrc).toMatch(/["']\/T["']/);
		expect(stopPythonSrc).toMatch(/["']\/F["']/);
		expect(stopPythonSrc).toMatch(/["']\/PID["']/);
	});

	it("branches on process.platform for the kill path", () => {
		// The platform check must be present so the Windows
		// taskkill path is actually taken on win32.
		expect(stopPythonSrc).toMatch(/process\.platform\s*===\s*["']win32["']/);
	});

	it("escalateTimer is armed with ESCALATE_TIMER_MS (no hardcoded 1500)", () => {
		// The escalation timer must reference the exported
		// constant, not a hardcoded 1500 (the old value).
		expect(stopPythonSrc).toMatch(
			/setTimeout\([\s\S]*?},\s*ESCALATE_TIMER_MS\)/,
		);
		// And the old hardcoded 1500 must NOT remain anywhere
		// in the file (defense against a regression that
		// re-introduces the old value).
		expect(stopPythonSrc).not.toMatch(/},\s*1500\)/);
	});

	it("killTimer is armed with KILL_TIMER_MS (not a hardcoded 3000)", () => {
		// The kill timer must reference the exported constant.
		expect(stopPythonSrc).toMatch(/setTimeout\([\s\S]*?},\s*KILL_TIMER_MS\)/);
	});

	it("escalateTimer is cleared when the proc emits 'exit' (graceful exit cancels escalation)", () => {
		// If Python exits gracefully (the .once('exit') path),
		// the escalateTimer must be cleared so SIGKILL doesn't
		// fire on an already-dead pid.
		expect(stopPythonSrc).toMatch(
			/proc\.once\(\s*["']exit["']\s*,\s*\(\)\s*=>\s*clearTimeout\(escalateTimer\)\s*\)/,
		);
	});

	it("imports spawnSync from node:child_process for the Windows tree kill", () => {
		// spawnSync is used for the synchronous taskkill call.
		expect(stopPythonSrc).toMatch(
			/import\s*\{\s*spawnSync\s*\}\s*from\s*["']node:child_process["']/,
		);
	});
});

// ────────────────────────────────────────────────────────────────────
// Runtime tests: import the actual stop-python.ts via vi.importActual
// (bypasses the hoisted `vi.mock("../python/stop-python", ...)`) and
// verify the timer-firing schedule under fake timers.
// ────────────────────────────────────────────────────────────────────

class _MockChildProcess extends EventEmitter {
	pid = 12345;
	killed = false;
	// The production escalation liveness check is
	// `proc.exitCode === null && proc.signalCode === null` (proc has
	// NOT actually exited). undefined would fail the `=== null` check
	// and make the SIGKILL escalation dead code, so the mock must
	// default both to null — matching a freshly-spawned, still-running
	// child process.
	exitCode: number | null = null;
	signalCode: NodeJS.Signals | null = null;
	kill = vi.fn((_signal?: NodeJS.Signals) => true);
}

describe("stop-python.ts: SIGKILL escalation contract (runtime)", () => {
	let stopPython: () => void;
	let mockProc: _MockChildProcess;
	let originalPlatform: string;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.useFakeTimers();
		Object.assign(mockState, makeMockState());
		vi.resetModules();
		// vi.importActual bypasses the hoisted
		// `vi.mock("../python/stop-python", ...)` so we get the
		// REAL module. The hoisted mocks for `../state` and
		// `../python/send-to-python` still apply to the real
		// module's transitive imports (vi.importActual only
		// bypasses the mock for the named module, not its
		// dependencies).
		const stopMod = await vi.importActual<
			typeof import("../python/stop-python")
		>("../python/stop-python");
		stopPython = stopMod.stopPython;
		mockProc = new _MockChildProcess();
		mockState.pythonProcess = mockProc as unknown as MainState["pythonProcess"];
		originalPlatform = process.platform;
	});

	afterEach(() => {
		vi.useRealTimers();
		// Restore process.platform in case a test stubbed it.
		Object.defineProperty(process, "platform", {
			value: originalPlatform,
			configurable: true,
		});
	});

	it("does NOT kill the process immediately (killTimer hasn't fired yet)", () => {
		stopPython();
		expect(mockProc.kill).not.toHaveBeenCalled();
	});

	it("after KILL_TIMER_MS, sends SIGTERM (POSIX graceful)", () => {
		// Run the POSIX branch on any host by stubbing the
		// platform (the Windows taskkill path is exercised in the
		// dedicated win32 test below; stubbing also prevents a
		// real taskkill /T /PID on the mock pid when the suite
		// runs on Windows).
		Object.defineProperty(process, "platform", {
			value: "linux",
			configurable: true,
		});
		stopPython();
		// Advance past the killTimer.
		vi.advanceTimersByTime(3000);
		expect(mockProc.kill).toHaveBeenCalledTimes(1);
		expect(mockProc.kill).toHaveBeenCalledWith("SIGTERM");
	});

	it("after KILL_TIMER_MS + ESCALATE_TIMER_MS, escalates to SIGKILL (POSIX)", () => {
		// The full escalation: SIGTERM at 3s, SIGKILL at 6s.
		// The MockChildProcess.kill doesn't actually flip
		// `killed` to true (it's a vi.fn), so the escalateTimer
		// sees `!proc.killed === true` and fires SIGKILL.
		// Stub the platform so the POSIX branch runs on any host
		// (no real taskkill on the mock pid).
		Object.defineProperty(process, "platform", {
			value: "linux",
			configurable: true,
		});
		stopPython();
		vi.advanceTimersByTime(3000);
		expect(mockProc.kill).toHaveBeenCalledWith("SIGTERM");
		// Advance past the escalateTimer.
		vi.advanceTimersByTime(3000);
		// Now SIGKILL should have been called too.
		expect(mockProc.kill).toHaveBeenCalledTimes(2);
		expect(mockProc.kill).toHaveBeenNthCalledWith(2, "SIGKILL");
	});

	it("graceful proc exit cancels the SIGKILL escalation", () => {
		// If the proc emits "exit" after SIGTERM (the graceful
		// path), the escalateTimer must be cleared so SIGKILL
		// doesn't fire on an already-dead pid. Stub the platform
		// so the POSIX branch runs on any host (no real taskkill
		// on the mock pid).
		Object.defineProperty(process, "platform", {
			value: "linux",
			configurable: true,
		});
		stopPython();
		vi.advanceTimersByTime(3000); // killTimer fires → SIGTERM
		expect(mockProc.kill).toHaveBeenCalledTimes(1);
		// Simulate graceful exit.
		mockProc.emit("exit", 0, null);
		// Advance past the escalateTimer window.
		vi.advanceTimersByTime(3000);
		// SIGKILL must NOT have been called — the escalateTimer
		// was cleared by the exit handler.
		expect(mockProc.kill).toHaveBeenCalledTimes(1);
	});

	it("on Windows, uses taskkill /F /T /PID for the escalation (not proc.kill SIGKILL)", async () => {
		// Stub process.platform to "win32" so the Windows
		// branch is taken. The spawnSync call is mocked via
		// vi.doMock on node:child_process.
		const spawnSyncMock = vi.fn((..._args: unknown[]) => ({
			status: 0,
			pid: 0,
			output: [],
		}));
		vi.doMock("node:child_process", () => ({ spawnSync: spawnSyncMock }));
		// Re-import stop-python so it picks up the mocked
		// node:child_process. vi.importActual still bypasses
		// the hoisted stop-python mock; the doMock for
		// node:child_process applies to its transitive imports.
		vi.resetModules();
		Object.defineProperty(process, "platform", {
			value: "win32",
			configurable: true,
		});
		Object.assign(mockState, makeMockState());
		const freshProc = new _MockChildProcess();
		mockState.pythonProcess =
			freshProc as unknown as MainState["pythonProcess"];

		const stopMod = await vi.importActual<
			typeof import("../python/stop-python")
		>("../python/stop-python");
		stopMod.stopPython();

		// Advance past the killTimer (3s) — Windows graceful
		// attempt: taskkill /T /PID (no /F).
		vi.advanceTimersByTime(3000);
		expect(spawnSyncMock).toHaveBeenCalledTimes(1);
		const firstCallArgs = spawnSyncMock.mock.calls[0];
		expect(firstCallArgs?.[0]).toBe("taskkill");
		// /T and /PID must be present. /F must NOT (graceful).
		expect(firstCallArgs?.[1]).toContain("/T");
		expect(firstCallArgs?.[1]).toContain("/PID");
		expect(firstCallArgs?.[1]).toContain(String(freshProc.pid));
		expect(firstCallArgs?.[1]).not.toContain("/F");

		// Advance past the escalateTimer (3s more) — Windows
		// force-kill: taskkill /F /T /PID.
		vi.advanceTimersByTime(3000);
		expect(spawnSyncMock).toHaveBeenCalledTimes(2);
		const secondCallArgs = spawnSyncMock.mock.calls[1];
		expect(secondCallArgs?.[0]).toBe("taskkill");
		expect(secondCallArgs?.[1]).toContain("/F");
		expect(secondCallArgs?.[1]).toContain("/T");
		expect(secondCallArgs?.[1]).toContain("/PID");
		expect(secondCallArgs?.[1]).toContain(String(freshProc.pid));

		// proc.kill must NEVER have been called on Windows —
		// the taskkill path replaces it entirely.
		expect(freshProc.kill).not.toHaveBeenCalled();
	});
});
