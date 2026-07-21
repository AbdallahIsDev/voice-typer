/**
 * @vitest-environment node
 *
 * CR-9 (IMPL-7) — Electron crash log rotation + REVIEW-12/REVIEW-9
 * circuit-breaker regression coverage.
 *
 * These tests run in a `node` environment (no jsdom) because they
 * exercise real `fs` I/O against a per-test temp directory. The
 * `electron`, `./single_instance`, and `./state` modules are mocked
 * out so importing `bootstrap.ts` does not pull in the real Electron
 * runtime (which is not available under vitest).
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vi.mock calls are hoisted by vitest to before all imports, so they
// intercept the imports done by `bootstrap.ts` even though the import
// statement appears later in the file.

// Mock `electron` — bootstrap.ts only uses app.getPath / dialog.showErrorBox
// / session.defaultSession.webRequest.onHeadersReceived. We provide all
// three so importing the module does not throw at top level.
vi.mock("electron", () => ({
	app: {
		getPath: () => "/tmp/vt-mock-userdata",
		isPackaged: true,
	},
	dialog: {
		showErrorBox: vi.fn(),
	},
	session: {
		defaultSession: {
			webRequest: {
				onHeadersReceived: vi.fn(),
			},
		},
	},
}));

// Mock `./single_instance` — its real implementation transitively imports
// `./windows`, which pulls in heavy Electron BrowserWindow machinery we
// do not need here.
vi.mock("../single_instance", () => ({
	computeConfigDir: () => "/tmp/vt-mock-userdata",
}));

// Mock `./state` — bootstrap.ts only reads `state.sessionNonce` and never
// observes its initial value; an empty object is sufficient.
vi.mock("../state", () => ({
	state: { sessionNonce: "" },
}));

// Import the units under test AFTER the mocks are registered.
// `_installErrorHandlers` is the factory; `setupErrorHandlers` is the
// production wrapper that we do not call here (it would call
// `app.getPath` which is mocked, but we want explicit control over the
// tmp dir).
import { _crashLogPaths, _installErrorHandlers } from "../bootstrap";
import { DEFAULT_CRASH_LOG_MAX_BYTES, rotateIfNeeded } from "../logging";

/**
 * Recursively remove a directory, ignoring "ENOENT" (already gone).
 */
function rmrf(target: string): void {
	try {
		fs.rmSync(target, { recursive: true, force: true });
	} catch (e) {
		const code = (e as NodeJS.ErrnoException).code;
		if (code !== "ENOENT") throw e;
	}
}

/**
 * Helper: emit N `uncaughtException` events on the real `process` object.
 *
 * `process.emit` is synchronous — when there is at least one
 * `uncaughtException` listener attached, Node does not crash the process
 * and just returns `true`. This lets the test drive the handler
 * deterministically.
 */
function emitUncaught(n: number, prefix = "boom"): void {
	for (let i = 0; i < n; i++) {
		process.emit("uncaughtException", new Error(`${prefix} ${i}`));
	}
}

/**
 * Helper: emit N `unhandledRejection` events on the real `process` object.
 */
function emitRejection(n: number, prefix = "reject"): void {
	for (let i = 0; i < n; i++) {
		process.emit("unhandledRejection", new Error(`${prefix} ${i}`));
	}
}

describe("_crashLogPaths", () => {
	it("returns separate crash and rejection log paths under the userData dir", () => {
		const dir = "/tmp/vt-test-xyz";
		const { crashLogPath, rejectionLogPath } = _crashLogPaths(dir);
		expect(crashLogPath).toBe(path.join(dir, "electron-crashes.log"));
		expect(rejectionLogPath).toBe(path.join(dir, "electron-rejections.log"));
		expect(crashLogPath).not.toBe(rejectionLogPath);
	});
});

