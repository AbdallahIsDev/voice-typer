// @vitest-environment node
/**
 * Tests for `sweepStaleLogsIn()` — Tiers 1 (age) + 2 (size fallback) of
 * the three-tier log-cleanup design.
 *
 * Pins:
 * 1. A file older than LOG_AGE_RETENTION_MS is deleted (Tier 1).
 * 2. A file larger than LOG_SIZE_FALLBACK_BYTES is deleted even when
 *    freshly written (Tier 2).
 * 3. Recent + small files survive.
 * 4. `*.lock` files are NEVER deleted.
 * 5. A missing logs dir is a silent no-op.
 *
 * Temp dirs live under `os.tmpdir()` — NEVER inside the source tree
 * (an earlier draft created them next to this test file and its
 * `rmSync` cleanup deleted the directory it lived in).
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
	LOG_AGE_RETENTION_MS,
	LOG_SIZE_FALLBACK_BYTES,
} from "../logging/constants";
import { sweepStaleLogsIn } from "../logging/rotation";

let logsDir = "";

function backdateMtime(filePath: string, msAgo: number): void {
	const target = new Date(Date.now() - msAgo);
	fs.utimesSync(filePath, target, target);
}

describe("sweepStaleLogsIn — three-tier cleanup Tiers 1 + 2", () => {
	beforeEach(() => {
		logsDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-sweep-test-"));
	});

	afterEach(() => {
		fs.rmSync(logsDir, { recursive: true, force: true });
	});

	it("deletes a file older than the age retention (Tier 1)", () => {
		const oldFile = path.join(logsDir, "electron-main.log");
		fs.writeFileSync(oldFile, "ancient", "utf-8");
		backdateMtime(oldFile, LOG_AGE_RETENTION_MS + 60_000);

		sweepStaleLogsIn(logsDir);

		expect(fs.existsSync(oldFile)).toBe(false);
	});

	it("keeps a recent file under the size fallback", () => {
		const recent = path.join(logsDir, "electron-main.log");
		fs.writeFileSync(recent, "current session", "utf-8");

		sweepStaleLogsIn(logsDir);

		expect(fs.existsSync(recent)).toBe(true);
		expect(fs.readFileSync(recent, "utf-8")).toBe("current session");
	});

	it("deletes an oversized fresh file via the size fallback (Tier 2)", () => {
		const oversized = path.join(logsDir, "electron-runtime.log");
		fs.writeFileSync(
			oversized,
			Buffer.alloc(LOG_SIZE_FALLBACK_BYTES + 1, 0x78),
		);
		// mtime is NOW — only size triggers the delete.

		sweepStaleLogsIn(logsDir);

		expect(fs.existsSync(oversized)).toBe(false);
	});

	it("never deletes lock files", () => {
		const lock = path.join(logsDir, "electron-main.log.lock");
		fs.writeFileSync(lock, "", "utf-8");
		backdateMtime(lock, LOG_AGE_RETENTION_MS * 10);

		sweepStaleLogsIn(logsDir);

		expect(fs.existsSync(lock)).toBe(true);
	});

	it("is a no-op when the logs dir does not exist", () => {
		const missing = path.join(logsDir, "does-not-exist");
		// Must not throw.
		expect(() => sweepStaleLogsIn(missing)).not.toThrow();
	});
});
