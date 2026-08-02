// @vitest-environment node
/**
 *  regression tests for `appendLogLine` file-permission hardening.
 *
 * Background
 * ----------
 * `rotation.ts:134` previously called
 *   `fs.appendFileSync(filePath, line, { encoding: "utf-8" })`
 * with NO `mode` option — the file was created with the process umask
 * (typically 0o644 on POSIX = world-readable). Per  the
 * Electron loggers (`electron-main.log`, `electron-runtime.log`,
 * `electron-renderer-errors.log`) have no PII redaction, so dictated-
 * text fragments may be present in these files. The sibling
 * `appendLifecycleLine` (structuredLogger.ts:114) already passed
 * `{ flag: "a", mode: 0o600 }` — this is a parity fix.
 *
 *  fix:
 *   1. `appendLogLine` now passes `{ flag: "a", mode: 0o600 }` so
 *      newly-created files are owner-only.
 *   2. `appendLogLine` also calls `fs.chmodSync(filePath, 0o600)`
 *      after the append to tighten perms on pre-existing files that
 *      may have been created with looser perms (e.g. by an older
 *      build, or by a umask of 0o000 on a misconfigured host).
 *
 * These tests exercise the real `node:fs` against a tmp dir so the
 * actual file-creation mode + chmod are observed.
 *
 * ON LINUX (sandbox): `fs.statSync(...).mode & 0o777` reflects the
 *   actual on-disk mode (the umask is applied at create time). The
 *   process-default umask in vitest is typically 0o022 — so a
 *   default-mode append (0o666 & ~0o022 = 0o644) would be observable
 *   as world-readable. The  fix's `mode: 0o600` forces 0o600
 *   regardless of the umask.
 * ON WINDOWS (not run here): POSIX mode bits don't apply — the test
 *   is POSIX-only.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../state";

// Mock electron's `app` so structuredLogger / printfLogger module
// imports don't blow up at module-load time. `appendLogLine` itself
// does not touch `app`, but the barrel re-exports `logger` which
// imports `app` at module-load.
vi.mock("electron", () => ({
	app: {
		getPath: () => "/tmp/vt-fr9-test-userdata",
		isPackaged: false,
	},
	dialog: { showErrorBox: vi.fn() },
}));

// Mock state — required by the logging barrel's transitive imports
// (printfLogger imports `appendLifecycleLine` which imports
// `lifecycleLogPath` which uses `app`). `appendLogLine` itself does
// not touch `state`, but the mock keeps the barrel import clean.
function makeMockState(): MainState {
	return {
		pythonProcess: null,
		tcpSocket: null,
		mainWindow: null,
		bubbleWindow: null,
		pendingRequests: new Map(),
		nextId: 1,
		tcpBuffer: Buffer.alloc(0),
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
	} as MainState;
}
vi.mock("../state", () => ({ state: makeMockState() }));

describe("FR-9: appendLogLine creates log files with mode 0o600", () => {
	let tmpDir: string;
	let logPath: string;
	let appendFileSyncSpy: ReturnType<typeof vi.spyOn>;
	let chmodSpy: ReturnType<typeof vi.spyOn>;
	let _resetFileSizeCacheForTest: () => void;

	beforeEach(async () => {
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-fr9-perms-"));
		logPath = path.join(tmpDir, "test.log");

		//Reset the  cache so each test starts fresh.
		vi.resetModules();
		const mod = await import("../logging");
		_resetFileSizeCacheForTest = mod._resetFileSizeCacheForTest;
		_resetFileSizeCacheForTest();

		// Spy on fs.appendFileSync + fs.chmodSync WITHOUT overriding
		// the implementation — vi.spyOn defaults to calling through,
		// so the file is actually created with the mode set by the
		//fix.
		appendFileSyncSpy = vi.spyOn(fs, "appendFileSync");
		chmodSpy = vi.spyOn(fs, "chmodSync");
	});

	afterEach(() => {
		vi.restoreAllMocks();
		try {
			fs.rmSync(tmpDir, { recursive: true, force: true });
		} catch {
			/* ignore */
		}
	});

	/**
	 * Read the low 9 bits (rwxrwxrwx) of a file's mode.
	 */
	function fileMode(filePath: string): number {
		return fs.statSync(filePath).mode & 0o777;
	}

	it("creates a new log file with mode 0o600 (owner-only)", async () => {
		const { appendLogLine } = await import("../logging");
		_resetFileSizeCacheForTest();
		appendLogLine(logPath, "first line\n", 1024 * 1024);

		expect(fs.existsSync(logPath)).toBe(true);
		//the file must be owner-only (0o600) — NOT 0o644.
		expect(fileMode(logPath)).toBe(0o600);
	});

	it("passes mode:0o600 in the fs.appendFileSync options", async () => {
		const { appendLogLine } = await import("../logging");
		_resetFileSizeCacheForTest();
		appendLogLine(logPath, "first line\n", 1024 * 1024);

		// Find the appendFileSync call against our logPath.
		const calls = appendFileSyncSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === logPath,
		);
		expect(calls.length).toBeGreaterThanOrEqual(1);
		// The third arg is the options object — must contain
		// `mode: 0o600`.
		const opts = calls[0][2] as { mode?: number; flag?: string };
		expect(opts.mode).toBe(0o600);
	});

	it("calls fs.chmodSync(filePath, 0o600) after the append to tighten pre-existing perms", async () => {
		const { appendLogLine } = await import("../logging");
		_resetFileSizeCacheForTest();
		appendLogLine(logPath, "first line\n", 1024 * 1024);

		//a chmod call must fire against the log path with 0o600.
		const chmodCalls = chmodSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === logPath,
		);
		expect(chmodCalls.length).toBeGreaterThanOrEqual(1);
		expect(chmodCalls[0][1]).toBe(0o600);
	});

	it("tightens a pre-existing 0o644 file to 0o600 on the next append", async () => {
		//Simulate a file created by an older build (pre-) with
		// looser perms.
		fs.writeFileSync(logPath, "old content\n", { mode: 0o644 });
		// Verify the pre-existing file is 0o644 (sanity check the
		// test fixture — the umask may interfere; explicitly chmod).
		fs.chmodSync(logPath, 0o644);
		expect(fileMode(logPath)).toBe(0o644);

		const { appendLogLine } = await import("../logging");
		_resetFileSizeCacheForTest();
		appendLogLine(logPath, "new line\n", 1024 * 1024);

		//after the append, the file must be tightened to 0o600.
		expect(fileMode(logPath)).toBe(0o600);
	});

	it("does NOT break when the chmod call fails (best-effort swallow)", async () => {
		// Make chmodSync throw — the helper must NOT re-throw.
		chmodSpy.mockImplementation(() => {
			const err = new Error("EACCES") as NodeJS.ErrnoException;
			err.code = "EACCES";
			throw err;
		});

		const { appendLogLine } = await import("../logging");
		_resetFileSizeCacheForTest();
		// Must not throw — chmod failure is best-effort.
		expect(() =>
			appendLogLine(logPath, "first line\n", 1024 * 1024),
		).not.toThrow();

		// The append itself must have succeeded (the file exists).
		expect(fs.existsSync(logPath)).toBe(true);
	});
});

describe("FR-9: appendLogLine options parity with appendLifecycleLine", () => {
	it("appendLogLine passes the same options shape as appendLifecycleLine", async () => {
		//appendLogLine and appendLifecycleLine must
		// pass the SAME options shape to fs.appendFileSync so tests
		// that assert on the options object (e.g.
		// electron-info-log.test.ts:127) continue to pass after
		//routes appendLifecycleLine through appendLogLine.
		// The canonical shape is `{ flag: "a", mode: 0o600 }`.
		const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-fr9-parity-"));
		const logPath = path.join(tmpDir, "parity.log");

		try {
			vi.resetModules();
			const mod = await import("../logging");
			mod._resetFileSizeCacheForTest();

			const spy = vi.spyOn(fs, "appendFileSync");
			mod.appendLogLine(logPath, "line\n", 1024 * 1024);

			const calls = spy.mock.calls.filter(
				(args: unknown[]) => args[0] === logPath,
			);
			expect(calls.length).toBeGreaterThanOrEqual(1);
			expect(calls[0]?.[2]).toEqual({ flag: "a", mode: 0o600 });

			spy.mockRestore();
		} finally {
			fs.rmSync(tmpDir, { recursive: true, force: true });
		}
	});
});
