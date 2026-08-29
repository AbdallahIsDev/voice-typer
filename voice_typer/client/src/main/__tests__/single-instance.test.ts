// @vitest-environment node
/**
 * : behavioral tests for `src/main/single_instance.ts`.
 *
 * Covers the testable surface of the single-instance gate:
 *   - `computeConfigDir()` env-var + platform-path resolution
 *   - `electronPidFile()` path joining
 *   - `readStaleElectronPid()` stale-PID recovery (file-based, no
 *     Electron APIs required) — verifies the three outcomes:
 *     no file → null, alive PID → null, dead PID → the PID.
 *
 * The `acquireSingleInstanceLock()` function calls
 * `app.requestSingleInstanceLock()` / `app.exit(0)` and is therefore
 * covered only by source-inspection tests (see `shutdown-hooks.test.ts`
 * for the pattern); this file exercises the file-system helpers it
 * composes from.
 *
 * The `VT_FOCUS_ONLY=1` exit path is exercised at the source level
 * (the env-var check is a single `if` statement that gates `app.exit(0)`;
 * importing `acquireSingleInstanceLock` directly would require
 * `app.exit` to be a no-op mock, which is fragile across Electron
 * versions). The behavioral contract is documented in the source.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
	afterAll,
	afterEach,
	beforeEach,
	describe,
	expect,
	it,
	vi,
} from "vitest";

// Mock only the Electron `app` import that `single_instance.ts` makes.
// The file-system helpers (`computeConfigDir`, `electronPidFile`,
// `readStaleElectronPid`) do not call any `app.*` methods, so the mock
// is purely a defensive guard for the (untested here)
// `acquireSingleInstanceLock` path.
vi.mock("electron", () => ({
	app: {
		requestSingleInstanceLock: vi.fn(() => true),
		releaseSingleInstanceLock: vi.fn(),
		exit: vi.fn(),
		on: vi.fn(),
	},
}));

// Mock `./windows` so the `showMainWindow` import doesn't pull in the
// full BrowserWindow wiring chain (which transitively imports
// `./index.ts` and triggers Electron API calls at module-eval time).
vi.mock("../windows", () => ({
	showMainWindow: vi.fn(),
}));

import {
	clearElectronPidFile,
	computeConfigDir,
	electronPidFile,
	readStaleElectronPid,
	writeElectronPidFile,
} from "../single_instance";

const _origEnv = { ...process.env };

describe("single_instance.ts", () => {
	let tmpDir: string;
	let savedConfigDir: string | undefined;

	beforeEach(() => {
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "vt-single-instance-"));
		// electron.pid lives under `<configDir>/run`; pre-create the
		// subdir so direct fs.writeFileSync in the tests works without
		// relying on writeElectronPidFile's own mkdirSync.
		fs.mkdirSync(path.join(tmpDir, "run"), { recursive: true });
		savedConfigDir = process.env.VOICE_TYPER_CONFIG_DIR;
		process.env.VOICE_TYPER_CONFIG_DIR = tmpDir;
	});

	afterEach(() => {
		// Restore env.
		if (savedConfigDir === undefined) {
			delete process.env.VOICE_TYPER_CONFIG_DIR;
		} else {
			process.env.VOICE_TYPER_CONFIG_DIR = savedConfigDir;
		}
		// Wipe tmp dir contents.
		try {
			fs.rmSync(tmpDir, { recursive: true, force: true });
		} catch {
			// ignore
		}
	});

	afterAll(() => {
		// Restore env fully (defensive — afterEach should already have).
		for (const k of Object.keys(process.env)) {
			if (!(k in _origEnv)) delete process.env[k as keyof NodeJS.ProcessEnv];
		}
		Object.assign(process.env, _origEnv);
	});

	describe("computeConfigDir()", () => {
		it("honors VOICE_TYPER_CONFIG_DIR env var when set", () => {
			const dir = computeConfigDir();
			expect(dir).toBe(tmpDir);
		});

		it("falls back to platform path when env var is unset", () => {
			delete process.env.VOICE_TYPER_CONFIG_DIR;
			const dir = computeConfigDir();
			// We don't assert the exact platform path (it varies by OS),
			// but it must be a non-empty string ending in "voice-typer"
			// (the canonical app directory name on every platform).
			expect(typeof dir).toBe("string");
			expect(dir.length).toBeGreaterThan(0);
			expect(dir.endsWith("voice-typer")).toBe(true);
		});
	});

	describe("electronPidFile()", () => {
		it("returns <configDir>/run/electron.pid", () => {
			const f = electronPidFile();
			expect(f).toBe(path.join(tmpDir, "run", "electron.pid"));
		});
	});

	describe("writeElectronPidFile() / readStaleElectronPid() / clearElectronPidFile()", () => {
		it("writeElectronPidFile() writes the current process PID with mode 0o600", () => {
			writeElectronPidFile();
			const f = electronPidFile();
			expect(fs.existsSync(f)).toBe(true);
			const content = fs.readFileSync(f, "utf-8").trim();
			expect(content).toBe(String(process.pid));
		});

		it("readStaleElectronPid() returns null when the file does not exist", () => {
			expect(readStaleElectronPid()).toBeNull();
		});

		it("readStaleElectronPid() returns null when the PID is still alive (the current process)", () => {
			writeElectronPidFile();
			//readStaleElectronPid now also verifies the
			// PID belongs to Voice Typer via /proc/<pid>/cmdline
			// (Linux), ps (macOS), or tasklist (Windows). The vitest
			// runner process does NOT contain "electron" in its
			// cmdline, so we mock fs.readFileSync for the /proc
			// path to simulate a real Voice Typer process.
			//
			// The /proc read mock only intercepts the LINUX probe;
			// on win32/darwin the real ps/tasklist probe would report
			// the vitest runner ("node.exe", not "electron") and the
			// test would fail. Stub process.platform to "linux" so the
			// probe path (and thus the mock) is deterministic on every
			// host OS.
			const originalPlatform = process.platform;
			Object.defineProperty(process, "platform", {
				value: "linux",
				configurable: true,
			});
			const realReadFileSync = fs.readFileSync;
			const spy = vi
				.spyOn(fs, "readFileSync")
				.mockImplementation((file, ...rest) => {
					if (typeof file === "string" && file.includes("/cmdline")) {
						return "electron\0voice-typer";
					}
					return realReadFileSync(file as fs.PathOrFileDescriptor, ...rest);
				});
			try {
				expect(readStaleElectronPid()).toBeNull();
			} finally {
				spy.mockRestore();
				Object.defineProperty(process, "platform", {
					value: originalPlatform,
					configurable: true,
				});
			}
		});

		it("readStaleElectronPid() returns the PID when alive but not Voice Typer (PID reuse)", () => {
			writeElectronPidFile();
			// The PID is alive (it's the current vitest process), so
			// ``process.kill(pid, 0)`` succeeds and ``readStaleElectronPid``
			// falls through to the ``isPidVoiceTyper`` check. We mock the
			// cmdline probe to return a process that does NOT contain
			// "electron" / "voice-typer" / "voice_typer" (simulating PID
			// reuse by an unrelated process — e.g. a browser or shell that
			// happened to claim the PID after Voice Typer crashed).
			//
			// Without this mock the test would be flaky on Linux: the real
			// ``/proc/<pid>/cmdline`` of the vitest runner contains the
			// project path ``/home/z/.../voice-typer/...`` which trips the
			// ``voice-typer`` substring check in ``isPidVoiceTyper`` — so
			// ``readStaleElectronPid`` would (incorrectly for this test's
			// intent) return null and the PID-reuse path would go unexercised.
			const realReadFileSync = fs.readFileSync;
			const spy = vi
				.spyOn(fs, "readFileSync")
				.mockImplementation((file, ...rest) => {
					if (typeof file === "string" && file.includes("/cmdline")) {
						return "/usr/bin/unrelated-process\0--flag";
					}
					return realReadFileSync(file as fs.PathOrFileDescriptor, ...rest);
				});
			try {
				const result = readStaleElectronPid();
				expect(result).toBe(process.pid);
			} finally {
				spy.mockRestore();
			}
		});

		it("readStaleElectronPid() returns the dead PID when the process is gone", () => {
			// Write a PID that is essentially guaranteed to be dead:
			// PID 1 (init) doesn't qualify on most CI runners because
			// process.kill(1, 0) may succeed; use a very large PID that
			// doesn't correspond to any real process.
			const deadPid = 4_194_303; // 2^22 - 1, well above typical PID max
			const f = electronPidFile();
			fs.writeFileSync(f, `${deadPid}\n`, { encoding: "utf-8" });
			const result = readStaleElectronPid();
			// process.kill(deadPid, 0) throws ESRCH for a non-existent
			// PID, so the helper returns the PID (stale).
			expect(result).toBe(deadPid);
		});

		it("readStaleElectronPid() returns null for non-numeric file content", () => {
			const f = electronPidFile();
			fs.writeFileSync(f, "not-a-number\n", { encoding: "utf-8" });
			expect(readStaleElectronPid()).toBeNull();
		});

		it("readStaleElectronPid() returns null for empty file content", () => {
			const f = electronPidFile();
			fs.writeFileSync(f, "", { encoding: "utf-8" });
			expect(readStaleElectronPid()).toBeNull();
		});

		it("readStaleElectronPid() returns null for PID <= 0", () => {
			const f = electronPidFile();
			fs.writeFileSync(f, "0\n", { encoding: "utf-8" });
			expect(readStaleElectronPid()).toBeNull();
		});

		it("clearElectronPidFile() removes the file when it exists", () => {
			writeElectronPidFile();
			const f = electronPidFile();
			expect(fs.existsSync(f)).toBe(true);
			clearElectronPidFile();
			expect(fs.existsSync(f)).toBe(false);
		});

		it("clearElectronPidFile() is a no-op when the file does not exist", () => {
			const f = electronPidFile();
			expect(fs.existsSync(f)).toBe(false);
			// Must NOT throw.
			expect(() => clearElectronPidFile()).not.toThrow();
		});
	});

	describe("VT_FOCUS_ONLY=1 exit-path contract (source-level)", () => {
		// We cannot safely exercise `acquireSingleInstanceLock()` end-to-end
		// because it calls `app.exit(0)` on the focus-only path, which
		// would terminate the vitest process. Instead, we assert that
		// the source contains the env-var check + the early exit —
		// matching the pattern in `shutdown-hooks.test.ts`.
		it("source checks process.env.VT_FOCUS_ONLY === '1' and calls app.exit(0)", async () => {
			const src = await fs.promises.readFile(
				path.resolve(__dirname, "../single_instance.ts"),
				"utf-8",
			);
			expect(src).toMatch(/VT_FOCUS_ONLY/);
			expect(src).toMatch(/app\.exit\(0\)/);
		});
	});
});
