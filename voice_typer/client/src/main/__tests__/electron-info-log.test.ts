/**
 * @vitest-environment node
 *
 * YJ-3 regression coverage for the opt-in INFO log persistence path.
 *
 * Background
 * ----------
 * `logging.ts`'s `logger.info` and `log.info` historically skipped file
 * writes in production (only `console.info` was called, which is a no-op
 * in packaged Electron builds — no terminal attached). The result was
 * that lifecycle events (TCP connect, Python sidecar spawned, bubble
 * shown, window created) left ZERO durable trace in packaged builds,
 * making support triage impossible.
 *
 * The fix (YJ-3) adds a `VOICE_TYPER_ELECTRON_INFO_LOG=1` env-var
 * opt-in. When set, INFO logs are routed through `appendLifecycleLine`
 * to a dedicated `electron-lifecycle.log` (1 MiB × 1 backup). When
 * unset, behavior is unchanged.
 *
 * These tests verify:
 *   (a) When the env var is set, `logger.info("test")` calls
 *       `fs.appendFileSync` with a line containing the `[INFO]` level
 *       tag and the message text.
 *   (b) When the env var is unset, `logger.info("test")` does NOT call
 *       `fs.appendFileSync` against the lifecycle log path (the only
 *       `appendFileSync` calls that may fire are against
 *       `electron-main.log`, gated by `!app.isPackaged`).
 *
 * The test mocks `electron` (so `app.getPath("userData")` returns a
 * tmp path and `app.isPackaged` returns `false` so the dev-mode
 * `electron-main.log` write is observable). It spies on
 * `fs.appendFileSync` and asserts against the file path + content
 * of the call.
 *
 * IMPORTANT: this test imports `logging.ts` TWICE in two separate
 * `describe` blocks — once with the env var set BEFORE import (so the
 * module-level `PERSIST_INFO` constant resolves to `true`), and once
 * with the env var unset. Because vitest caches modules per test file,
 * we use `vi.resetModules()` + dynamic `import()` in `beforeEach` so
 * the module re-evaluates with the current env state.
 */
import fs from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock `electron` — `app.getPath` returns a deterministic tmp path so
// the lifecycle log path is stable across runs; `app.isPackaged` is
// `false` so the dev-mode `electron-main.log` write fires (this lets
// the "env unset" test verify that ONLY the lifecycle log is skipped,
// not the entire file-write path).
vi.mock("electron", () => ({
	app: {
		getPath: () => "/tmp/vt-yj-3-test-userdata",
		isPackaged: false,
	},
}));

const MOCK_USERDATA = "/tmp/vt-yj-3-test-userdata";
const LIFECYCLE_LOG_PATH = path.join(MOCK_USERDATA, "electron-lifecycle.log");

/**
 * Dynamically import `logging.ts` AFTER setting the env var, so the
 * module-level `PERSIST_INFO` constant (`process.env.VOICE_TYPER_ELECTRON_INFO_LOG === "1"`)
 * binds to the desired value. Returns a fresh module namespace.
 *
 * `vi.resetModules()` clears the module cache so each call re-evaluates
 * the source.
 */
async function importLoggingFresh(): Promise<typeof import("../logging")> {
	vi.resetModules();
	return await import("../logging");
}