describe("rotateIfNeeded (CR-9)", () => {
	let tmpDir: string;

	beforeEach(() => {
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-rotate-test-"));
	});

	afterEach(() => {
		rmrf(tmpDir);
	});

	it("is a no-op when the file does not exist yet (first crash)", () => {
		const target = path.join(tmpDir, "missing.log");
		expect(() => rotateIfNeeded(target)).not.toThrow();
		expect(fs.existsSync(target)).toBe(false);
		expect(fs.existsSync(`${target}.1`)).toBe(false);
	});

	it("is a no-op when the file is smaller than the cap", () => {
		const target = path.join(tmpDir, "small.log");
		fs.writeFileSync(target, "x".repeat(100));
		rotateIfNeeded(target, 1_000);
		expect(fs.existsSync(target)).toBe(true);
		expect(fs.existsSync(`${target}.1`)).toBe(false);
	});

	it("renames the file to .1 when it exceeds the cap", () => {
		const target = path.join(tmpDir, "big.log");
		fs.writeFileSync(target, "x".repeat(2_000));
		rotateIfNeeded(target, 1_000);
		// Source is gone (renamed away).
		expect(fs.existsSync(target)).toBe(false);
		// .1 contains the rotated content.
		expect(fs.existsSync(`${target}.1`)).toBe(true);
		expect(fs.readFileSync(`${target}.1`, "utf-8").length).toBe(2_000);
	});

	it("overwrites a prior .1 file (single-generation rotation)", () => {
		const target = path.join(tmpDir, "rollover.log");
		// Seed an old .1 backup.
		fs.writeFileSync(`${target}.1`, "OLD_BACKUP_CONTENT");
		// Seed the active file with new oversized content.
		fs.writeFileSync(target, "x".repeat(2_000));
		rotateIfNeeded(target, 1_000);
		// .1 was overwritten with the new content.
		expect(fs.readFileSync(`${target}.1`, "utf-8")).toBe("x".repeat(2_000));
		expect(fs.existsSync(target)).toBe(false);
	});

	it("uses DEFAULT_CRASH_LOG_MAX_BYTES (1 MiB) when maxSize is omitted", () => {
		expect(DEFAULT_CRASH_LOG_MAX_BYTES).toBe(1_048_576);
		const target = path.join(tmpDir, "default-cap.log");
		// Exactly at the cap → no rotation (size <= maxSize).
		fs.writeFileSync(target, "x".repeat(DEFAULT_CRASH_LOG_MAX_BYTES));
		rotateIfNeeded(target);
		expect(fs.existsSync(target)).toBe(true);
		expect(fs.existsSync(`${target}.1`)).toBe(false);
		// One byte over the cap → rotate.
		fs.appendFileSync(target, "y");
		rotateIfNeeded(target);
		expect(fs.existsSync(target)).toBe(false);
		expect(fs.existsSync(`${target}.1`)).toBe(true);
	});
});

