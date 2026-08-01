// @vitest-environment node
/**
 * Regression coverage: structuredLogger path resolver memoization.
 *
 * `mainLogPath()` / `lifecycleLogPath()` / `rendererErrorsLogPath()`
 * previously called `app.getPath("userData")` on every invocation.
 * Since `logger.warn` / `logger.error` / `logger.info` / `logger.debug`
 * all funnel through `mainLogPath()`, a hot crash-loop path issued
 * a `getPath` call per log line. The fix memoizes each path for the
 * process lifetime (mirroring `getRuntimeLogPath()` in printfLogger.ts).
 *
 * Tests:
 *   1. Each path resolver calls `app.getPath("userData")` exactly once
 *      across N calls.
 *   2. `_resetMainLogPathForTest()` clears the cache → next call
 *      re-resolves.
 *   3. The cached value is the same on every call.
 *   4. `app.getPath` throwing falls back to `process.cwd()` (and caches
 *      the fallback so subsequent calls don't re-attempt).
 *   5. Each resolver has an independent memoization slot.
 */
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const electronMocks = vi.hoisted(() => ({
	electronGetPathSpy: vi.fn(() => "/tmp/vt-log-path-memo-test-userdata"),
}));

vi.mock("electron", () => ({
	app: {
		getPath: electronMocks.electronGetPathSpy,
		isPackaged: false,
	},
}));

import {
	_resetMainLogPathForTest,
	lifecycleLogPath,
	mainLogPath,
	rendererErrorsLogPath,
} from "../structuredLogger";

const MOCK_USERDATA = "/tmp/vt-log-path-memo-test-userdata";

describe("structuredLogger path resolvers are memoized", () => {
	beforeEach(() => {
		_resetMainLogPathForTest();
		electronMocks.electronGetPathSpy.mockClear();
		electronMocks.electronGetPathSpy.mockImplementation(() => MOCK_USERDATA);
	});

	afterEach(() => {
		_resetMainLogPathForTest();
	});

	it("mainLogPath returns the same string on every call", () => {
		const a = mainLogPath();
		const b = mainLogPath();
		const c = mainLogPath();
		expect(a).toBe(b);
		expect(b).toBe(c);
		expect(a).toBe(path.join(MOCK_USERDATA, "electron-main.log"));
	});

	it("mainLogPath calls app.getPath exactly once across N calls", () => {
		mainLogPath();
		mainLogPath();
		mainLogPath();
		mainLogPath();
		mainLogPath();
		expect(electronMocks.electronGetPathSpy).toHaveBeenCalledTimes(1);
	});

	it("lifecycleLogPath calls app.getPath exactly once across N calls", () => {
		lifecycleLogPath();
		lifecycleLogPath();
		lifecycleLogPath();
		expect(electronMocks.electronGetPathSpy).toHaveBeenCalledTimes(1);
		expect(lifecycleLogPath()).toBe(
			path.join(MOCK_USERDATA, "electron-lifecycle.log"),
		);
	});

	it("rendererErrorsLogPath calls app.getPath exactly once across N calls", () => {
		rendererErrorsLogPath();
		rendererErrorsLogPath();
		rendererErrorsLogPath();
		expect(electronMocks.electronGetPathSpy).toHaveBeenCalledTimes(1);
		expect(rendererErrorsLogPath()).toBe(
			path.join(MOCK_USERDATA, "electron-renderer-errors.log"),
		);
	});

	it("re-resolves after _resetMainLogPathForTest() clears the cache", () => {
		mainLogPath();
		expect(electronMocks.electronGetPathSpy).toHaveBeenCalledTimes(1);
		mainLogPath();
		expect(electronMocks.electronGetPathSpy).toHaveBeenCalledTimes(1);
		_resetMainLogPathForTest();
		mainLogPath();
		expect(electronMocks.electronGetPathSpy).toHaveBeenCalledTimes(2);
	});

	it("falls back to process.cwd() (and caches) when app.getPath throws", () => {
		electronMocks.electronGetPathSpy.mockImplementationOnce(() => {
			throw new Error("electron not available in this test");
		});
		const first = mainLogPath();
		expect(first).toBe(path.join(process.cwd(), "electron-main.log"));
		// Subsequent calls must return the cached fallback WITHOUT
		// re-attempting (which would throw again).
		const second = mainLogPath();
		const third = mainLogPath();
		expect(second).toBe(first);
		expect(third).toBe(first);
		expect(electronMocks.electronGetPathSpy).toHaveBeenCalledTimes(1);
	});

	it("mainLogPath memoization is independent of lifecycleLogPath memoization", () => {
		// Each resolver has its own memoization slot. Calling mainLogPath
		// does NOT populate lifecycleLogPath's slot, and vice versa.
		mainLogPath();
		expect(electronMocks.electronGetPathSpy).toHaveBeenCalledTimes(1);
		lifecycleLogPath();
		expect(electronMocks.electronGetPathSpy).toHaveBeenCalledTimes(2);
		rendererErrorsLogPath();
		expect(electronMocks.electronGetPathSpy).toHaveBeenCalledTimes(3);
		// Subsequent calls to all three are cache hits — no new getPath calls.
		mainLogPath();
		lifecycleLogPath();
		rendererErrorsLogPath();
		expect(electronMocks.electronGetPathSpy).toHaveBeenCalledTimes(3);
	});
});