describe("YJ-3: VOICE_TYPER_ELECTRON_INFO_LOG=1 routes logger.info to electron-lifecycle.log", () => {
	let appendFileSyncSpy: ReturnType<typeof vi.spyOn>;
	const originalEnv = process.env.VOICE_TYPER_ELECTRON_INFO_LOG;

	beforeEach(async () => {
		// Set the env var BEFORE importing the module so the
		// module-level `PERSIST_INFO` constant binds to `true`.
		process.env.VOICE_TYPER_ELECTRON_INFO_LOG = "1";
		appendFileSyncSpy = vi
			.spyOn(fs, "appendFileSync")
			.mockImplementation(() => undefined);
		// Force module re-evaluation so PERSIST_INFO picks up the env.
		await importLoggingFresh();
	});

	afterEach(() => {
		appendFileSyncSpy.mockRestore();
		// Restore env.
		if (originalEnv === undefined) {
			delete process.env.VOICE_TYPER_ELECTRON_INFO_LOG;
		} else {
			process.env.VOICE_TYPER_ELECTRON_INFO_LOG = originalEnv;
		}
		vi.resetModules();
	});

	it("calls fs.appendFileSync with a line containing [INFO] and the message text", async () => {
		const { logger } = await importLoggingFresh();
		logger.info("YJ-3 test lifecycle event");

		// Find any call whose first arg is the lifecycle log path.
		const lifecycleCalls = appendFileSyncSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === LIFECYCLE_LOG_PATH,
		);
		expect(lifecycleCalls.length).toBeGreaterThanOrEqual(1);

		// The line passed as the 2nd arg must contain [INFO] and the
		// message text. The exact ISO-8601 timestamp is non-deterministic,
		// so we assert substrings only.
		const line = String(lifecycleCalls[0][1]);
		expect(line).toContain("[INFO]");
		expect(line).toContain("YJ-3 test lifecycle event");
		// The line must end with a newline so `tail -f` shows it cleanly.
		expect(line.endsWith("\n")).toBe(true);

		// YJ-FIX-D2: verify the file mode is 0o600 (sensitive INFO context —
		// PII risk if world-readable). The lifecycle log is created on first
		// INFO write, so the third `options` arg to `fs.appendFileSync` MUST
		// pass `mode: 0o600` to constrain permissions on the freshly-created
		// file. Asserting against the FIRST lifecycle call is sufficient
		// because `appendLifecycleLine` always passes the same options object
		// literal (`{ flag: "a", mode: 0o600 }`) on every write.
		expect(lifecycleCalls[0][0]).toBe(LIFECYCLE_LOG_PATH);
		expect(lifecycleCalls[0][2]).toEqual({ flag: "a", mode: 0o600 });
	});

	it("writes to the lifecycle log path under userData (not electron-main.log)", async () => {
		const { logger } = await importLoggingFresh();
		logger.info("path check");

		const lifecycleCalls = appendFileSyncSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === LIFECYCLE_LOG_PATH,
		);
		expect(lifecycleCalls.length).toBeGreaterThanOrEqual(1);
		// Sanity: the path ends with `electron-lifecycle.log` (NOT
		// `electron-main.log` — the two streams are kept separate so
		// the higher-volume INFO log doesn't push WARN/ERROR context
		// out of the 5 MiB rotation window on the main log).
		expect(LIFECYCLE_LOG_PATH.endsWith("electron-lifecycle.log")).toBe(true);
	});

	it("log.info (printf-style logger) also routes to the lifecycle log when env is set", async () => {
		const { log } = await importLoggingFresh();
		log.info("[BUBBLE] creating window at", 100, 200);

		const lifecycleCalls = appendFileSyncSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === LIFECYCLE_LOG_PATH,
		);
		expect(lifecycleCalls.length).toBeGreaterThanOrEqual(1);
		const line = String(lifecycleCalls[0][1]);
		expect(line).toContain("[INFO]");
		// printf-style logger coerces args via String() and joins with
		// spaces — verify the tag + coordinates are present.
		expect(line).toContain("[BUBBLE] creating window at");
		expect(line).toContain("100");
		expect(line).toContain("200");
	});
});

describe("YJ-3: when VOICE_TYPER_ELECTRON_INFO_LOG is UNSET, logger.info does NOT touch electron-lifecycle.log", () => {
	let appendFileSyncSpy: ReturnType<typeof vi.spyOn>;
	const originalEnv = process.env.VOICE_TYPER_ELECTRON_INFO_LOG;

	beforeEach(async () => {
		// Ensure the env var is unset so PERSIST_INFO binds to `false`.
		delete process.env.VOICE_TYPER_ELECTRON_INFO_LOG;
		appendFileSyncSpy = vi
			.spyOn(fs, "appendFileSync")
			.mockImplementation(() => undefined);
		await importLoggingFresh();
	});

	afterEach(() => {
		appendFileSyncSpy.mockRestore();
		if (originalEnv !== undefined) {
			process.env.VOICE_TYPER_ELECTRON_INFO_LOG = originalEnv;
		}
		vi.resetModules();
	});

	it("logger.info does NOT call appendFileSync on the lifecycle log path", async () => {
		const { logger } = await importLoggingFresh();
		logger.info("should not persist to lifecycle log");

		const lifecycleCalls = appendFileSyncSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === LIFECYCLE_LOG_PATH,
		);
		// Zero lifecycle-log writes — the opt-in is OFF, so the
		// behavior is unchanged from pre-YJ-3.
		expect(lifecycleCalls).toHaveLength(0);
	});

	it("log.info (printf-style) does NOT call appendFileSync on the lifecycle log path", async () => {
		const { log } = await importLoggingFresh();
		log.info("[BUBBLE] should not persist to lifecycle log");

		const lifecycleCalls = appendFileSyncSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === LIFECYCLE_LOG_PATH,
		);
		expect(lifecycleCalls).toHaveLength(0);
	});
});
