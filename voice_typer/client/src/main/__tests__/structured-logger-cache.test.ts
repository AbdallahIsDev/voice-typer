/**
 * @vitest-environment node
 *
 *  regression coverage: `appendLifecycleLine` (the opt-in INFO
 * persistence path) routes through `appendLogLine` (from
 * `./rotation.ts`) instead of an inline `stat + rename + append`.
 *
 * Background
 * ----------
 * The previous implementation inlined a `fs.statSync(p)` + conditional
 * `fs.truncateSync(p, 0)` + `fs.appendFileSync(p, line, ...)`
 * block inside `appendLifecycleLine`. The comment explicitly stated
 * "avoid the  file-size cache — the INFO stream is lower priority
 * than WARN/ERROR and the extra stat on each write is acceptable for
 * an opt-in diagnostic path." But the ONLY caller path that reaches
 * this function is `logger.info` / `log.info` when `PERSIST_INFO=1` is
 * set, and when `PERSIST_INFO=1` is set the entire purpose is
 * high-volume lifecycle logging — so the "lower priority" stream is BY
 * DEFINITION the high-volume one. Each INFO log = 1 stat + 1 open + 1
 * write + 1 close = 4 syscalls.
 *
 * The  fix routes through `appendLogLine` which already uses the
 *  file-size cache (stat on cache miss only, bump after each
 * successful append).
 *
 * These tests verify:
 *   (a) `logger.info("...")` (with `PERSIST_INFO=1`) calls `appendLogLine`
 *       (spied via the rotation module export) — NOT an inline
 *       `statSync` + `renameSync` + `appendFileSync`.
 *   (b) The line passed to `appendLogLine` contains the bare `INFO`
 *       level label and the message text.
 *   (c) The 1 MiB cap is preserved (the third arg to `appendLogLine`).
 *   (d) The lifecycle log path is passed as the first arg.
 *
 * The test mocks `electron` and uses `vi.mock("./rotation")` to spy on
 * `appendLogLine` calls. It also spies on `fs.statSync` /
 * `fs.renameSync` to assert they are NOT called from inside
 * `appendLifecycleLine` (they may still be called from inside the
 * mocked `appendLogLine`, but those calls are inside the mock and
 * therefore do not fire on the real fs).
 */
