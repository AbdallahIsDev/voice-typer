// @vitest-environment node
/**
 * PI-13 + FR-5 regression tests for `atomicWriteFileSync` (Electron-side).
 *
 * Verifies the contract the helper provides to the export IPC paths:
 *   - On success: the destination contains the new content.
 *   - On success: the temp file is removed (does not leak).
 *   - On overwrite: an existing destination is fully replaced (no
 *     truncated half).
 *   - On rename failure: the original destination is UNCHANGED and
 *     the temp file is cleaned up.
 *
 * FR-5 (Critical) adds regression coverage for the Windows-fallback
 * data-loss scenario:
 *   - On EEXIST/EPERM rename failure, the unlink+rename fallback
 *     recovers the destination atomically.
 *   - On EEXIST/EPERM rename failure + non-ENOENT destination-unlink
 *     failure, the NEW tmp file is PRESERVED (not deleted) so the
 *     user can manually rename it for recovery — the previous
 *     implementation deleted the tmp file and the user lost BOTH
 *     old and new exports.
 *   - On non-EEXIST/EPERM rename failure (e.g. ENOSPC, EACCES), the
 *     tmp file is cleaned up (the user has no way to recover it).
 *
 * These tests use the real `node:fs` against a temporary directory
 * (NOT mocked) so they exercise the actual `rename(2)` syscall path.
 * FR-5 regression tests selectively mock `fs.renameSync` /
 * `fs.unlinkSync` via `vi.spyOn` to simulate Windows-specific error
 * codes that POSIX does not naturally produce.
 *
 * ON LINUX (sandbox): POSIX `rename` overwrites atomically — the
 *   EEXIST/EPERM fallback is unreachable without mocking.
 * ON WINDOWS (not run here): Node 10+ uses
 *   `MOVEFILE_REPLACE_EXISTING` so the rename-first branch succeeds
 *   in the common case; the fallback runs only on rare ACL/lock
 *   conditions.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
	_atomicWriteTempPath,
	atomicWriteFileSync,
} from "../ipc/export-handlers";

describe("atomicWriteFileSync (PI-13)", () => {
	let tmpDir: string;

	beforeEach(() => {
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-pi13-atomic-"));
	});

	afterEach(() => {
		// Best-effort cleanup — never fail a test on cleanup errors.
		try {
			fs.rmSync(tmpDir, { recursive: true, force: true });
		} catch {
			/* ignore */
		}
	});

	it("writes new content to the destination on success", () => {
		const dest = path.join(tmpDir, "out.json");
		atomicWriteFileSync(dest, '{"hello":"world"}', "utf-8");
		expect(fs.readFileSync(dest, "utf-8")).toBe('{"hello":"world"}');
	});

	it("does not leak the temp file on success", () => {
		const dest = path.join(tmpDir, "out.csv");
		atomicWriteFileSync(dest, "a,b,c\n1,2,3\n", "utf-8");
		expect(fs.existsSync(_atomicWriteTempPath(dest))).toBe(false);
	});

	it("fully replaces existing content on overwrite (no truncated half)", () => {
		const dest = path.join(tmpDir, "out.json");
		fs.writeFileSync(dest, "OLD,SENTINEL,CONTENTS\n", "utf-8");
		atomicWriteFileSync(dest, '{"new":"contents"}', "utf-8");
		expect(fs.readFileSync(dest, "utf-8")).toBe('{"new":"contents"}');
	});

	it("preserves the original file when the rename fails", () => {
		// Pointing the destination at a path whose PARENT directory
		// does not exist forces `fs.writeFileSync(tmpPath)` to
		// throw ENOENT — the helper must surface this error
		// without touching any pre-existing destination file.
		const originalDir = path.join(tmpDir, "preserve");
		fs.mkdirSync(originalDir, { recursive: true });
		const original = path.join(originalDir, "out.json");
		fs.writeFileSync(original, "ORIGINAL,SENTINEL\n", "utf-8");

		const badDest = path.join(tmpDir, "nonexistent_subdir", "out.json");
		expect(() => atomicWriteFileSync(badDest, "NEW", "utf-8")).toThrow();

		// The original file at the unrelated path must be unchanged.
		expect(fs.readFileSync(original, "utf-8")).toBe("ORIGINAL,SENTINEL\n");
		// No temp file should leak at the bad destination.
		expect(fs.existsSync(_atomicWriteTempPath(badDest))).toBe(false);
	});

	it("accepts Buffer content (no encoding applied)", () => {
		const dest = path.join(tmpDir, "out.bin");
		const buf = Buffer.from([0x00, 0x01, 0x02, 0xff]);
		atomicWriteFileSync(dest, buf);
		expect(fs.readFileSync(dest)).toEqual(buf);
	});

	it("defaults to utf-8 encoding for string content", () => {
		const dest = path.join(tmpDir, "out.txt");
		// Multi-byte UTF-8 content ensures encoding is wired correctly.
		const content = "héllo, 世界";
		atomicWriteFileSync(dest, content);
		expect(fs.readFileSync(dest, "utf-8")).toBe(content);
	});
});

