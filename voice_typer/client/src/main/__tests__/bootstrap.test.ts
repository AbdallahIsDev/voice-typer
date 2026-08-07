/**
 * @vitest-environment node
 *
 *  (IMPL-7) — Electron crash log rotation + REVIEW-12/REVIEW-9
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
	//bootstrap.ts now also imports `clearElectronPidFile`
	// and calls it inside the production exit hook. The test never
	// exercises that hook (it injects its own `exit` mock), but the
	// import + symbol binding still needs to resolve.
	clearElectronPidFile: vi.fn(),
}));

//bootstrap.ts now imports `stopPython` from `./python` so the
// production exit hook can call it before `app.quit()`. The real
// `./python` index transitively imports `./send-to-python` → `../index`
// (the main entry, which fires Electron APIs at module-eval time). We
// mock `./python` to short-circuit that chain — the test only needs the
// `stopPython` symbol to exist; it never invokes it (the test injects
// its own `exit` hook that records calls without calling stopPython).
vi.mock("../python", () => ({
	stopPython: vi.fn(),
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
import {
	_resetFileSizeCacheForTest,
	DEFAULT_CRASH_LOG_MAX_BYTES,
	rotateIfNeeded,
} from "../logging";

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
		(process.emit as (event: string, ...args: unknown[]) => boolean)(
			"uncaughtException",
			new Error(`${prefix} ${i}`),
		);
	}
}

/**
 * Helper: emit N `unhandledRejection` events on the real `process` object.
 */
function emitRejection(n: number, prefix = "reject"): void {
	for (let i = 0; i < n; i++) {
		(process.emit as (event: string, ...args: unknown[]) => boolean)(
			"unhandledRejection",
			new Error(`${prefix} ${i}`),
		);
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
		//reset the file-size cache so prior tests' cached stat
		// results don't prevent rotation (the cache keys by file path,
		// but reusing the same tmpDir root could produce stale hits if
		// the OS recycles inode-backed paths).
		_resetFileSizeCacheForTest();
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

	it("truncates the file in place when it exceeds the cap (no .1 backup)", () => {
		const target = path.join(tmpDir, "big.log");
		fs.writeFileSync(target, "x".repeat(2_000));
		rotateIfNeeded(target, 1_000);
		// Single-file policy: the file is emptied IN PLACE — it keeps
		// its single identity (no numbered .1 backup is ever created).
		expect(fs.existsSync(target)).toBe(true);
		expect(fs.statSync(target).size).toBe(0);
		expect(fs.existsSync(`${target}.1`)).toBe(false);
	});

	it("ignores a stale .1 backup from an old build (single-file policy never writes backups)", () => {
		const target = path.join(tmpDir, "rollover.log");
		// A leftover numbered backup from a PRE-single-file build must
		// be left untouched — the current policy never creates or
		// writes backups.
		fs.writeFileSync(`${target}.1`, "OLD_BACKUP_CONTENT");
		// Seed the active file with new oversized content.
		fs.writeFileSync(target, "x".repeat(2_000));
		rotateIfNeeded(target, 1_000);
		// The active file is truncated in place (0 bytes); the stale
		// backup is neither touched nor deleted.
		expect(fs.statSync(target).size).toBe(0);
		expect(fs.readFileSync(`${target}.1`, "utf-8")).toBe("OLD_BACKUP_CONTENT");
	});

	it("uses DEFAULT_CRASH_LOG_MAX_BYTES (1 MiB) when maxSize is omitted", () => {
		expect(DEFAULT_CRASH_LOG_MAX_BYTES).toBe(1_048_576);
		const target = path.join(tmpDir, "default-cap.log");
		// Exactly at the cap → no rotation (size <= maxSize).
		fs.writeFileSync(target, "x".repeat(DEFAULT_CRASH_LOG_MAX_BYTES));
		rotateIfNeeded(target);
		expect(fs.existsSync(target)).toBe(true);
		expect(fs.statSync(target).size).toBe(DEFAULT_CRASH_LOG_MAX_BYTES);
		// One byte over the cap → truncate in place. Reset the cache
		// first — the prior rotateIfNeeded call cached the file size
		// (1048576), and fs.appendFileSync doesn't update the cache,
		// so the second call would see the stale cached size and skip
		// truncation.
		_resetFileSizeCacheForTest();
		fs.appendFileSync(target, "y");
		rotateIfNeeded(target);
		expect(fs.existsSync(target)).toBe(true);
		expect(fs.statSync(target).size).toBe(0);
		expect(fs.existsSync(`${target}.1`)).toBe(false);
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
		const preseed = `PRESEED_${"x".repeat(DEFAULT_CRASH_LOG_MAX_BYTES + 1024)}`;
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

		// Single-file policy: the oversized pre-seed was truncated IN
		// PLACE on the first emit — no .1 backup exists, and the
		// active file holds the 6 new crash lines (each ~hundreds of
		// bytes, well under the 1 MiB cap, so no further truncation).
		expect(fs.existsSync(`${crashLogPath}.1`)).toBe(false);
		expect(fs.existsSync(crashLogPath)).toBe(true);
		const active = fs.readFileSync(crashLogPath, "utf-8");
		expect(active).toContain("[uncaughtException]");
		expect(active).toContain("boom 0");
		expect(active).toContain("boom 5");
		// The pre-seed was truncated away, not preserved in a backup.
		expect(active).not.toContain("PRESEED");

		// Circuit breaker: MAX_UNCAUGHT=5, so exit(1) was called
		// on the 5th error AND on the 6th (count is still >= 5).
		expect(exitCalls.length).toBeGreaterThanOrEqual(1);
		expect(exitCalls.every((c) => c === 1)).toBe(true);

		//separation: the rejection log must NOT have been
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
			`R_${"y".repeat(DEFAULT_CRASH_LOG_MAX_BYTES + 512)}`,
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

		// Single-file policy: the oversized pre-seed was truncated in
		// place — no .1 backup exists, and the active file holds the
		// new event.
		expect(fs.existsSync(`${rejectionLogPath}.1`)).toBe(false);
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