import fs from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock `electron` so `app.isPackaged` returns `false` (irrelevant to
// this test, but required for the structuredLogger module to load
// without error).
vi.mock("electron", () => ({
	app: {
		isPackaged: false,
	},
}));

// O1: log paths resolve via the dependency-free `computeConfigDir`
// leaf + `/logs` (NOT `app.getPath("userData")` anymore).
const MOCK_USERDATA = "/tmp/vt-dj-50-test-userdata";

vi.mock("../config-dir", () => ({
	computeConfigDir: () => MOCK_USERDATA,
}));

const LIFECYCLE_LOG_PATH = path.join(
	MOCK_USERDATA,
	"logs",
	"electron-lifecycle.log",
);

// Track calls to the mocked `appendLogLine`. The mock implementation
// is a no-op (does not touch the filesystem) so the test can assert
// purely on the call args without worrying about real fs side effects.
const appendLogLineMock = vi.fn();
vi.mock("../logging/rotation", () => ({
	appendLogLine: (...args: unknown[]) => appendLogLineMock(...args),
	rotateIfNeeded: vi.fn(),
	cleanConsoleMsg: vi.fn(),
	fileTimestamp: vi.fn(() => "2026-08-21  12:00:00"),
	redactPii: (s: string) => s,
	ts: vi.fn(() => "12:00:00"),
}));

/**
 * Dynamically import `logging.ts` AFTER setting the env var, so the
 * module-level `PERSIST_INFO` constant binds to `true`. Returns a fresh
 * module namespace.
 */
async function importLoggingFresh(): Promise<typeof import("../logging")> {
	vi.resetModules();
	return await import("../logging");
}

describe("DJ-50: appendLifecycleLine routes through appendLogLine (not inline stat+rename+append)", () => {
	const originalEnv = process.env.VOICE_TYPER_ELECTRON_INFO_LOG;
	let statSyncSpy: ReturnType<typeof vi.spyOn>;
	let renameSyncSpy: ReturnType<typeof vi.spyOn>;

	beforeEach(async () => {
		// Set the env var BEFORE importing so PERSIST_INFO binds to true.
		process.env.VOICE_TYPER_ELECTRON_INFO_LOG = "1";
		appendLogLineMock.mockClear();
		// Spy on fs.statSync / fs.renameSync to assert they are NOT
		// called from inside appendLifecycleLine. The mocked
		// appendLogLine is a no-op, so any statSync/renameSync calls
		// would have to come from inline code in appendLifecycleLine
		// itself (the bug we're guarding against).
		statSyncSpy = vi
			.spyOn(fs, "statSync")
			.mockImplementation(() => ({}) as fs.Stats);
		renameSyncSpy = vi
			.spyOn(fs, "renameSync")
			.mockImplementation(() => undefined);
		await importLoggingFresh();
	});

	afterEach(() => {
		statSyncSpy.mockRestore();
		renameSyncSpy.mockRestore();
		appendLogLineMock.mockReset();
		if (originalEnv === undefined) {
			delete process.env.VOICE_TYPER_ELECTRON_INFO_LOG;
		} else {
			process.env.VOICE_TYPER_ELECTRON_INFO_LOG = originalEnv;
		}
		vi.resetModules();
	});

	it("logger.info calls appendLogLine twice (main + lifecycle) with lifecycle path on second call", async () => {
		const { logger } = await importLoggingFresh();
		logger.info("DJ-50 test lifecycle event");

		// Two calls: main-log write (call 0) + lifecycle-log write (call 1).
		expect(appendLogLineMock).toHaveBeenCalledTimes(2);
		const lifecycleCall = appendLogLineMock.mock.calls[1] as unknown[];
		expect(lifecycleCall[0]).toBe(LIFECYCLE_LOG_PATH);
	});

	it("the line passed to appendLogLine (lifecycle call) contains the bare INFO label and the message text", async () => {
		const { logger } = await importLoggingFresh();
		logger.info("DJ-50 message body");

		const lifecycleCall = appendLogLineMock.mock.calls[1] as unknown[];
		const line = String(lifecycleCall[1]);
		// Canonical C-LOG-1 format: two-space separators around a bare
		// level label — no bracketed `[INFO]`.
		expect(line).toContain("  INFO  ");
		expect(line).not.toContain("[INFO]");
		expect(line).toContain("DJ-50 message body");
		// Must end with newline so tail -f shows it cleanly.
		expect(line.endsWith("\n")).toBe(true);
	});

	it("the 1 MiB cap is passed as the third arg to appendLogLine (lifecycle call)", async () => {
		const { logger } = await importLoggingFresh();
		logger.info("DJ-50 cap check");

		const lifecycleCall = appendLogLineMock.mock.calls[1] as unknown[];
		expect(lifecycleCall[2]).toBe(1 * 1024 * 1024);
	});

	it("does NOT call fs.statSync on the lifecycle log path (inline block removed)", async () => {
		const { logger } = await importLoggingFresh();
		logger.info("DJ-50 stat check");

		// The mocked appendLogLine is a no-op, so the only way
		// statSync would be called on the lifecycle log path is if
		// appendLifecycleLine had an inline stat block (the bug).
		const statCallsOnLifecycle = statSyncSpy.mock.calls.filter(
			(c: unknown[]) => c[0] === LIFECYCLE_LOG_PATH,
		);
		expect(statCallsOnLifecycle).toHaveLength(0);
	});

	it("does NOT call fs.renameSync on the lifecycle log path (inline block removed)", async () => {
		const { logger } = await importLoggingFresh();
		logger.info("DJ-50 rename check");

		const renameCallsOnLifecycle = renameSyncSpy.mock.calls.filter(
			(c: unknown[]) => c[0] === LIFECYCLE_LOG_PATH,
		);
		expect(renameCallsOnLifecycle).toHaveLength(0);
	});

	it("log.info (printf-style) also routes through appendLogLine", async () => {
		const { log } = await importLoggingFresh();
		log.info("[BUBBLE] DJ-50 printf path", 100, 200);

		// printfLogger's log.info also calls appendLifecycleLine when
		// PERSIST_INFO=1 — assert the call landed on appendLogLine.
		expect(appendLogLineMock).toHaveBeenCalledTimes(1);
		const callArgs = appendLogLineMock.mock.calls[0] as unknown[];
		expect(callArgs[0]).toBe(LIFECYCLE_LOG_PATH);
		const line = String(callArgs[1]);
		expect(line).toContain("  INFO  ");
		expect(line).toContain("[BUBBLE] DJ-50 printf path");
	});
});
