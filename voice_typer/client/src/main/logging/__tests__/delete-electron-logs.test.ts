// @vitest-environment node
/**
 *  regression coverage: `deleteElectronPersonalDataLogs` (GDPR
 * Art. 17 helper) must include `electron-runtime.log` and
 * `electron-lifecycle.log` (+ their `.1` backups) in the erasure scope.
 *
 * Pre-fix the helper only unlinked `electron-main.log` and
 * `electron-renderer-errors.log` — `electron-runtime.log` (written by
 * `printfLogger.ts`'s `mainRuntimeLogger` for every WARN/ERROR) and
 * `electron-lifecycle.log` (written by `appendLifecycleLine` for opt-in
 * INFO persistence) were omitted, leaving up to 12 MiB of potentially
 * PII-bearing log data on disk after a "delete all personal data"
 * request.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The temp userData dir — replaced on each test run so the test starts
// with a clean slate. The mock returns this path so
// `deleteElectronPersonalDataLogs` finds the files we create.
let tmpDir: string;

vi.mock("electron", () => ({
	app: {
		getPath: () => tmpDir,
		isPackaged: false,
	},
}));

describe("XE-20-3: deleteElectronPersonalDataLogs erases all 4 log files", () => {
	beforeEach(() => {
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-xe-20-3-"));
	});

	afterEach(() => {
		try {
			fs.rmSync(tmpDir, { recursive: true, force: true });
		} catch {
			/* ignore */
		}
		vi.resetModules();
	});

	it("unlinks electron-main.log + .1 backup", async () => {
		const { deleteElectronPersonalDataLogs } = await import(
			"../structuredLogger"
		);
		fs.writeFileSync(path.join(tmpDir, "electron-main.log"), "main");
		fs.writeFileSync(path.join(tmpDir, "electron-main.log.1"), "main.1");

		const result = deleteElectronPersonalDataLogs();
		expect(result.erased).toEqual(
			expect.arrayContaining([
				path.join(tmpDir, "electron-main.log"),
				path.join(tmpDir, "electron-main.log.1"),
			]),
		);
		expect(fs.existsSync(path.join(tmpDir, "electron-main.log"))).toBe(false);
		expect(fs.existsSync(path.join(tmpDir, "electron-main.log.1"))).toBe(false);
	});

	it("unlinks electron-renderer-errors.log + .1 backup", async () => {
		const { deleteElectronPersonalDataLogs } = await import(
			"../structuredLogger"
		);
		fs.writeFileSync(
			path.join(tmpDir, "electron-renderer-errors.log"),
			"renderer",
		);
		fs.writeFileSync(
			path.join(tmpDir, "electron-renderer-errors.log.1"),
			"renderer.1",
		);

		const result = deleteElectronPersonalDataLogs();
		expect(result.erased).toEqual(
			expect.arrayContaining([
				path.join(tmpDir, "electron-renderer-errors.log"),
				path.join(tmpDir, "electron-renderer-errors.log.1"),
			]),
		);
		expect(
			fs.existsSync(path.join(tmpDir, "electron-renderer-errors.log")),
		).toBe(false);
		expect(
			fs.existsSync(path.join(tmpDir, "electron-renderer-errors.log.1")),
		).toBe(false);
	});

	it("unlinks electron-runtime.log + .1 backup (XE-20-3)", async () => {
		const { deleteElectronPersonalDataLogs } = await import(
			"../structuredLogger"
		);
		fs.writeFileSync(path.join(tmpDir, "electron-runtime.log"), "runtime");
		fs.writeFileSync(path.join(tmpDir, "electron-runtime.log.1"), "runtime.1");

		const result = deleteElectronPersonalDataLogs();
		expect(result.erased).toEqual(
			expect.arrayContaining([
				path.join(tmpDir, "electron-runtime.log"),
				path.join(tmpDir, "electron-runtime.log.1"),
			]),
		);
		expect(fs.existsSync(path.join(tmpDir, "electron-runtime.log"))).toBe(
			false,
		);
		expect(fs.existsSync(path.join(tmpDir, "electron-runtime.log.1"))).toBe(
			false,
		);
	});

	it("unlinks electron-lifecycle.log + .1 backup (XE-20-3)", async () => {
		const { deleteElectronPersonalDataLogs } = await import(
			"../structuredLogger"
		);
		fs.writeFileSync(path.join(tmpDir, "electron-lifecycle.log"), "lifecycle");
		fs.writeFileSync(
			path.join(tmpDir, "electron-lifecycle.log.1"),
			"lifecycle.1",
		);

		const result = deleteElectronPersonalDataLogs();
		expect(result.erased).toEqual(
			expect.arrayContaining([
				path.join(tmpDir, "electron-lifecycle.log"),
				path.join(tmpDir, "electron-lifecycle.log.1"),
			]),
		);
		expect(fs.existsSync(path.join(tmpDir, "electron-lifecycle.log"))).toBe(
			false,
		);
		expect(fs.existsSync(path.join(tmpDir, "electron-lifecycle.log.1"))).toBe(
			false,
		);
	});

	it("unlinks ALL 4 log files + backups in one call", async () => {
		const { deleteElectronPersonalDataLogs } = await import(
			"../structuredLogger"
		);
		// Create all 4 active logs + .1 backups = 8 files total.
		const names = [
			"electron-main.log",
			"electron-renderer-errors.log",
			"electron-runtime.log",
			"electron-lifecycle.log",
		];
		for (const name of names) {
			fs.writeFileSync(path.join(tmpDir, name), name);
			fs.writeFileSync(path.join(tmpDir, `${name}.1`), `${name}.1`);
		}

		const result = deleteElectronPersonalDataLogs();

		// All 8 files must be unlinked.
		expect(result.erased).toHaveLength(8);
		for (const name of names) {
			expect(fs.existsSync(path.join(tmpDir, name))).toBe(false);
			expect(fs.existsSync(path.join(tmpDir, `${name}.1`))).toBe(false);
		}
		expect(Object.keys(result.failed)).toHaveLength(0);
	});
});
