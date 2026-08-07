// @vitest-environment node
/**
 *  regression tests: `appendLogLine` per-path "perms verified" cache.
 *
 * Background
 * ----------
 * Pre-: `appendLogLine` called `fs.chmodSync(filePath, 0o600)` on
 * EVERY append — even though the chmod is idempotent (the file is
 * already 0o600 after the first call). This was 30 sync chmods/sec
 * when `VOICE_TYPER_ELECTRON_INFO_LOG=1` (30 Hz bubble_level lifecycle
 * logging).
 *
 * Post-: a per-path "perms verified" flag (`_permsVerified` Set
 * in rotation.ts) skips the chmod after the first successful call.
 * The flag is reset on truncation (single-file policy truncates
 * the file in place; the next append re-asserts 0o600).
 *
 * These tests verify:
 *   1. `fs.chmodSync` is called exactly ONCE for the first append to
 *      a new file.
 *   2. `fs.chmodSync` is NOT called on subsequent appends to the same
 *      file (the flag is set after the first call).
 *   3. After rotation, the flag is reset — the next append re-chmods.
 *   4. Different file paths have independent flags.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../../state";

// Mock electron's `app` so the logging barrel imports don't blow up.
vi.mock("electron", () => ({
	app: {
		getPath: () => "/tmp/vt-ab40-test-userdata",
		isPackaged: false,
	},
	dialog: { showErrorBox: vi.fn() },
}));

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
vi.mock("../../state", () => ({ state: makeMockState() }));

describe("AB-40: appendLogLine per-path perms cache", () => {
	let tmpDir: string;
	let logPath: string;
	let chmodSpy: ReturnType<typeof vi.spyOn>;
	let _resetFileSizeCacheForTest: () => void;
	let _resetPermsVerifiedForTest: () => void;

	beforeEach(async () => {
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-ab40-perms-"));
		logPath = path.join(tmpDir, "test.log");

		vi.resetModules();
		//import directly from the rotation module (not the
		// barrel) so we can access the internal `_resetPermsVerifiedForTest`
		// which is NOT re-exported from the public barrel.
		const rotationMod = await import("../rotation");
		const fileSizeMod = await import("../fileSizeCache");
		_resetFileSizeCacheForTest = fileSizeMod._resetFileSizeCacheForTest;
		_resetPermsVerifiedForTest = rotationMod._resetPermsVerifiedForTest;
		_resetFileSizeCacheForTest();
		_resetPermsVerifiedForTest();

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

	it("calls fs.chmodSync exactly ONCE on the first append to a new file", async () => {
		const { appendLogLine } = await import("../rotation");
		_resetFileSizeCacheForTest();
		_resetPermsVerifiedForTest();

		appendLogLine(logPath, "first line\n", 1024 * 1024);

		const chmodCalls = chmodSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === logPath,
		);
		expect(chmodCalls.length).toBe(1);
		expect(chmodCalls[0][1]).toBe(0o600);
	});

	it("does NOT call fs.chmodSync on subsequent appends to the same file (AB-40 perms cache)", async () => {
		const { appendLogLine } = await import("../rotation");
		_resetFileSizeCacheForTest();
		_resetPermsVerifiedForTest();

		// Append 5 lines to the same file.
		appendLogLine(logPath, "line 1\n", 1024 * 1024);
		appendLogLine(logPath, "line 2\n", 1024 * 1024);
		appendLogLine(logPath, "line 3\n", 1024 * 1024);
		appendLogLine(logPath, "line 4\n", 1024 * 1024);
		appendLogLine(logPath, "line 5\n", 1024 * 1024);

		const chmodCalls = chmodSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === logPath,
		);
		//chmod is called ONLY ONCE (on the first append). The
		// subsequent 4 appends skip chmod because the flag is set.
		expect(chmodCalls.length).toBe(1);
	});

	it("resets the perms cache on rotation — next append re-chmods", async () => {
		const { appendLogLine } = await import("../rotation");
		_resetFileSizeCacheForTest();
		_resetPermsVerifiedForTest();

		// First append — chmod fires, flag is set. Use a tiny cap (10
		// bytes) so the next append triggers rotation.
		appendLogLine(logPath, "first line\n", 10);
		expect(
			chmodSpy.mock.calls.filter((a: unknown[]) => a[0] === logPath).length,
		).toBe(1);

		// Wait for the deferred rotation to fire (setImmediate).
		await new Promise<void>((resolve) => setImmediate(resolve));
		await new Promise<void>((resolve) => setImmediate(resolve));

		// Second append — the truncation emptied the file in place and
		// reset the perms flag. chmod fires again (flag was reset by
		// the truncation).
		appendLogLine(logPath, "second line\n", 1024 * 1024);

		const chmodCalls = chmodSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === logPath,
		);
		//at least 2 chmod calls total (one before rotation, one
		// after). The exact count depends on timing of the deferred
		// rotation, but >= 2 proves the flag was reset.
		expect(chmodCalls.length).toBeGreaterThanOrEqual(2);
	});

	it("does NOT call chmodSync on a different file path (per-path cache)", async () => {
		const { appendLogLine } = await import("../rotation");
		_resetFileSizeCacheForTest();
		_resetPermsVerifiedForTest();

		const logPath2 = path.join(tmpDir, "other.log");

		appendLogLine(logPath, "line for file 1\n", 1024 * 1024);
		appendLogLine(logPath2, "line for file 2\n", 1024 * 1024);
		appendLogLine(logPath, "another line for file 1\n", 1024 * 1024);
		appendLogLine(logPath2, "another line for file 2\n", 1024 * 1024);

		const chmodCalls1 = chmodSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === logPath,
		);
		const chmodCalls2 = chmodSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === logPath2,
		);
		//each path gets exactly ONE chmod (independent flags).
		expect(chmodCalls1.length).toBe(1);
		expect(chmodCalls2.length).toBe(1);
	});
});