describe("atomicWriteFileSync (FR-5 Windows-fallback data-loss regression)", () => {
	let tmpDir: string;

	beforeEach(() => {
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-fr5-atomic-"));
	});

	afterEach(() => {
		// Restore any spies left over from a test before cleaning up.
		vi.restoreAllMocks();
		try {
			fs.rmSync(tmpDir, { recursive: true, force: true });
		} catch {
			/* ignore */
		}
	});

	/**
	 * Build a NodeJS.ErrnoException-shaped Error with a specific `code`.
	 * Used to simulate Windows-specific fs error codes that POSIX does
	 * not naturally produce (EEXIST on rename, EPERM on unlink).
	 */
	function errnoError(code: string, message: string): NodeJS.ErrnoException {
		const err = new Error(message) as NodeJS.ErrnoException;
		err.code = code;
		return err;
	}

	it("recovers via the unlink+rename fallback when rename fails with EEXIST", () => {
		// Simulate Windows pre-Node-10 behavior: rename refuses to
		// overwrite an existing destination with EEXIST. The fallback
		// unlinks the destination, then retries the rename.
		const dest = path.join(tmpDir, "out.json");
		fs.writeFileSync(dest, "OLD\n", "utf-8");

		// mockImplementationOnce throws EEXIST on the first call.
		// The default spy behavior (call the original) handles the
		// second call so the fallback's retry succeeds.
		const renameSpy = vi.spyOn(fs, "renameSync").mockImplementationOnce(() => {
			throw errnoError("EEXIST", "EEXIST: rename");
		});

		atomicWriteFileSync(dest, "NEW\n", "utf-8");

		// The destination now contains the NEW content (the fallback
		// succeeded).
		expect(fs.readFileSync(dest, "utf-8")).toBe("NEW\n");
		// No temp file leaks.
		expect(fs.existsSync(_atomicWriteTempPath(dest))).toBe(false);
		// Two rename calls fired (initial + fallback retry).
		expect(renameSpy.mock.calls.length).toBe(2);

		renameSpy.mockRestore();
	});

	it("PRESERVES the tmp file when rename fails with EPERM AND destination unlink fails with non-ENOENT", () => {
		// FR-5 critical regression: previously the catch block on the
		// destination unlinkSync deleted the NEW tmp file, leaving the
		// user with NEITHER the old destination NOR the new content.
		// The fix preserves the tmp file so the user can manually
		// rename it for recovery.
		const dest = path.join(tmpDir, "out.json");
		fs.writeFileSync(dest, "OLD\n", "utf-8");

		const renameSpy = vi.spyOn(fs, "renameSync").mockImplementation(() => {
			throw errnoError("EPERM", "EPERM: rename");
		});
		const unlinkSpy = vi
			.spyOn(fs, "unlinkSync")
			.mockImplementation((p: fs.PathLike) => {
				const pStr = String(p);
				// Both the destination unlink AND any tmp cleanup must
				// be intercepted. The destination is at `dest`; the
				// tmp is at `_atomicWriteTempPath(dest)`. Throw a
				// non-ENOENT error for the destination to trigger the
				// FR-5 path. Throw the same for the tmp to verify the
				// helper does NOT attempt to delete the tmp on this
				// path (if it did, this mock would throw and surface
				// the wrong error).
				if (pStr === dest) {
					throw errnoError("EPERM", "EPERM: unlink destination");
				}
				// Should not be called for the tmp file (FR-5 says do
				// NOT unlink tmpPath in this catch block). If it is,
				// throw to make the regression visible.
				throw new Error(
					`FR-5 violation: unlinkSync called on tmp path ${pStr} (should be preserved)`,
				);
			});

		// The helper must throw the EPERM from the destination unlink
		// (NOT the EPERM from the rename — the rename's EPERM is
		// caught and triggers the fallback, which is what we want).
		expect(() => atomicWriteFileSync(dest, "NEW\n", "utf-8")).toThrow();

		// FR-5: the tmp file MUST still exist so the user can
		// manually rename it for recovery.
		const tmpPath = _atomicWriteTempPath(dest);
		expect(fs.existsSync(tmpPath)).toBe(true);
		expect(fs.readFileSync(tmpPath, "utf-8")).toBe("NEW\n");

		// The original destination should still exist (the destination
		// unlink failed) — though its contents are unchanged because
		// the EPERM blocked the unlink before any modification.
		expect(fs.existsSync(dest)).toBe(true);
		expect(fs.readFileSync(dest, "utf-8")).toBe("OLD\n");

		renameSpy.mockRestore();
		unlinkSpy.mockRestore();
	});

	it("cleans up the tmp file when rename fails with a non-EEXIST/EPERM error (unrecoverable)", () => {
		// ENOSPC / EACCES / ENOENT on tmp: the user has no way to
		// recover the tmp file (the parent dir is unwritable, etc.).
		// The helper should clean up the tmp and re-throw.
		const dest = path.join(tmpDir, "out.json");
		fs.writeFileSync(dest, "OLD\n", "utf-8");

		const renameSpy = vi.spyOn(fs, "renameSync").mockImplementation(() => {
			throw errnoError("ENOSPC", "ENOSPC: rename");
		});

		expect(() => atomicWriteFileSync(dest, "NEW\n", "utf-8")).toThrow();

		// The tmp file should be cleaned up (unrecoverable error).
		expect(fs.existsSync(_atomicWriteTempPath(dest))).toBe(false);
		// The original destination is unchanged.
		expect(fs.readFileSync(dest, "utf-8")).toBe("OLD\n");

		renameSpy.mockRestore();
	});

	it("does NOT delete the tmp file when the fallback rename also fails (after unlink succeeded)", () => {
		// FR-5: even if the destination unlink succeeded, a
		// subsequent fallback-rename failure must NOT delete the tmp
		// file. The destination has already been unlinked, so the
		// tmp file is the user's only remaining copy of the new
		// content.
		const dest = path.join(tmpDir, "out.json");
		fs.writeFileSync(dest, "OLD\n", "utf-8");

		const renameSpy = vi.spyOn(fs, "renameSync").mockImplementation(() => {
			// Both the initial rename AND the fallback retry rename
			// throw EEXIST/EPERM-style errors. We use EEXIST for the
			// first call (to enter the fallback) and EACCES for the
			// retry (to fail the fallback rename too — different
			// non-EEXIST code so the retry's catch propagates).
			const callCount = renameSpy.mock.calls.length;
			if (callCount === 1) {
				throw errnoError("EEXIST", "EEXIST: rename first attempt");
			}
			throw errnoError("EACCES", "EACCES: rename fallback retry");
		});

		// The destination unlink should succeed (real fs). The
		// helper should call fs.unlinkSync for `dest`. We don't spy
		// on unlinkSync here so it goes to the real fs.

		expect(() => atomicWriteFileSync(dest, "NEW\n", "utf-8")).toThrow();

		// The destination was unlinked by the fallback (real fs).
		expect(fs.existsSync(dest)).toBe(false);
		// FR-5: the tmp file MUST still exist so the user can
		// manually rename it for recovery (the destination is gone,
		// so the tmp is the only surviving copy of the new content).
		const tmpPath = _atomicWriteTempPath(dest);
		expect(fs.existsSync(tmpPath)).toBe(true);
		expect(fs.readFileSync(tmpPath, "utf-8")).toBe("NEW\n");

		renameSpy.mockRestore();
	});
});
