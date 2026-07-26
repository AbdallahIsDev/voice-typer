// @vitest-environment node
/**
 * PI-13 regression tests for `atomicWriteFileSync` (Electron-side).
 *
 * Verifies the contract the helper provides to the export IPC paths:
 *   - On success: the destination contains the new content.
 *   - On success: the temp file is removed (does not leak).
 *   - On overwrite: an existing destination is fully replaced (no
 *     truncated half).
 *   - On rename failure: the original destination is UNCHANGED and
 *     the temp file is cleaned up.
 *
 * These tests use the real `node:fs` against a temporary directory
 * (NOT mocked) so they exercise the actual `rename(2)` syscall path.
 *
 * ON LINUX (sandbox): POSIX `rename` overwrites atomically.
 * ON WINDOWS (not run here): the helper unlinks the destination first
 *   so the rename succeeds — the unlink-then-rename window is racy
 *   but bounded by the same-process caller.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

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
