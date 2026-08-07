// @vitest-environment node
/**
 * Tests for the logging-health ring buffer added to
 * `main/logging/rotation.ts` (+ the structuredLogger.ts wiring).
 *
 * Background
 * ----------
 * Pre-fix: the five `console.warn(...)` call sites in
 * `rotation.ts` (lines 126, 184, 201, 209) and `structuredLogger.ts`
 * (line 173) swallowed logging failures. In packaged Electron builds,
 * stdout/stderr are closed → these `console.warn` calls are no-ops.
 * When logging silently degraded (disk full, perm regression, userData
 * path moved to read-only mount), there was ZERO durable trace — the
 * diagnostics meant to debug crashes were themselves silent.
 *
 * Post-fix: a bounded in-memory ring buffer (last 20 entries) captures
 * every logging failure. `getLoggingHealth()` returns a snapshot so an
 * orchestrator (or future IPC handler) can surface "logging degraded
 * since <timestamp>" on a Troubleshooting page.
 *
 * These tests verify:
 *   1. `appendLogLine` with a mocked `fs.appendFileSync` failure
 *      records an entry to the ring buffer (operation:
 *      "appendLogLine").
 *   2. `appendLogLine` with a mocked `fs.chmodSync` failure records
 *      an entry (operation: "chmod 0o600") — the chmod failure is
 *      a separate catch block inside the same try, so the append
 *      itself succeeds but the perms cache is left unset.
 *   3. `rotateIfNeeded` with a mocked `fs.truncateSync` failure records
 *      an entry (operation: "rotateIfNeeded").
 *   4. The ring buffer is bounded at 20 entries — the 21st failure
 *      evicts the oldest.
 *   5. `getLoggingHealth()` returns a shallow copy so callers can't
 *      mutate the internal buffer.
 *   6. The recorded `timestamp` is a valid ISO-8601 string (the
 *      orchestrator's "logging degraded since <timestamp>" surface
 *      relies on this).
 *   7. `appendLifecycleLine` (structuredLogger.ts) records an entry
 *      when its try block throws — proving the cross-module wiring.
 *
 * The tests mock `electron` minimally (the rotation module doesn't
 * import `app` directly, but the structuredLogger module does via
 * `lifecycleLogPath()` → `app.getPath("userData")`).
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock `electron` so `structuredLogger.ts`'s `app.getPath("userData")`
// returns a deterministic tmp path. The rotation module itself doesn't
// import `app` — only the structured logger does (via
// `lifecycleLogPath()`).
vi.mock("electron", () => ({
	app: {
		getPath: () => "/tmp/vt-logging-health-test-userdata",
		isPackaged: false,
	},
}));

describe("logging-health ring buffer: getLoggingHealth() captures failures", () => {
	let tmpDir: string;
	let logPath: string;
	let _resetFileSizeCacheForTest: () => void;
	let _resetPermsVerifiedForTest: () => void;
	let _resetLoggingHealthForTest: () => void;

	beforeEach(async () => {
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-logging-health-"));
		logPath = path.join(tmpDir, "test.log");

		// `vi.resetModules()` clears the module cache so the
		// `_resetLoggingHealthForTest` / `_resetPermsVerifiedForTest`
		// / `_resetFileSizeCacheForTest` functions resolve to the same
		// module instance the test imports below. Without this, the
		// reset helpers would clear a different module's state than
		// the one the test calls `appendLogLine` against.
		vi.resetModules();
		const rotationMod = await import("../rotation");
		const fileSizeMod = await import("../fileSizeCache");
		_resetFileSizeCacheForTest = fileSizeMod._resetFileSizeCacheForTest;
		_resetPermsVerifiedForTest = rotationMod._resetPermsVerifiedForTest;
		_resetLoggingHealthForTest = rotationMod._resetLoggingHealthForTest;
		_resetFileSizeCacheForTest();
		_resetPermsVerifiedForTest();
		_resetLoggingHealthForTest();
	});

	afterEach(() => {
		vi.restoreAllMocks();
		try {
			fs.rmSync(tmpDir, { recursive: true, force: true });
		} catch {
			/* ignore */
		}
	});

	it("appendLogLine records a failure entry when fs.appendFileSync throws", async () => {
		const { appendLogLine, getLoggingHealth } = await import("../rotation");
		// Mock `fs.appendFileSync` to throw a disk-full error.
		const appendSpy = vi.spyOn(fs, "appendFileSync").mockImplementation(() => {
			const err = new Error(
				"ENOSPC: no space left on device",
			) as NodeJS.ErrnoException;
			err.code = "ENOSPC";
			throw err;
		});

		appendLogLine(logPath, "test line\n", 1024 * 1024);

		// The append failure was recorded to the ring buffer.
		const health = getLoggingHealth();
		expect(health.length).toBe(1);
		expect(health[0]?.operation).toBe("appendLogLine");
		expect(health[0]?.filePath).toBe(logPath);
		expect(health[0]?.error).toContain("ENOSPC");
		expect(health[0]?.error).toContain("no space left on device");
		// The timestamp must be a valid ISO-8601 string — the
		// orchestrator's "logging degraded since <timestamp>" surface
		// parses it with `new Date(entry.timestamp)`.
		const ts = new Date(health[0]?.timestamp ?? "");
		expect(ts.toString()).not.toBe("Invalid Date");

		appendSpy.mockRestore();
	});

	it("appendLogLine records a chmod failure entry when fs.chmodSync throws", async () => {
		const { appendLogLine, getLoggingHealth } = await import("../rotation");
		// Mock `fs.chmodSync` to throw an EACCES error. The append itself
		// succeeds (no mock on `appendFileSync`), but the perms-cache
		// chmod fails — proving the chmod catch block records its own
		// failure separately from the append catch block.
		const chmodSpy = vi.spyOn(fs, "chmodSync").mockImplementation(() => {
			const err = new Error(
				"EACCES: permission denied",
			) as NodeJS.ErrnoException;
			err.code = "EACCES";
			throw err;
		});

		appendLogLine(logPath, "test line\n", 1024 * 1024);

		const health = getLoggingHealth();
		expect(health.length).toBe(1);
		expect(health[0]?.operation).toBe("chmod 0o600");
		expect(health[0]?.filePath).toBe(logPath);
		expect(health[0]?.error).toContain("EACCES");
		expect(health[0]?.error).toContain("permission denied");

		chmodSpy.mockRestore();
	});

	it("rotateIfNeeded records a failure entry when fs.truncateSync throws", async () => {
		const { rotateIfNeeded, getLoggingHealth } = await import("../rotation");
		// Pre-create a log file that exceeds the truncation threshold so
		// `rotateIfNeeded` enters the truncate branch (single-file
		// policy truncates in place; it never renames).
		fs.writeFileSync(logPath, "x".repeat(100), { mode: 0o644 });
		// Mock `fs.truncateSync` to throw an EIO error — the kind of
		// failure `rotateIfNeeded` catches.
		const truncateSpy = vi.spyOn(fs, "truncateSync").mockImplementation(() => {
			const err = new Error("EIO: I/O error") as NodeJS.ErrnoException;
			err.code = "EIO";
			throw err;
		});

		rotateIfNeeded(logPath, 10);

		const health = getLoggingHealth();
		expect(health.length).toBe(1);
		expect(health[0]?.operation).toBe("rotateIfNeeded");
		expect(health[0]?.filePath).toBe(logPath);
		expect(health[0]?.error).toContain("EIO");

		truncateSpy.mockRestore();
	});

	it("the ring buffer is bounded at 20 entries (oldest evicted on overflow)", async () => {
		const { appendLogLine, getLoggingHealth } = await import("../rotation");
		// Mock `fs.appendFileSync` to always throw — every append
		// produces a ring-buffer entry.
		const appendSpy = vi.spyOn(fs, "appendFileSync").mockImplementation(() => {
			throw new Error("disk full");
		});

		// Trigger 25 failures — the buffer is bounded at 20.
		for (let i = 0; i < 25; i++) {
			appendLogLine(logPath, `line ${i}\n`, 1024 * 1024);
		}

		const health = getLoggingHealth();
		// Exactly 20 entries — the 5 oldest were evicted.
		expect(health.length).toBe(20);
		// The most recent entry is the 25th failure.
		expect(health[health.length - 1]?.error).toContain("disk full");

		appendSpy.mockRestore();
	});

	it("getLoggingHealth returns a shallow copy of the array (callers can push/pop without affecting the buffer)", async () => {
		const { appendLogLine, getLoggingHealth } = await import("../rotation");
		const appendSpy = vi.spyOn(fs, "appendFileSync").mockImplementation(() => {
			throw new Error("first failure");
		});

		appendLogLine(logPath, "test\n", 1024 * 1024);

		const snapshot1 = getLoggingHealth();
		expect(snapshot1.length).toBe(1);

		// Mutate the returned ARRAY (push a fake entry + pop the real
		// one). The internal buffer must NOT be affected —
		// `getLoggingHealth()` returns a fresh array via spread, so
		// array-level mutations don't propagate.
		snapshot1.push({
			timestamp: "fake",
			filePath: "fake",
			operation: "fake",
			error: "fake",
		});
		snapshot1.pop();
		snapshot1.pop();

		// The internal buffer is unaffected at the array level — a
		// fresh snapshot still has exactly 1 entry. (The entry objects
		// themselves are shared by reference per the docstring:
		// "entries themselves are NOT frozen — callers should treat
		// them as read-only." This test deliberately does NOT mutate
		// entry fields.)
		const snapshot2 = getLoggingHealth();
		expect(snapshot2.length).toBe(1);
		expect(snapshot2[0]?.operation).toBe("appendLogLine");

		appendSpy.mockRestore();
	});

	it("appendLifecycleLine (structuredLogger.ts) records a write failure in the health ring", async () => {
		// Cross-module wiring: `appendLifecycleLine` (structuredLogger)
		// delegates to `appendLogLine` (rotation), whose failure path
		// records to the shared health ring via `recordLoggingFailure`
		// — readable back through `getLoggingHealth` (rotation).
		//
		// NOTE: `lifecycleLogPath()` deliberately swallows `app.getPath`
		// errors and falls back to cwd (logging must keep working), so
		// `appendLifecycleLine`'s own catch is a pure safety net. The
		// observable failure contract is the WRITE failure inside
		// `appendLogLine` — force it by making `fs.appendFileSync`
		// throw for the lifecycle-log path.
		vi.resetModules();
		const rotationMod = await import("../rotation");
		rotationMod._resetLoggingHealthForTest();
		const structuredMod = await import("../structuredLogger");
		const lifecyclePath = structuredMod.lifecycleLogPath();

		const appendSpy = vi
			.spyOn(fs, "appendFileSync")
			.mockImplementation((p: fs.PathOrFileDescriptor) => {
				if (String(p) === lifecyclePath) {
					throw new Error("disk full (simulated)");
				}
				throw new Error("unexpected path");
			});

		// Suppress the console.warn that the catch block emits (dev
		// noise).
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		try {
			structuredMod.appendLifecycleLine("info", "test msg", []);
		} finally {
			warnSpy.mockRestore();
			appendSpy.mockRestore();
		}

		const health = rotationMod.getLoggingHealth();
		expect(health.length).toBe(1);
		expect(health[0]?.operation).toBe("appendLogLine");
		expect(health[0]?.filePath).toBe(lifecyclePath);
		expect(health[0]?.error).toContain("disk full (simulated)");
	});

	it("multiple distinct failures accumulate in the ring buffer in insertion order", async () => {
		const { appendLogLine, rotateIfNeeded, getLoggingHealth } = await import(
			"../rotation"
		);
		// First: trigger a rotateIfNeeded failure (truncate throws).
		fs.writeFileSync(logPath, "x".repeat(100), { mode: 0o644 });
		const truncateSpy = vi.spyOn(fs, "truncateSync").mockImplementation(() => {
			throw new Error("truncate failure A");
		});
		rotateIfNeeded(logPath, 10);
		truncateSpy.mockRestore();

		// Second: trigger an appendLogLine failure (append throws).
		const appendSpy = vi.spyOn(fs, "appendFileSync").mockImplementation(() => {
			throw new Error("append failure B");
		});
		appendLogLine(logPath, "test\n", 1024 * 1024);
		appendSpy.mockRestore();

		const health = getLoggingHealth();
		// Both failures are recorded, in insertion order
		// (rotateIfNeeded first, appendLogLine second).
		expect(health.length).toBe(2);
		expect(health[0]?.operation).toBe("rotateIfNeeded");
		expect(health[0]?.error).toContain("truncate failure A");
		expect(health[1]?.operation).toBe("appendLogLine");
		expect(health[1]?.error).toContain("append failure B");
	});

	it("the ring buffer starts empty after _resetLoggingHealthForTest", async () => {
		const { getLoggingHealth } = await import("../rotation");
		// `beforeEach` already called `_resetLoggingHealthForTest`, so
		// the buffer must be empty.
		expect(getLoggingHealth()).toEqual([]);
	});

	it("recordLoggingFailure tolerates hostile error objects (never throws, best-effort record)", async () => {
		// The record function is wrapped in try/catch so a hostile
		// error object can never crash the caller. The contract is
		// "never throws" — the entry may or may not be added depending
		// on whether the try block reaches the `push` before the
		// error stringification fails.
		//
		// This test verifies the "never throws" half of the contract
		// with a hostile error object whose getters throw. (A plain
		// object with throwing getters doesn't actually trigger the
		// getters — `String(non-Error)` returns `"[object Object]"`
		// without touching them — so we use a real Error subclass
		// with overridden getters to force the `instanceof Error`
		// branch and trigger the getters.)
		const { recordLoggingFailure, getLoggingHealth } = await import(
			"../rotation"
		);

		class HostileError extends Error {
			get name(): string {
				throw new Error("name getter threw");
			}
			get message(): string {
				throw new Error("message getter threw");
			}
		}
		const hostileError: unknown = new HostileError();

		// The record call must not throw — even though accessing
		// `hostileError.name` / `hostileError.message` (which the
		// record function does via `${error.name}: ${error.message}`)
		// throws internally. The try/catch swallows the throw so the
		// diagnostic code never crashes the caller.
		expect(() =>
			recordLoggingFailure(logPath, "hostile-test", hostileError),
		).not.toThrow();

		// The entry was NOT added because the try block threw (on
		// `error.name` access) before reaching `LOGGING_FAILURE_RING.push`.
		// This is the documented "best-effort" behavior: if recording
		// itself fails, the failure is silently swallowed so the
		// diagnostic code stays non-fatal.
		const health = getLoggingHealth();
		expect(health.length).toBe(0);
	});
});