describe("_installErrorHandlers — CR-9 rotation + circuit breaker", () => {
	let tmpDir: string;
	let exitCalls: number[];

	beforeEach(() => {
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-bootstrap-test-"));
		exitCalls = [];
	});

	afterEach(() => {
		rmrf(tmpDir);
	});

	it("rotates the crash log after 6 uncaughtException events", () => {
		const { crashLogPath } = _crashLogPaths(tmpDir);
		// Pre-seed the active crash log with 1.1 MiB of content so
		// the very first emit triggers a rotation.
		const preseed = "PRESEED_" + "x".repeat(DEFAULT_CRASH_LOG_MAX_BYTES + 1024);
		fs.writeFileSync(crashLogPath, preseed);

		const handlers = _installErrorHandlers({
			userDataDir: tmpDir,
			exit: (code) => exitCalls.push(code),
		});

		try {
			emitUncaught(6);
		} finally {
			handlers.dispose();
		}

		// CR-9: .1 backup exists and holds the pre-seeded content
		// (renamed away when the active file exceeded 1 MiB).
		const backupPath = `${crashLogPath}.1`;
		expect(fs.existsSync(backupPath)).toBe(true);
		expect(fs.readFileSync(backupPath, "utf-8")).toBe(preseed);

		// The active file now holds the 6 new crash lines (each
		// ~hundreds of bytes, well under the 1 MiB cap, so no
		// further rotation).
		expect(fs.existsSync(crashLogPath)).toBe(true);
		const active = fs.readFileSync(crashLogPath, "utf-8");
		expect(active).toContain("[uncaughtException]");
		expect(active).toContain("boom 0");
		expect(active).toContain("boom 5");

		// Circuit breaker: MAX_UNCAUGHT=5, so exit(1) was called
		// on the 5th error AND on the 6th (count is still >= 5).
		expect(exitCalls.length).toBeGreaterThanOrEqual(1);
		expect(exitCalls.every((c) => c === 1)).toBe(true);

		// CR-9 separation: the rejection log must NOT have been
		// touched by uncaughtException events.
		const { rejectionLogPath } = _crashLogPaths(tmpDir);
		expect(fs.existsSync(rejectionLogPath)).toBe(false);
	});

	it("writes unhandledRejection events to the separate rejection log", () => {
		const { crashLogPath, rejectionLogPath } = _crashLogPaths(tmpDir);

		const handlers = _installErrorHandlers({
			userDataDir: tmpDir,
			exit: (code) => exitCalls.push(code),
		});

		try {
			emitRejection(2);
		} finally {
			handlers.dispose();
		}

		// REVIEW-12 alignment: rejections go to the rejection log.
		expect(fs.existsSync(rejectionLogPath)).toBe(true);
		const rej = fs.readFileSync(rejectionLogPath, "utf-8");
		expect(rej).toContain("[unhandledRejection]");
		expect(rej).toContain("reject 0");
		expect(rej).toContain("reject 1");

		// The crash log must NOT have been touched.
		expect(fs.existsSync(crashLogPath)).toBe(false);

		// Only 2 rejections — breaker has not tripped yet.
		expect(exitCalls.length).toBe(0);
	});

	it("trips the 5-error circuit breaker across mixed event types (REVIEW-12)", () => {
		// 3 uncaughtException + 2 unhandledRejection = 5 total → exit.
		const handlers = _installErrorHandlers({
			userDataDir: tmpDir,
			exit: (code) => exitCalls.push(code),
		});

		try {
			emitUncaught(3);
			emitRejection(2);
		} finally {
			handlers.dispose();
		}

		// Both logs received their respective events.
		const { crashLogPath, rejectionLogPath } = _crashLogPaths(tmpDir);
		const crash = fs.readFileSync(crashLogPath, "utf-8");
		const rej = fs.readFileSync(rejectionLogPath, "utf-8");
		expect(crash).toContain("boom 0");
		expect(crash).toContain("boom 2");
		expect(rej).toContain("reject 0");
		expect(rej).toContain("reject 1");

		// Breaker tripped exactly once on the 5th event.
		expect(exitCalls).toEqual([1]);
	});

	it("rotates the rejection log independently from the crash log", () => {
		const { rejectionLogPath } = _crashLogPaths(tmpDir);
		// Pre-seed the rejection log with oversized content.
		fs.writeFileSync(
			rejectionLogPath,
			"R_" + "y".repeat(DEFAULT_CRASH_LOG_MAX_BYTES + 512),
		);

		const handlers = _installErrorHandlers({
			userDataDir: tmpDir,
			exit: () => {
				/* swallow */
			},
		});

		try {
			emitRejection(1);
		} finally {
			handlers.dispose();
		}

		// Rejection log rotated to .1.
		expect(fs.existsSync(`${rejectionLogPath}.1`)).toBe(true);
		expect(
			fs.readFileSync(`${rejectionLogPath}.1`, "utf-8").startsWith("R_"),
		).toBe(true);
		// Active rejection log holds the new event.
		expect(fs.readFileSync(rejectionLogPath, "utf-8")).toContain(
			"[unhandledRejection]",
		);
	});

	it("dispose() removes the process listeners (no leak across tests)", () => {
		const before = process.listenerCount("uncaughtException");
		const handlers = _installErrorHandlers({
			userDataDir: tmpDir,
			exit: () => {},
		});
		const during = process.listenerCount("uncaughtException");
		expect(during).toBe(before + 1);
		handlers.dispose();
		const after = process.listenerCount("uncaughtException");
		expect(after).toBe(before);
	});
});

describe("_installErrorHandlers — REVIEW-9 sliding window", () => {
	let tmpDir: string;
	let exitCalls: number[];
	let realNow: typeof Date.now;
	let nowMs: number;

	beforeEach(() => {
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-sliding-test-"));
		exitCalls = [];
		// Stub Date.now so the test can advance virtual time without
		// waiting 60s of wall-clock. We save realNow to restore in
		// afterEach.
		realNow = Date.now;
		nowMs = 1_000_000;
		Date.now = () => nowMs;
	});

	afterEach(() => {
		Date.now = realNow;
		rmrf(tmpDir);
	});

	it("resets uncaughtCount after 60s of silence (isolated bursts do not trip the breaker)", () => {
		const handlers = _installErrorHandlers({
			userDataDir: tmpDir,
			exit: (code) => exitCalls.push(code),
		});

		try {
			// Burst 1: 4 errors (one short of the breaker).
			emitUncaught(4);
			expect(exitCalls).toEqual([]);

			// 61 seconds later — outside the sliding window.
			nowMs += 61_000;

			// Burst 2: 4 more errors. The first of these
			// resets the counter to 0 (window expired), so
			// 4 more errors is still below MAX_UNCAUGHT=5.
			emitUncaught(4);
			expect(exitCalls).toEqual([]);
		} finally {
			handlers.dispose();
		}
	});

	it("does NOT reset inside the sliding window (tight crash loop trips the breaker)", () => {
		const handlers = _installErrorHandlers({
			userDataDir: tmpDir,
			exit: (code) => exitCalls.push(code),
		});

		try {
			// 4 errors, then 30s gap (< 60s window), then 1 more.
			// Counter should NOT reset → breaker trips on the 5th.
			emitUncaught(4);
			nowMs += 30_000;
			emitUncaught(1);
			expect(exitCalls).toEqual([1]);
		} finally {
			handlers.dispose();
		}
	});
});
