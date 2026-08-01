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
 *   3. `rotateIfNeeded` with a mocked `fs.renameSync` failure records
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

	it("rotateIfNeeded records a failure entry when fs.renameSync throws", async () => {
		const { rotateIfNeeded, getLoggingHealth } = await import("../rotation");
		// Pre-create a log file that exceeds the rotation threshold so
		// `rotateIfNeeded` enters the rename branch.
		fs.writeFileSync(logPath, "x".repeat(100), { mode: 0o644 });
		// Mock `fs.renameSync` to throw an EXDEV (cross-device link)
		// error — the kind of failure `rotateIfNeeded` catches.
		const renameSpy = vi.spyOn(fs, "renameSync").mockImplementation(() => {
			const err = new Error(
				"EXDEV: cross-device link not permitted",
			) as NodeJS.ErrnoException;
			err.code = "EXDEV";
			throw err;
		});

		rotateIfNeeded(logPath, 10);

		const health = getLoggingHealth();
		expect(health.length).toBe(1);
		expect(health[0]?.operation).toBe("rotateIfNeeded");
		expect(health[0]?.filePath).toBe(logPath);
		expect(health[0]?.error).toContain("EXDEV");
		expect(health[0]?.error).toContain("cross-device link");

		renameSpy.mockRestore();
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

	it("appendLifecycleLine (structuredLogger.ts) records a failure when its try block throws", async () => {
		// Import `appendLifecycleLine` from `../structuredLogger`. The
		// structuredLogger module imports `recordLoggingFailure` from
		// `../rotation` and calls it in the catch block of
		// `appendLifecycleLine`. This test proves the cross-module
		// wiring.
		//
		// To force the try block to throw, we mock `appendLogLine` (the
		// last call in `appendLifecycleLine`'s try block). When
		// `appendLogLine` throws, the catch block fires and records the
		// failure to the ring buffer (operation: "appendLifecycleLine").
		//
		// `appendLogLine` is imported into `structuredLogger.ts` from
		// `./rotation` — mocking the rotation module's `appendLogLine`
		// export via `vi.mock("../rotation", ...)` would normally also
		// replace `getLoggingHealth`, breaking the test. Instead, we
		// spy on the real `fs.appendFileSync` (which `appendLogLine`
		// calls internally) — the failure propagates up through
		// `appendLogLine`'s own catch (recording an "appendLogLine"
		// entry), then `appendLifecycleLine`'s call to `appendLogLine`
		// returns normally (it swallowed the error). To force
		// `appendLifecycleLine`'s OWN catch to fire, we instead mock
		// `lifecycleLogPath`'s `app.getPath` to throw.

		// Re-mock `electron` so `app.getPath` throws — this forces
		// `lifecycleLogPath()` to throw inside `appendLifecycleLine`'s
		// try block, hitting its OWN catch (which records
		// "appendLifecycleLine", not "appendLogLine").
		vi.doMock("electron", () => ({
			app: {
				getPath: () => {
					throw new Error("userData path unavailable");
				},
				isPackaged: false,
			},
		}));
		vi.resetModules();
		// Re-import the rotation module so `_resetLoggingHealthForTest`
		// and `getLoggingHealth` resolve to the fresh module instance
		// that `structuredLogger.ts` will import after `vi.resetModules`.
		const rotationMod = await import("../rotation");
		rotationMod._resetLoggingHealthForTest();
		const structuredMod = await import("../structuredLogger");

		// Suppress the console.warn that the catch block emits (dev
		// noise).
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		try {
			structuredMod.appendLifecycleLine("info", "test msg", []);
		} finally {
			warnSpy.mockRestore();
		}

		const health = rotationMod.getLoggingHealth();
		expect(health.length).toBe(1);
		expect(health[0]?.operation).toBe("appendLifecycleLine");
		// The `filePath` is empty because `lifecycleLogPath()` threw
		// before `p` was assigned — the catch block records with an
		// empty path (documented in the structuredLogger.ts comment).
		expect(health[0]?.filePath).toBe("");
		expect(health[0]?.error).toContain("userData path unavailable");
	});

	it("multiple distinct failures accumulate in the ring buffer in insertion order", async () => {
		const { appendLogLine, rotateIfNeeded, getLoggingHealth } = await import(
			"../rotation"
		);
		// First: trigger a rotateIfNeeded failure (rename throws).
		fs.writeFileSync(logPath, "x".repeat(100), { mode: 0o644 });
		const renameSpy = vi.spyOn(fs, "renameSync").mockImplementation(() => {
			throw new Error("rename failure A");
		});
		rotateIfNeeded(logPath, 10);
		renameSpy.mockRestore();

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
		expect(health[0]?.error).toContain("rename failure A");
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
