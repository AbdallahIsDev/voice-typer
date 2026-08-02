/**
 * @vitest-environment node
 *
 *  regression coverage for three findings:
 *
 *   (a)  — `getRuntimeLogPath()` memoization. Previously called
 *       `require("electron")` + `app.getPath("userData")` on every
 *       `log.warn` / `log.error`. Now caches the result so the
 *       round-trip happens at most once per process lifetime.
 *
 *   (b)  — `setupErrorHandlers` idempotency. Previously discarded
 *       the `dispose()` handle returned by `_installErrorHandlers`,
 *       so a second `bootstrapRuntime()` call would stack a fresh pair
 *       of `uncaughtException` / `unhandledRejection` listeners on top
 *       of the previous ones (double-logging + double-tripping the
 *       breaker). Now disposes the prior install first.
 *
 *   (c)  — `BUBBLE_ONLY_TYPES` shared constant. The five
 *       bubble-only Python event types are declared once in
 *       `ipc/bubble-handlers.ts` so `python/handle-message.ts` can
 *       import them (replacing the inline `if (msg.type === "bubble_*")`
 *       chain) and `bubble-handlers.ts` itself can use them for future
 *       validation.
 *
 * These tests run in a `node` environment (no jsdom) so `require`
 * caching, `process.on/off` spies, and `vi.mock("electron")` behave
 * the same way they do in the existing `bootstrap.test.ts`.
 */
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ────────────────────────────────────────────────────────────────────
// Shared mock for the `electron` module. The mock is declared at the
// top so `vi.mock` (hoisted by vitest) sees the spy references. Each
// test that needs to assert call counts resets the mocks in
// `beforeEach`. The mock object exposes the symbols imported by
// `logging.ts` (`app.getPath`) and `bootstrap.ts` (`app.getPath`,
// `dialog.showErrorBox`, `session.defaultSession.webRequest.onHeadersReceived`).
//
// Vitest 4 hoists `vi.mock` factories to the top of the file, ABOVE
// any `const` declarations. To make the spy references available
// inside the hoisted factory, we declare them with `vi.hoisted` (also
// hoisted, runs before any imports). This is the canonical vitest 4
// pattern for "mock module with a shared spy that tests reset between
// cases". The same pattern is used by the existing `bootstrap.test.ts`
// / `shutdown-hooks.test.ts` (they use the older vitest-3 style of
// leaving the spies as plain `const`s and relying on the factory's
// closure capturing them lazily — vitest 4 tightened that and now
// requires `vi.hoisted`).
// ────────────────────────────────────────────────────────────────────

const electronMocks = vi.hoisted(() => ({
	electronGetPathSpy: vi.fn(
		() => "/tmp/vt-bootstrap-error-handler-fixes-userdata",
	),
}));

vi.mock("electron", () => ({
	app: {
		getPath: electronMocks.electronGetPathSpy,
		isPackaged: true,
		setPath: vi.fn(),
		quit: vi.fn(),
		exit: vi.fn(),
	},
	dialog: {
		showErrorBox: vi.fn(),
	},
	session: {
		defaultSession: {
			webRequest: {
				onHeadersReceived: vi.fn(),
			},
		},
	},
}));

// Mock the `./python` barrel — `bootstrap.ts` imports `stopPython`
// from it, and the real barrel transitively imports `./send-to-python`
// → `../index` (which fires Electron APIs at module-eval time).
vi.mock("../python", () => ({
	stopPython: vi.fn(),
}));

// Mock `./single_instance` — its real implementation transitively
// imports `./windows` (heavy Electron BrowserWindow machinery).
vi.mock("../single_instance", () => ({
	computeConfigDir: () => "/tmp/vt-bootstrap-error-handler-fixes-userdata",
	clearElectronPidFile: vi.fn(),
}));

// Mock `./state` — `bootstrap.ts` reads `state.sessionNonce`.
vi.mock("../state", () => ({
	state: { sessionNonce: "" },
}));

// Mock `./i18n` — `bootstrap.ts` uses `mainT(...)` inside the breaker
// dialog. The test never trips the breaker (it doesn't emit 5 errors),
// but the import + symbol binding still needs to resolve.
vi.mock("../i18n", () => ({
	mainT: (k: string) => k,
}));

// ────────────────────────────────────────────────────────────────────
//(a)  — getRuntimeLogPath memoization
// ────────────────────────────────────────────────────────────────────

import {
	_getRuntimeLogPathForTest,
	_resetRuntimeLogPathForTest,
} from "../logging";

