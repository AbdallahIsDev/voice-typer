// @vitest-environment node
/**
 * Regression coverage: structuredLogger path resolver memoization.
 *
 * `mainLogPath()` / `lifecycleLogPath()` / `rendererErrorsLogPath()`
 * resolve under `<config-dir>/logs/` via the dependency-free
 * `computeConfigDir()` leaf (O1 logs → logs/ migration).
 * Since `logger.warn` / `logger.error` / `logger.info` / `logger.debug`
 * all funnel through `mainLogPath()`, a hot crash-loop path issued
 * a `computeConfigDir` call per log line. The fix memoizes each path
 * for the process lifetime (mirroring `getRuntimeLogPath()` in
 * printfLogger.ts).
 *
 * Tests:
 *   1. Each path resolver calls `computeConfigDir()` exactly once
 *      across N calls.
 *   2. `_resetMainLogPathForTest()` clears the cache → next call
 *      re-resolves.
 *   3. The cached value is the same on every call.
 *   4. `computeConfigDir` throwing falls back to
 *      `process.cwd()/logs` (and caches the fallback so subsequent
 *      calls don't re-attempt).
 *   5. Each resolver has an independent memoization slot.
 */
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const configDirMocks = vi.hoisted(() => ({
	computeConfigDir: vi.fn(() => "/tmp/vt-log-path-memo-test-config"),
}));

vi.mock("../../config-dir", () => ({
	computeConfigDir: configDirMocks.computeConfigDir,
}));

vi.mock("electron", () => ({
	app: {
		isPackaged: false,
	},
}));

import {
	_resetMainLogPathForTest,
	lifecycleLogPath,
	mainLogPath,
	rendererErrorsLogPath,
} from "../structuredLogger";

const MOCK_CONFIG_DIR = "/tmp/vt-log-path-memo-test-config";
const MOCK_LOGS_DIR = path.join(MOCK_CONFIG_DIR, "logs");

describe("structuredLogger path resolvers are memoized", () => {
	beforeEach(() => {
		_resetMainLogPathForTest();
		configDirMocks.computeConfigDir.mockClear();
		configDirMocks.computeConfigDir.mockImplementation(() => MOCK_CONFIG_DIR);
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
		expect(a).toBe(path.join(MOCK_LOGS_DIR, "electron-main.log"));
	});

	it("mainLogPath calls computeConfigDir exactly once across N calls", () => {
		mainLogPath();
		mainLogPath();
		mainLogPath();
		mainLogPath();
		mainLogPath();
		expect(configDirMocks.computeConfigDir).toHaveBeenCalledTimes(1);
	});

	it("lifecycleLogPath calls computeConfigDir exactly once across N calls", () => {
		lifecycleLogPath();
		lifecycleLogPath();
		lifecycleLogPath();
		expect(configDirMocks.computeConfigDir).toHaveBeenCalledTimes(1);
		expect(lifecycleLogPath()).toBe(
			path.join(MOCK_LOGS_DIR, "electron-lifecycle.log"),
		);
	});

	it("rendererErrorsLogPath calls computeConfigDir exactly once across N calls", () => {
		rendererErrorsLogPath();
		rendererErrorsLogPath();
		rendererErrorsLogPath();
		expect(configDirMocks.computeConfigDir).toHaveBeenCalledTimes(1);
		expect(rendererErrorsLogPath()).toBe(
			path.join(MOCK_LOGS_DIR, "electron-renderer-errors.log"),
		);
	});

	it("re-resolves after _resetMainLogPathForTest() clears the cache", () => {
		mainLogPath();
		expect(configDirMocks.computeConfigDir).toHaveBeenCalledTimes(1);
		mainLogPath();
		expect(configDirMocks.computeConfigDir).toHaveBeenCalledTimes(1);
		_resetMainLogPathForTest();
		mainLogPath();
		expect(configDirMocks.computeConfigDir).toHaveBeenCalledTimes(2);
	});

	it("falls back to process.cwd()/logs (and caches) when computeConfigDir throws", () => {
		configDirMocks.computeConfigDir.mockImplementationOnce(() => {
			throw new Error("config-dir resolution failed in this test");
		});
		const first = mainLogPath();
		expect(first).toBe(path.join(process.cwd(), "logs", "electron-main.log"));
		// Subsequent calls must return the cached fallback WITHOUT
		// re-attempting (which would throw again).
		const second = mainLogPath();
		const third = mainLogPath();
		expect(second).toBe(first);
		expect(third).toBe(first);
		expect(configDirMocks.computeConfigDir).toHaveBeenCalledTimes(1);
	});

	it("mainLogPath memoization is independent of lifecycleLogPath memoization", () => {
		// Each resolver has its own memoization slot. Calling mainLogPath
		// does NOT populate lifecycleLogPath's slot, and vice versa.
		mainLogPath();
		expect(configDirMocks.computeConfigDir).toHaveBeenCalledTimes(1);
		lifecycleLogPath();
		expect(configDirMocks.computeConfigDir).toHaveBeenCalledTimes(2);
		rendererErrorsLogPath();
		expect(configDirMocks.computeConfigDir).toHaveBeenCalledTimes(3);
		// Subsequent calls to all three are cache hits — no new calls.
		mainLogPath();
		lifecycleLogPath();
		rendererErrorsLogPath();
		expect(configDirMocks.computeConfigDir).toHaveBeenCalledTimes(3);
	});
});
