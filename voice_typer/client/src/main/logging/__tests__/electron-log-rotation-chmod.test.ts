// @vitest-environment node
/**
 *  +  regression coverage for `rotateIfNeeded` +
 * `appendLogLine` permission handling.
 *
 * : `appendLogLine` previously called `fs.chmodSync(filePath,
 * 0o600)` on EVERY write — a syscall per log line. The fix gates the
 * chmod via a module-level `Set<string>` so it fires ONCE per session
 * per file. After rotation, the Set entry is cleared so the new active
 * file gets chmod'd on the next append.
 *
 * Single-file policy: `rotateIfNeeded` truncates the file IN PLACE
 * (empties it) when it exceeds the cap — a numbered `.1` backup is
 * never created. The per-path "perms verified" flag is reset on
 * truncation so the next append re-asserts 0o600.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock electron minimally so the logging module barrel can load.
vi.mock("electron", () => ({
	app: {
		getPath: () => "/tmp/vt-xe-20-5-test-userdata",
		isPackaged: false,
	},
}));

describe("XE-20-5: appendLogLine chmod once per session (not per-write)", () => {
	let tmpDir: string;
	let logPath: string;
	let chmodSpy: ReturnType<typeof vi.spyOn>;

	beforeEach(async () => {
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-xe-20-5-"));
		logPath = path.join(tmpDir, "test.log");

		// Reset modules so the `_chmodDone` Set is re-created
		// fresh for each test.
		vi.resetModules();
		const mod = await import("../../logging");
		mod._resetFileSizeCacheForTest();

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

	it("calls chmodSync on the first append (tighten pre-existing perms)", async () => {
		const { appendLogLine } = await import("../../logging");
		// Reset modules again to get a fresh _chmodDone Set.
		vi.resetModules();
		const mod = await import("../../logging");
		mod._resetFileSizeCacheForTest();

		appendLogLine(logPath, "first line\n", 1024 * 1024);

		const chmodCalls = chmodSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === logPath,
		);
		expect(chmodCalls.length).toBe(1);
		expect(chmodCalls[0][1]).toBe(0o600);
	});

	it("does NOT call chmodSync on the second append (Set gate)", async () => {
		vi.resetModules();
		const mod = await import("../../logging");
		mod._resetFileSizeCacheForTest();
		const { appendLogLine } = mod;

		appendLogLine(logPath, "first line\n", 1024 * 1024);
		appendLogLine(logPath, "second line\n", 1024 * 1024);

		const chmodCalls = chmodSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === logPath,
		);
		//only ONE chmod call across two appends.
		expect(chmodCalls.length).toBe(1);
	});

	it("does NOT call chmodSync on the third append either", async () => {
		vi.resetModules();
		const mod = await import("../../logging");
		mod._resetFileSizeCacheForTest();
		const { appendLogLine } = mod;

		appendLogLine(logPath, "line 1\n", 1024 * 1024);
		appendLogLine(logPath, "line 2\n", 1024 * 1024);
		appendLogLine(logPath, "line 3\n", 1024 * 1024);

		const chmodCalls = chmodSpy.mock.calls.filter(
			(args: unknown[]) => args[0] === logPath,
		);
		expect(chmodCalls.length).toBe(1);
	});

	// POSIX-only: Windows emulates POSIX mode bits as 0o666 for a
	// writable file regardless of chmod, so the on-disk mode
	// assertion can only observe 0o600 on Linux/macOS.
	it.skipIf(process.platform === "win32")(
		"tightens a pre-existing 0o644 file to 0o600 on the first append",
		async () => {
			//Simulate a file created by an older build (pre-) with
			// looser perms.
			fs.writeFileSync(logPath, "old content\n", { mode: 0o644 });
			fs.chmodSync(logPath, 0o644);
			expect(fs.statSync(logPath).mode & 0o777).toBe(0o644);

			vi.resetModules();
			const mod = await import("../../logging");
			mod._resetFileSizeCacheForTest();
			const { appendLogLine } = mod;

			appendLogLine(logPath, "new line\n", 1024 * 1024);

			expect(fs.statSync(logPath).mode & 0o777).toBe(0o600);
		},
	);
});

describe("XE-20-6: rotateIfNeeded chmods the .1 backup after rename", () => {
	let tmpDir: string;
	let logPath: string;

	beforeEach(async () => {
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-xe-20-6-"));
		logPath = path.join(tmpDir, "test.log");

		vi.resetModules();
		const mod = await import("../../logging");
		mod._resetFileSizeCacheForTest();
	});

	afterEach(() => {
		vi.restoreAllMocks();
		try {
			fs.rmSync(tmpDir, { recursive: true, force: true });
		} catch {
			/* ignore */
		}
	});

	it.skipIf(process.platform === "win32")(
		"truncates the active file in place when over the cap (POSIX)",
		async () => {
			vi.resetModules();
			const mod = await import("../../logging");
			mod._resetFileSizeCacheForTest();
			const { rotateIfNeeded } = mod;

			// Create a log file at 0o644 (simulating a pre-hardening
			// leftover) that exceeds the max-size threshold.
			const bigContent = "x".repeat(100);
			fs.writeFileSync(logPath, bigContent, { mode: 0o644 });
			fs.chmodSync(logPath, 0o644);
			expect(fs.statSync(logPath).mode & 0o777).toBe(0o644);

			// Trigger truncation with a very small max-size.
			rotateIfNeeded(logPath, 10);

			// Single-file policy: NO .1 backup is ever created. The
			// active file is truncated IN PLACE (0 bytes) and keeps its
			// single identity.
			expect(fs.existsSync(`${logPath}.1`)).toBe(false);
			expect(fs.existsSync(logPath)).toBe(true);
			expect(fs.statSync(logPath).size).toBe(0);
		},
	);

	it("does NOT throw when the truncate fails (best-effort)", async () => {
		vi.resetModules();
		const mod = await import("../../logging");
		mod._resetFileSizeCacheForTest();
		const { rotateIfNeeded } = mod;

		const bigContent = "x".repeat(100);
		fs.writeFileSync(logPath, bigContent, { mode: 0o644 });

		// Mock truncateSync to throw.
		const truncateSpy = vi.spyOn(fs, "truncateSync").mockImplementation(() => {
			const err = new Error("EACCES") as NodeJS.ErrnoException;
			err.code = "EACCES";
			throw err;
		});

		// Must not throw — truncate failure is best-effort.
		expect(() => rotateIfNeeded(logPath, 10)).not.toThrow();

		truncateSpy.mockRestore();
	});
});
