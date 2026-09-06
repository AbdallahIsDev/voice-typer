// @vitest-environment node
/**
 * Unit tests: `appendLogLine` per-directory mkdir cache.
 *
 * Background: `appendLogLine` used to run
 * `fs.mkdirSync(dirname, { recursive: true })` on EVERY appended line —
 * one to three avoidable syscalls stacked onto the ~50-100µs append
 * path. The fix mirrors the in-file `_permsVerified` Set pattern: a
 * per-directory "directory exists" cache (`_dirVerified`) skips the
 * mkdir after the first append to that directory, and the catch site
 * invalidates the cached entry on failure so a runtime-deleted logs
 * directory recovers on the next append.
 *
 * These tests verify:
 *   1. `fs.mkdirSync` is called exactly ONCE for the first append into
 *      a (missing) directory — and the directory is really created.
 *   2. `fs.mkdirSync` is NOT called on subsequent appends to log files
 *      in the same directory (cache hit), while the lines still land.
 *   3. Different directories have independent cache entries.
 *   4. After a failed append (directory deleted at runtime), the cache
 *      entry is invalidated — the next append re-runs the mkdir and
 *      recovers.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock electron's `app` so the logging module imports don't blow up
// (config-dir resolves paths through it).
vi.mock("electron", () => ({
	app: {
		getPath: () => "/tmp/vt-dir-cache-test-userdata",
		isPackaged: false,
	},
	dialog: { showErrorBox: vi.fn() },
}));

describe("appendLogLine per-directory mkdir cache", () => {
	let tmpDir: string;
	let mkdirSpy: ReturnType<typeof vi.spyOn>;
	let _resetFileSizeCacheForTest: () => void;
	let _resetPermsVerifiedForTest: () => void;
	let _resetDirVerifiedForTest: () => void;
	let appendLogLine: (
		filePath: string,
		line: string,
		maxBytes?: number,
	) => void;

	beforeEach(async () => {
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-dircache-"));

		vi.resetModules();
		const rotationMod = await import("../rotation");
		const fileSizeMod = await import("../fileSizeCache");
		appendLogLine = rotationMod.appendLogLine;
		_resetFileSizeCacheForTest = fileSizeMod._resetFileSizeCacheForTest;
		_resetPermsVerifiedForTest = rotationMod._resetPermsVerifiedForTest;
		_resetDirVerifiedForTest = rotationMod._resetDirVerifiedForTest;
		_resetFileSizeCacheForTest();
		_resetPermsVerifiedForTest();
		_resetDirVerifiedForTest();

		// Spy keeps the real implementation (calls still execute).
		mkdirSpy = vi.spyOn(fs, "mkdirSync");
	});

	afterEach(() => {
		vi.restoreAllMocks();
		try {
			fs.rmSync(tmpDir, { recursive: true, force: true });
		} catch {
			/* ignore */
		}
	});

	it("calls fs.mkdirSync exactly ONCE for a new directory and really creates it", () => {
		const logsDir = path.join(tmpDir, "logs");
		const logPath = path.join(logsDir, "app.log");

		appendLogLine(logPath, "first line\n", 1024 * 1024);

		expect(fs.existsSync(logsDir)).toBe(true);
		const dirCalls = mkdirSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === logsDir,
		);
		expect(dirCalls.length).toBe(1);
		// recursive:true is preserved so nested dirs are created.
		expect(
			(dirCalls[0]?.[1] as { recursive?: boolean } | undefined)?.recursive,
		).toBe(true);
	});

	it("skips fs.mkdirSync on subsequent appends to the same directory (cache hit)", () => {
		const logsDir = path.join(tmpDir, "logs");
		const logPath = path.join(logsDir, "app.log");

		appendLogLine(logPath, "line 1\n", 1024 * 1024);
		appendLogLine(logPath, "line 2\n", 1024 * 1024);
		appendLogLine(logPath, "line 3\n", 1024 * 1024);

		const dirCalls = mkdirSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === logsDir,
		);
		expect(dirCalls.length).toBe(1);
		// Every line still landed — the cache must not lose writes.
		expect(fs.readFileSync(logPath, "utf-8")).toBe("line 1\nline 2\nline 3\n");
	});

	it("caches per directory — a different directory gets its own mkdir", () => {
		const dirA = path.join(tmpDir, "logs-a");
		const dirB = path.join(tmpDir, "logs-b");
		const pathA = path.join(dirA, "a.log");
		const pathB = path.join(dirB, "b.log");

		appendLogLine(pathA, "a1\n", 1024 * 1024);
		appendLogLine(pathA, "a2\n", 1024 * 1024);
		appendLogLine(pathB, "b1\n", 1024 * 1024);
		appendLogLine(pathB, "b2\n", 1024 * 1024);

		const callsA = mkdirSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === dirA,
		);
		const callsB = mkdirSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === dirB,
		);
		expect(callsA.length).toBe(1);
		expect(callsB.length).toBe(1);
	});

	it("invalidates the cache entry when an append fails, recovering on the next append", () => {
		const logsDir = path.join(tmpDir, "logs");
		const logPath = path.join(logsDir, "app.log");

		// First append: mkdir fires + entry cached.
		appendLogLine(logPath, "before delete\n", 1024 * 1024);
		expect(
			mkdirSpy.mock.calls.filter((a: unknown[]) => a[0] === logsDir).length,
		).toBe(1);

		// Simulate the logs directory disappearing at runtime.
		fs.rmSync(logsDir, { recursive: true, force: true });

		// Next append hits the cached entry, skips mkdir, and the
		// appendFileSync fails (ENOENT) — swallowed by the
		// best-effort catch, but the cache entry must be dropped.
		appendLogLine(logPath, "during outage\n", 1024 * 1024);
		expect(
			mkdirSpy.mock.calls.filter((a: unknown[]) => a[0] === logsDir).length,
		).toBe(1);

		// The following append re-runs the mkdir (cache invalidated
		// by the failure) and the write succeeds again.
		appendLogLine(logPath, "after recovery\n", 1024 * 1024);
		expect(
			mkdirSpy.mock.calls.filter((a: unknown[]) => a[0] === logsDir).length,
		).toBe(2);
		expect(fs.readFileSync(logPath, "utf-8")).toBe("after recovery\n");
	});
});