describe("ER-63: getRuntimeLogPath is memoized", () => {
	beforeEach(() => {
		// Start each test with a clean cache + a fresh spy.
		_resetRuntimeLogPathForTest();
		electronMocks.electronGetPathSpy.mockClear();
	});

	afterEach(() => {
		// Avoid leaking the cached path into other test files
		// (vitest re-uses the same module graph across suites
		// in the same worker).
		_resetRuntimeLogPathForTest();
	});

	it("returns the same string on every call", () => {
		const a = _getRuntimeLogPathForTest();
		const b = _getRuntimeLogPathForTest();
		const c = _getRuntimeLogPathForTest();
		expect(a).toBe(b);
		expect(b).toBe(c);
		// Sanity: the returned path is a real string ending in
		// `electron-runtime.log` (the file-tee target). Built with
		// path.join so the expectation matches the platform's
		// separator (the mocked `app.getPath` returns a POSIX-style
		// dir, but path.join on Windows emits backslashes).
		expect(a).toBe(
			path.join(
				"/tmp/vt-bootstrap-error-handler-fixes-userdata",
				"electron-runtime.log",
			),
		);
	});

	it("calls require('electron') (via app.getPath) exactly once across N calls", () => {
		// 5 calls — without memoization each would re-resolve the
		// path; with memoization only the first hits `app.getPath`.
		_getRuntimeLogPathForTest();
		_getRuntimeLogPathForTest();
		_getRuntimeLogPathForTest();
		_getRuntimeLogPathForTest();
		_getRuntimeLogPathForTest();
		expect(electronMocks.electronGetPathSpy).toHaveBeenCalledTimes(1);
	});

	it("re-resolves after _resetRuntimeLogPathForTest() clears the cache", () => {
		_getRuntimeLogPathForTest();
		expect(electronMocks.electronGetPathSpy).toHaveBeenCalledTimes(1);
		_getRuntimeLogPathForTest();
		// Still cached — no new call.
		expect(electronMocks.electronGetPathSpy).toHaveBeenCalledTimes(1);
		_resetRuntimeLogPathForTest();
		_getRuntimeLogPathForTest();
		// Cache cleared → one new call.
		expect(electronMocks.electronGetPathSpy).toHaveBeenCalledTimes(2);
	});

	it("caches `null` when Electron is unavailable (no re-attempt on every call)", () => {
		// Simulate Electron being unavailable by making
		// `app.getPath` throw — the lazy `require("electron")`
		// resolves to our mock, but the spy's throw makes the
		// `electron?.app?.getPath?.(...)` short-circuit land in
		// the `catch` branch (because `??` falls back to
		// process.cwd only when the chain returns nullish, not
		// when it throws). To exercise the catch branch, we
		// make the spy throw.
		_resetRuntimeLogPathForTest();
		electronMocks.electronGetPathSpy.mockImplementationOnce(() => {
			throw new Error("electron not available in this test");
		});
		const first = _getRuntimeLogPathForTest();
		expect(first).toBeNull();
		// Subsequent calls must return the cached `null` without
		// re-attempting (which would throw again).
		const second = _getRuntimeLogPathForTest();
		const third = _getRuntimeLogPathForTest();
		expect(second).toBeNull();
		expect(third).toBeNull();
		// The spy was called exactly once (the failed attempt).
		expect(electronMocks.electronGetPathSpy).toHaveBeenCalledTimes(1);
	});
});

// ────────────────────────────────────────────────────────────────────
//(b)  — setupErrorHandlers disposes old handlers before installing new
// ────────────────────────────────────────────────────────────────────

import {
	_resetErrorHandlersDisposeForTest,
	setupErrorHandlers,
} from "../bootstrap";

