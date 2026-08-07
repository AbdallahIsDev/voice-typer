// @vitest-environment node
/**
 * Unit tests for the SIGTERM/SIGINT backstop timer in `index.ts`.
 *
 * Verifies the backstop race fix:
 *  (a) The backstop `setTimeout` delay is exactly
 *      `KILL_TIMER_MS + ESCALATE_TIMER_MS + 500` so the
 *      `escalateTimer` in `stop-python.ts` has a guaranteed window to
 *      fire SIGKILL BEFORE Electron exits. Pre-fix the delay was a
 *      hardcoded `3000` — equal to `KILL_TIMER_MS` — so on
 *      SIGTERM-with-Python-stuck-in-C-extension the unref'd backstop
 *      fired at t=3s, exited Electron, and the `escalateTimer`
 *      (scheduled for t=6s) NEVER fired. Python was orphaned, still
 *      holding the single-instance mutex.
 *  (b) The timer is `.unref()`'d so it does NOT keep the Node event
 *      loop alive on its own — if all other handles (including the
 *      non-`.unref()`'d `killTimer` in `stop-python.ts`) have settled
 *      and Python has exited cleanly, Electron can exit promptly
 *      without waiting the full backstop delay.
 *
 * The test imports the real `index.ts` (with heavy mocking of its
 * dependencies so the module-eval side effects don't escape the test
 * sandbox) and captures the `signalQuitHandler` registered via
 * `process.on("SIGTERM", ...)`. It then invokes the handler directly
 * while spying on `setTimeout` and `process.exit` to assert on the
 * backstop arming behavior without actually exiting the test process.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock electron BEFORE importing index.ts. index.ts has side effects at
// module top level (`app.setAppUserModelId`, `app.whenReady().then(…)`).
// `vi.hoisted` ensures the mock fn is initialized BEFORE the hoisted
// `vi.mock` factory runs (vi.mock factories are hoisted above all
// top-level bindings).
const { mockAppQuit } = vi.hoisted(() => ({
	mockAppQuit: vi.fn(() => {}),
}));
vi.mock("electron", () => ({
	app: {
		quit: mockAppQuit,
		exit: vi.fn(),
		whenReady: () => Promise.resolve(),
		setAppUserModelId: vi.fn(),
		isPackaged: false,
		isQuitting: false,
		on: vi.fn(),
		getPath: vi.fn(() => "/tmp"),
	},
	dialog: { showErrorBox: vi.fn() },
}));

vi.mock("../bootstrap", () => ({
	bootstrapRuntime: vi.fn(),
	setupUserData: vi.fn(),
}));
vi.mock("../dev/bubble-test", () => ({
	runBubbleTestDiagnostics: () => ({ cleanup: () => {} }),
}));
vi.mock("../ipc", () => ({ registerIpcHandlers: vi.fn() }));
vi.mock("../logging", () => ({
	BUBBLE_CLR: "",
	log: { warn: vi.fn(), info: vi.fn(), error: vi.fn() },
	RESET: "",
	ts: () => "",
}));
vi.mock("../python", () => ({
	startPython: vi.fn(),
	stopPython: vi.fn(),
}));
vi.mock("../single_instance", () => ({
	acquireSingleInstanceLock: vi.fn(),
	clearElectronPidFile: vi.fn(),
}));
vi.mock("../state", () => ({ state: {} }));
vi.mock("../tray_available", () => ({
	isLinuxWaylandWithoutSni: () => false,
}));
vi.mock("../windows", () => ({
	createWindows: vi.fn(),
	showMainWindow: vi.fn(),
}));

// Import the REAL constants from stop-python.ts so the test pins the
// contract against the canonical values (not a hardcoded 6500). This
// also implicitly verifies that index.ts imports the constants rather
// than redefining them — if a regression re-introduced a magic number,
// the source-text assertions below would catch it.
import { ESCALATE_TIMER_MS, KILL_TIMER_MS } from "../python/stop-python";

describe("index.ts: SIGTERM backstop race fix", () => {
	let originalSetTimeout: typeof setTimeout;
	let setTimeoutSpy: ReturnType<typeof vi.spyOn>;
	let processExitSpy: ReturnType<typeof vi.spyOn>;
	let unrefSpy: ReturnType<typeof vi.fn>;
	let capturedSigtermHandler: (() => void) | null;
	let capturedSigintHandler: (() => void) | null;
	let originalProcessOn: typeof process.on;

	beforeEach(async () => {
		vi.clearAllMocks();
		mockAppQuit.mockImplementation(() => {});

		// Capture the signalQuitHandler registered via
		// `process.on("SIGTERM", …)` so we can invoke it
		// directly (deterministic — no reliance on
		// `process.emit` reaching only our listener).
		capturedSigtermHandler = null;
		capturedSigintHandler = null;
		originalProcessOn = process.on.bind(process);
		vi.spyOn(process, "on").mockImplementation(((
			event: string | symbol,
			handler: (...args: unknown[]) => void,
		) => {
			if (event === "SIGTERM" && typeof handler === "function") {
				capturedSigtermHandler = handler as () => void;
			}
			if (event === "SIGINT" && typeof handler === "function") {
				capturedSigintHandler = handler as () => void;
			}
			// Delegate to the real process.on so vitest's
			// own listeners (if any) still register.
			return originalProcessOn(event, handler as (...args: unknown[]) => void);
		}) as never as typeof process.on);

		// Spy on global setTimeout. signalQuitHandler resolves
		// `setTimeout` from the global scope at CALL time, so
		// spying on globalThis.setTimeout before invoking the
		// handler intercepts the backstop arming. The mock
		// returns a fake timer object with `.unref()` so the
		// handler's `.unref()` chain doesn't crash.
		unrefSpy = vi.fn();
		originalSetTimeout = globalThis.setTimeout;
		setTimeoutSpy = vi.spyOn(globalThis, "setTimeout").mockImplementation(((
			_cb: (...args: unknown[]) => void,
			_delay?: number,
			..._rest: unknown[]
		) => {
			return {
				unref: unrefSpy,
				ref: vi.fn(),
				hasRef: () => false,
				refresh: () => ({}) as never,
				[Symbol.toPrimitive]: () => 0,
			} as never;
		}) as never);

		processExitSpy = vi
			.spyOn(process, "exit")
			.mockImplementation((() => undefined) as never);

		// Re-import index.ts so the handlers register against
		// the captured `process.on` spy. vi.resetModules
		// ensures a fresh module instance per test (no
		// `_signalQuitFired` carryover).
		vi.resetModules();
		await import("../index");
	});

	afterEach(() => {
		setTimeoutSpy.mockRestore();
		processExitSpy.mockRestore();
		vi.restoreAllMocks();
		globalThis.setTimeout = originalSetTimeout;
	});

	it("signalQuitHandler is registered for SIGTERM", () => {
		expect(capturedSigtermHandler).not.toBeNull();
	});

	it("signalQuitHandler is registered for SIGINT (same handler)", () => {
		expect(capturedSigintHandler).not.toBeNull();
	});

	it("arms backstop with delay = KILL_TIMER_MS + ESCALATE_TIMER_MS + 500 on SIGTERM", () => {
		// Clear any setTimeout calls that happened during
		// module eval (none expected, but defensive).
		setTimeoutSpy.mockClear();
		capturedSigtermHandler?.();
		// Exactly ONE setTimeout call — the backstop arming.
		expect(setTimeoutSpy).toHaveBeenCalledTimes(1);
		const [cb, delay] = setTimeoutSpy.mock.calls[0] ?? [];
		expect(cb).toBeTypeOf("function");
		expect(delay).toBe(KILL_TIMER_MS + ESCALATE_TIMER_MS + 500);
		// Sanity: KILL_TIMER_MS=3000, ESCALATE_TIMER_MS=3000 →
		// expected delay is 6500. This guards against a future
		// constant change that silently shrinks the backstop
		// back into the killTimer's window.
		expect(KILL_TIMER_MS).toBe(3000);
		expect(ESCALATE_TIMER_MS).toBe(3000);
		expect(delay).toBe(6500);
	});

	it(".unref()'s the backstop timer so it does NOT pin the event loop", () => {
		capturedSigtermHandler?.();
		// The backstop MUST be .unref()'d — without .unref(),
		// the timer would keep the event loop alive for the
		// full 6.5s even after Python has exited cleanly,
		// defeating the "exit promptly when Python is dead"
		// contract.
		expect(unrefSpy).toHaveBeenCalledTimes(1);
	});

	it("does NOT call process.exit immediately (the backstop is deferred)", () => {
		capturedSigtermHandler?.();
		// The backstop callback calls process.exit, but the
		// timer hasn't fired yet (we mocked setTimeout to a
		// no-op). So process.exit must NOT have been called
		// synchronously.
		expect(processExitSpy).not.toHaveBeenCalled();
	});

	it("SIGINT uses the same backstop contract (delay + .unref())", () => {
		setTimeoutSpy.mockClear();
		unrefSpy.mockClear();
		capturedSigintHandler?.();
		expect(setTimeoutSpy).toHaveBeenCalledTimes(1);
		const [, delay] = setTimeoutSpy.mock.calls[0] ?? [];
		expect(delay).toBe(KILL_TIMER_MS + ESCALATE_TIMER_MS + 500);
		expect(unrefSpy).toHaveBeenCalledTimes(1);
	});

	it("is idempotent — second invocation does NOT re-arm the backstop", () => {
		capturedSigtermHandler?.();
		setTimeoutSpy.mockClear();
		unrefSpy.mockClear();
		// Second invocation should be a no-op (the
		// `_signalQuitFired` guard short-circuits).
		capturedSigtermHandler?.();
		expect(setTimeoutSpy).not.toHaveBeenCalled();
		expect(unrefSpy).not.toHaveBeenCalled();
	});

	it("calls app.quit() before arming the backstop", () => {
		mockAppQuit.mockClear();
		capturedSigtermHandler?.();
		expect(mockAppQuit).toHaveBeenCalledTimes(1);
	});

	it("falls back to process.exit(0) if app.quit() throws", () => {
		mockAppQuit.mockImplementation(() => {
			throw new Error("quit failed");
		});
		processExitSpy.mockClear();
		setTimeoutSpy.mockClear();
		capturedSigtermHandler?.();
		// When app.quit() throws, the catch block calls
		// process.exit(0) SYNCHRONOUSLY — before the backstop
		// is armed.
		expect(processExitSpy).toHaveBeenCalledWith(0);
		// The backstop is STILL armed (after the catch) so the
		// process doesn't hang if the synchronous exit is
		// somehow intercepted.
		expect(setTimeoutSpy).toHaveBeenCalledTimes(1);
	});
});

// ────────────────────────────────────────────────────────────────────
// Source-text assertions: pin the contract statically so a regression
// that bypasses the test harness (e.g. someone re-introduces a magic
// number) is still caught.
// ────────────────────────────────────────────────────────────────────

import fs from "node:fs";
import path from "node:path";

describe("index.ts: SIGTERM backstop source-text contract", () => {
	const src = fs.readFileSync(path.resolve(__dirname, "../index.ts"), "utf-8");

	it("imports KILL_TIMER_MS and ESCALATE_TIMER_MS from ./python/stop-python", () => {
		// The backstop must reference the canonical constants
		// (PRESERVE ARCHITECTURE rule — do NOT redefine).
		expect(src).toMatch(
			/import\s*\{[^}]*\bKILL_TIMER_MS\b[^}]*\}\s*from\s*["']\.\/python\/stop-python["']/,
		);
		expect(src).toMatch(
			/import\s*\{[^}]*\bESCALATE_TIMER_MS\b[^}]*\}\s*from\s*["']\.\/python\/stop-python["']/,
		);
	});

	it("backstop delay is KILL_TIMER_MS + ESCALATE_TIMER_MS + 500 (no magic 3000)", () => {
		// The backstop setTimeout must use the expression
		// `KILL_TIMER_MS + ESCALATE_TIMER_MS + 500` — NOT a
		// hardcoded 3000 (the pre-fix value that raced with
		// the killTimer). Allow an optional trailing comma
		// after `500` (the source uses trailing-comma style).
		expect(src).toMatch(
			/setTimeout\(\s*\(\)\s*=>\s*process\.exit\(0\)\s*,\s*KILL_TIMER_MS\s*\+\s*ESCALATE_TIMER_MS\s*\+\s*500\s*,?\s*\)/,
		);
		// And the old hardcoded 3000 backstop must NOT remain.
		// The killTimer in stop-python.ts still uses 3000 (via
		// KILL_TIMER_MS), but index.ts must NOT have a bare
		// `setTimeout(…, 3000)` for the backstop.
		// We check the signalQuitHandler region specifically.
		const handlerIdx = src.indexOf("const signalQuitHandler");
		expect(handlerIdx).toBeGreaterThan(-1);
		const handlerBlock = src.slice(handlerIdx, handlerIdx + 600);
		expect(handlerBlock).not.toMatch(/setTimeout\([^)]*,\s*3000\s*\)/);
	});

	it("backstop timer is .unref()'d (does NOT pin the event loop)", () => {
		// The .unref() chain must be present so the backstop
		// doesn't keep Electron alive for the full 6.5s after
		// Python has exited cleanly.
		expect(src).toMatch(
			/KILL_TIMER_MS\s*\+\s*ESCALATE_TIMER_MS\s*\+\s*500\s*,?\s*\)\s*\.unref\(\)/,
		);
	});

	it("does NOT redefine KILL_TIMER_MS or ESCALATE_TIMER_MS locally", () => {
		// PRESERVE ARCHITECTURE: the constants must be
		// imported, not redefined. A local `const KILL_TIMER_MS`
		// would let the two declarations drift out of sync.
		expect(src).not.toMatch(/\b(?:const|let|var)\s+KILL_TIMER_MS\s*=/);
		expect(src).not.toMatch(/\b(?:const|let|var)\s+ESCALATE_TIMER_MS\s*=/);
	});
});