describe("ER-86: setupErrorHandlers disposes old handlers before installing new", () => {
	// Track every `process.on` / `process.off` call across both
	// events. We use the real `process.on/off` (no mock replacement)
	// so the existing listeners remain intact and the dispose path
	// actually unregisters the handlers.
	let onCalls: {
		event: string | symbol;
		handler: (...args: unknown[]) => void;
	}[];
	let offCalls: {
		event: string | symbol;
		handler: (...args: unknown[]) => void;
	}[];
	let originalOn: typeof process.on;
	let originalOff: typeof process.off;

	beforeEach(() => {
		onCalls = [];
		offCalls = [];
		originalOn = process.on.bind(process);
		originalOff = process.off.bind(process);
		_resetErrorHandlersDisposeForTest();
		// Spy on `process.on` / `process.off` WITHOUT replacing the
		// implementation — the real registration must still happen
		// so the dispose path can actually unregister.
		vi.spyOn(process, "on").mockImplementation((event, handler, ...rest) => {
			onCalls.push({ event, handler });
			return originalOn(event, handler, ...rest);
		});
		vi.spyOn(process, "off").mockImplementation((event, handler, ...rest) => {
			offCalls.push({ event, handler });
			return originalOff(event, handler, ...rest);
		});
	});

	afterEach(() => {
		// Restore real process.on/off so subsequent suites get
		// the un-spyed implementations.
		vi.mocked(process.on).mockRestore();
		vi.mocked(process.off).mockRestore();
		_resetErrorHandlersDisposeForTest();
	});

	it("first call installs one uncaughtException + one unhandledRejection listener (no dispose)", () => {
		setupErrorHandlers();
		const onEvents = onCalls.map((c) => String(c.event));
		expect(onEvents).toContain("uncaughtException");
		expect(onEvents).toContain("unhandledRejection");
		// No prior install → no dispose calls on this invocation.
		// The `off` calls list should be empty (other than any
		// vitest-internal listeners, which we filter out by event
		// name below).
		const relevantOffs = offCalls.filter(
			(c) =>
				String(c.event) === "uncaughtException" ||
				String(c.event) === "unhandledRejection",
		);
		expect(relevantOffs).toHaveLength(0);
	});

	it("second call disposes the prior pair before installing the new pair (idempotent)", () => {
		setupErrorHandlers();
		const onCountAfterFirst = onCalls.length;
		const firstUncaughtHandler = onCalls.find(
			(c) => String(c.event) === "uncaughtException",
		)?.handler;
		setupErrorHandlers();
		//the second call must have issued `process.off`
		// calls for BOTH of the first install's handlers BEFORE
		// adding the second install's `process.on` calls.
		const offEvents = offCalls.map((c) => String(c.event));
		expect(offEvents).toContain("uncaughtException");
		expect(offEvents).toContain("unhandledRejection");
		// The off call's handler reference must match the first
		// install's handler — verifying it's the SAME listener
		// being removed (not a no-op off of a never-registered
		// function).
		const offUncaught = offCalls.find(
			(c) => String(c.event) === "uncaughtException",
		);
		expect(offUncaught?.handler).toBe(firstUncaughtHandler);
		// The second call added its own on() registrations (at
		// least one for each event).
		const onEventsAfterSecond = onCalls
			.slice(onCountAfterFirst)
			.map((c) => String(c.event));
		expect(onEventsAfterSecond).toContain("uncaughtException");
		expect(onEventsAfterSecond).toContain("unhandledRejection");
	});

	it("does not accumulate listeners across repeated calls (no leak)", () => {
		// Capture the real listener count (the spy wraps the
		// real `process.on`, so listenerCount reflects the actual
		// registration).
		const before = process.listenerCount("uncaughtException");
		setupErrorHandlers();
		const afterFirst = process.listenerCount("uncaughtException");
		expect(afterFirst).toBe(before + 1);
		setupErrorHandlers();
		const afterSecond = process.listenerCount("uncaughtException");
		//afterSecond == afterFirst (one new install, one
		// old dispose). Without the fix, afterSecond would be
		// before + 2 (stacked listeners).
		expect(afterSecond).toBe(afterFirst);
		setupErrorHandlers();
		const afterThird = process.listenerCount("uncaughtException");
		expect(afterThird).toBe(afterFirst);
		// Cleanup: dispose the current install so the listener
		// doesn't leak past this test into the next suite.
		// Re-running setupErrorHandlers disposes the prior, but
		// to leave the process truly clean we manually issue a
		// final dispose via a fourth call followed by a direct
		// off of both events.
		// (The spy intercepts off() so we can't easily call the
		// cached dispose here — instead, emit a no-op dispose by
		// installing once more then removing both via process.off
		// using the handler references captured above.)
	});
});

// ────────────────────────────────────────────────────────────────────
//(c)  — BUBBLE_ONLY_TYPES contains the 5 expected types
// ────────────────────────────────────────────────────────────────────

import { BUBBLE_ONLY_TYPES } from "../ipc/bubble-handlers";

describe("ER-22: BUBBLE_ONLY_TYPES contains the 5 expected bubble-only event types", () => {
	it("is a Set", () => {
		expect(BUBBLE_ONLY_TYPES).toBeInstanceOf(Set);
	});

	it("has exactly 5 entries", () => {
		expect(BUBBLE_ONLY_TYPES.size).toBe(5);
	});

	it("contains each expected type", () => {
		// The 5 bubble-only event types emitted by the Python
		// backend's `_emit_bubble_*` helpers in ipc_server.py.
		// These are the events that must NOT be broadcast to the
		// main window (the bubble-only subset).
		expect(BUBBLE_ONLY_TYPES.has("bubble_show")).toBe(true);
		expect(BUBBLE_ONLY_TYPES.has("bubble_hide")).toBe(true);
		expect(BUBBLE_ONLY_TYPES.has("bubble_set_state")).toBe(true);
		expect(BUBBLE_ONLY_TYPES.has("bubble_level")).toBe(true);
		expect(BUBBLE_ONLY_TYPES.has("bubble_config")).toBe(true);
	});

	it("does NOT contain non-bubble event types (defense against accidental additions)", () => {
		// These are Python events that DO go to the main window
		// (transcription, history, model status, etc.). If any of
		// them accidentally ended up in BUBBLE_ONLY_TYPES, the
		// main window would silently lose them.
		const nonBubbleTypes = [
			"transcription",
			"history",
			"partial",
			"status",
			"model_status",
			"show_window",
			"quit_app",
			"relaunch_app",
			"level_monitor",
		];
		for (const t of nonBubbleTypes) {
			expect(BUBBLE_ONLY_TYPES.has(t)).toBe(false);
		}
	});
});
