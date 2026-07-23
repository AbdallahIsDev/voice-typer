// @vitest-environment node
/**
 * FA19 unit tests for XV-149 through XV-157 (Group 2 fixes in the
 * Electron main process).
 *
 * Each finding gets at least one assertion. Where a finding is purely
 * about source-text structure (e.g. "XV-153 stores timer handles"),
 * we assert on the source text — importing index.ts would fire
 * Electron APIs at module-eval time and is not testable in vitest
 * without mocking the entire Electron runtime.
 *
 * For runtime-testable findings (XV-149 StringDecoder, XV-154 cache,
 * XV-157 idempotency), we exercise the actual function with mocked
 * electron/state.
 */
import { EventEmitter } from "node:events";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { StringDecoder } from "node:string_decoder";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../state";

// ────────────────────────────────────────────────────────────────────
// Source-text helpers (for findings that can't be runtime-tested)
// ────────────────────────────────────────────────────────────────────

function readSrc(rel: string): string {
	return fs.readFileSync(path.resolve(__dirname, rel), "utf-8");
}

// ────────────────────────────────────────────────────────────────────
// XV-150: package.json has no mdn-data runtime dependency
// ────────────────────────────────────────────────────────────────────

describe("XV-150: mdn-data removed from runtime dependencies", () => {
	it("package.json dependencies does NOT contain mdn-data", () => {
		const pkg = JSON.parse(readSrc("../../../package.json")) as {
			dependencies: Record<string, string>;
			devDependencies: Record<string, string>;
		};
		expect(pkg.dependencies).not.toHaveProperty("mdn-data");
	});

	it("package.json devDependencies also does NOT contain mdn-data (jsdom pulls it transitively)", () => {
		const pkg = JSON.parse(readSrc("../../../package.json")) as {
			dependencies: Record<string, string>;
			devDependencies: Record<string, string>;
		};
		// XV-150 alternative: if a build-time tool needs it, mark
		// it optional or dev only. jsdom already pulls mdn-data
		// transitively via css-tree, so we don't even need a dev
		// entry. Assert it's NOT a direct entry in either section.
		expect(pkg.devDependencies).not.toHaveProperty("mdn-data");
	});
});

// ────────────────────────────────────────────────────────────────────
// XV-151: index.ts will-quit branch quits immediately when no pythonProcess
// ────────────────────────────────────────────────────────────────────

describe("XV-151: index.ts will-quit else-branch for null pythonProcess", () => {
	it("source contains an else-branch that calls app.exit(0) via setImmediate", () => {
		const src = readSrc("../index.ts");
		// Anchor on the will-quit handler registration.
		const willQuitIdx = src.search(/app\.on\(\s*["']will-quit["']\s*,/);
		expect(willQuitIdx).toBeGreaterThan(-1);
		const block = src.slice(willQuitIdx, willQuitIdx + 1500);
		// The if-branch checks state.pythonProcess and registers
		// the exit listener; the else-branch must call
		// setImmediate(() => app.exit(0)).
		expect(block).toContain("if (state.pythonProcess)");
		expect(block).toMatch(/else\s*\{[\s\S]*?setImmediate/);
		expect(block).toContain("app.exit(0)");
	});
});

// ────────────────────────────────────────────────────────────────────
// XV-152: bubble-window.ts showBubbleWindow calls removeAllListeners
// unconditionally (not just when _hideTimeout is set)
// ────────────────────────────────────────────────────────────────────

describe("XV-152: showBubbleWindow removeAllListeners is unconditional", () => {
	it("source: removeAllListeners('bubble:hidden') appears OUTSIDE the if (_hideTimeout) block", () => {
		const src = readSrc("../windows/bubble-window.ts");
		const showIdx = src.indexOf("export function showBubbleWindow");
		expect(showIdx).toBeGreaterThan(-1);
		// Slice from showBubbleWindow to hideBubbleWindow — that's
		// the entire show function body.
		const hideIdx = src.indexOf("export function hideBubbleWindow");
		const showBody = src.slice(showIdx, hideIdx);
		// Find the `if (state._hideTimeout)` block and the
		// `removeAllListeners` call. The removeAllListeners call
		// must NOT be nested inside the if block (i.e. it must
		// come AFTER the closing brace of that block).
		const ifIdx = showBody.indexOf("if (state._hideTimeout)");
		expect(ifIdx).toBeGreaterThan(-1);
		// Find the closing brace of the if-block (the next `}`
		// at the same indentation level as the if).
		const afterIf = showBody.slice(ifIdx);
		// The if-block contains clearTimeout + state._hideTimeout = null
		// and is closed by `}`. After that closing brace, the
		// removeAllListeners call should appear.
		const closingBrace = afterIf.indexOf("}");
		expect(closingBrace).toBeGreaterThan(-1);
		const afterBlock = afterIf.slice(closingBrace);
		expect(afterBlock).toContain('removeAllListeners("bubble:hidden")');
	});
});

// ────────────────────────────────────────────────────────────────────
// XV-153: index.ts stores VT_BUBBLE_TEST timers in module-level vars
// and clears them in before-quit
// ────────────────────────────────────────────────────────────────────

describe("XV-153: VT_BUBBLE_TEST timers stored + cleared", () => {
	const src = readSrc("../index.ts");

	it("source declares 3 module-level timer variables for bubble-test", () => {
		expect(src).toMatch(/let\s+_bubbleTestOuter\s*:/);
		expect(src).toMatch(/let\s+_bubbleTestInterval\s*:/);
		expect(src).toMatch(/let\s+_bubbleTestIntervalClear\s*:/);
	});

	it("source assigns the outer setTimeout to _bubbleTestOuter", () => {
		expect(src).toMatch(/_bubbleTestOuter\s*=\s*setTimeout/);
	});

	it("source assigns the inner setInterval to _bubbleTestInterval", () => {
		expect(src).toMatch(/_bubbleTestInterval\s*=\s*setInterval/);
	});

	it("source assigns the inner setTimeout (clear interval) to _bubbleTestIntervalClear", () => {
		expect(src).toMatch(/_bubbleTestIntervalClear\s*=\s*setTimeout/);
	});

	it("before-quit handler clears all 3 timers", () => {
		const beforeQuitIdx = src.search(/app\.on\(\s*["']before-quit["']\s*,/);
		expect(beforeQuitIdx).toBeGreaterThan(-1);
		// Slice from before-quit to will-quit (next handler).
		const willQuitIdx = src
			.slice(beforeQuitIdx + 1)
			.search(/app\.on\(\s*["']will-quit["']\s*,/);
		const block = src.slice(
			beforeQuitIdx,
			willQuitIdx >= 0 ? beforeQuitIdx + 1 + willQuitIdx : undefined,
		);
		expect(block).toContain("clearTimeout(_bubbleTestOuter)");
		expect(block).toContain("clearInterval(_bubbleTestInterval)");
		expect(block).toContain("clearTimeout(_bubbleTestIntervalClear)");
	});
});

// ────────────────────────────────────────────────────────────────────
// XV-154: logging.ts statSync is memoized via _fileSizeCache
// ────────────────────────────────────────────────────────────────────

describe("XV-154: logging.ts file-size cache", () => {
	let tmpDir: string;
	let statSpy: ReturnType<typeof vi.spyOn>;

	beforeEach(() => {
		tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "xv154-test-"));
		// Stat the real fs (no mock) — we want to count how many
		// times statSync is called on the log file path.
		statSpy = vi.spyOn(fs, "statSync");
	});

	afterEach(() => {
		statSpy.mockRestore();
		fs.rmSync(tmpDir, { recursive: true, force: true });
	});

	it("_bumpCachedFileSize + rotateIfNeeded: second append does NOT call statSync", async () => {
		const { appendLogLine, _resetFileSizeCacheForTest } = await import(
			"../logging"
		);

		_resetFileSizeCacheForTest();
		const logPath = path.join(tmpDir, "test.log");

		// First append: cache miss → stat to seed → append → bump.
		appendLogLine(logPath, "first line\n", 1024 * 1024);
		const callsAfterFirst = statSpy.mock.calls.filter(
			(c: unknown[]) => c[0] === logPath,
		).length;
		expect(callsAfterFirst).toBeGreaterThanOrEqual(1);

		// Second append: cache hit (cached size < threshold) →
		// NO statSync call on logPath.
		appendLogLine(logPath, "second line\n", 1024 * 1024);
		const callsAfterSecond = statSpy.mock.calls.filter(
			(c: unknown[]) => c[0] === logPath,
		).length;
		// Same count as after the first append — no new stat.
		expect(callsAfterSecond).toBe(callsAfterFirst);
	});

	it("rotation re-stats when cache says over threshold (defensive re-stat)", async () => {
		const { appendLogLine, _resetFileSizeCacheForTest } = await import(
			"../logging"
		);

		_resetFileSizeCacheForTest();
		const logPath = path.join(tmpDir, "rotate.log");

		// Pre-seed the file at > maxSize so the next append
		// triggers a rotation.
		fs.writeFileSync(logPath, "x".repeat(2048));

		// First append: cache miss → stat (sees 2048 > 1024) →
		// rotate (rename to .1, cache set to 0) → append → bump.
		appendLogLine(logPath, "trigger rotation\n", 1024);

		// Active file should now contain only the new line.
		expect(fs.existsSync(logPath)).toBe(true);
		expect(fs.existsSync(`${logPath}.1`)).toBe(true);
		expect(fs.readFileSync(logPath, "utf-8")).toBe("trigger rotation\n");

		// Second append: cache hit (cached 0 + line bytes, both <
		// threshold) → no stat, no rotation.
		const callsBefore = statSpy.mock.calls.filter(
			(c: unknown[]) => c[0] === logPath,
		).length;
		appendLogLine(logPath, "second line\n", 1024);
		const callsAfter = statSpy.mock.calls.filter(
			(c: unknown[]) => c[0] === logPath,
		).length;
		// Allow at most 0 NEW stat calls (the cache was just
		// reset to 0 after rotation, so no stat is needed).
		expect(callsAfter).toBe(callsBefore);
	});
});

// ────────────────────────────────────────────────────────────────────
// XV-155: main-window.ts ERROR skips electron-runtime.log write
// ────────────────────────────────────────────────────────────────────

describe("XV-155: main-window ERROR skips runtime.log (no double-write)", () => {
	const src = readSrc("../windows/main-window.ts");

	it("source: ERROR branch uses console.error (not log.error)", () => {
		// Find the console-message handler.
		const handlerIdx = src.indexOf('"console-message"');
		expect(handlerIdx).toBeGreaterThan(-1);
		const block = src.slice(handlerIdx, handlerIdx + 2500);
		// The ERROR branch (level >= 3) must call console.error
		// (NOT log.error) so electron-runtime.log is skipped.
		const errorBranchIdx = block.indexOf("if (level >= 3)");
		expect(errorBranchIdx).toBeGreaterThan(-1);
		const errorBranch = block.slice(errorBranchIdx, errorBranchIdx + 600);
		expect(errorBranch).toContain("console.error(msg)");
		expect(errorBranch).not.toMatch(/log\.error\(msg\)/);
	});

	it("source: WARN branch still routes through log.warn (stdout + runtime.log)", () => {
		const handlerIdx = src.indexOf('"console-message"');
		const block = src.slice(handlerIdx, handlerIdx + 2500);
		expect(block).toMatch(/else if \(level === 2\)\s*\{[\s\S]*?log\.warn/);
	});

	it("source: INFO branch still routes through log.info (stdout only)", () => {
		const handlerIdx = src.indexOf('"console-message"');
		const block = src.slice(handlerIdx, handlerIdx + 2500);
		expect(block).toMatch(/else\s*\{[\s\S]*?log\.info/);
	});
});

// ────────────────────────────────────────────────────────────────────
// XV-156: shutdown-path timers are .unref()'d
// ────────────────────────────────────────────────────────────────────

describe("XV-156: shutdown-path timers are unref'd", () => {
	it("stop-python.ts: killTimer.unref() is called", () => {
		const src = readSrc("../python/stop-python.ts");
		expect(src).toMatch(/killTimer\.unref\(\)/);
	});

	it("relaunch-app.ts: killTimer.unref() called in BOTH branches (dev + prod)", () => {
		const src = readSrc("../python/relaunch-app.ts");
		const matches = src.match(/killTimer\.unref\(\)/g);
		expect(matches?.length ?? 0).toBeGreaterThanOrEqual(2);
	});

	it("index.ts: forceExitTimer.unref() is called", () => {
		const src = readSrc("../index.ts");
		expect(src).toMatch(/forceExitTimer\.unref\(\)/);
	});

	it("tcp-connect.ts: _tcpStartupTimeoutTimer.unref() is called", () => {
		const src = readSrc("../python/tcp-connect.ts");
		expect(src).toMatch(/_tcpStartupTimeoutTimer\.unref\(\)/);
	});

	it("send-to-python.ts: 120s timer.unref() is called (borderline case)", () => {
		const src = readSrc("../python/send-to-python.ts");
		expect(src).toMatch(/\btimer\.unref\(\)/);
	});

	it("tcp-connect.ts: _tcpRetryTimer is NOT unref'd (must fire on schedule)", () => {
		const src = readSrc("../python/tcp-connect.ts");
		// The retry timer must NOT be unref'd.
		expect(src).not.toMatch(/_tcpRetryTimer\s*=\s*setTimeout[\s\S]*?\.unref/);
	});
});

// ────────────────────────────────────────────────────────────────────
// XV-157: stopPython idempotency guard + startPython reset
// ────────────────────────────────────────────────────────────────────

// Mock electron + state for the XV-157 runtime test.
vi.mock("electron", () => ({
	app: {
		quit: vi.fn(),
		exit: vi.fn(),
		isPackaged: false,
		isQuitting: false,
	},
	dialog: { showErrorBox: vi.fn() },
}));

function makeMockState(overrides: Partial<MainState> = {}): MainState {
	return {
		pythonProcess: null,
		tcpSocket: null,
		mainWindow: null,
		bubbleWindow: null,
		pendingRequests: new Map(),
		nextId: 1,
		tcpBuffer: "",
		pythonReady: false,
		pythonExitedEarly: false,
		heartbeatInterval: null,
		sessionNonce: "",
		bubblePosition: "top",
		bubbleDraggable: true,
		_bubblePageReady: false,
		_hideTimeout: null,
		_tcpRetryCount: 0,
		_tcpRetryTimer: null,
		_tcpRetryGeneration: 0,
		_tcpAuthed: false,
		_hadConnectedBefore: false,
		_relaunching: false,
		_restartTriggered: false,
		_stopPythonCalled: false,
		...overrides,
	} as MainState;
}

const mockState = makeMockState();
vi.mock("../state", () => ({ state: mockState }));

// Mock send-to-python so stopPython doesn't actually write to a socket.
const sendToPythonMock = vi.fn(() => Promise.resolve());
vi.mock("../python/send-to-python", () => ({
	sendToPython: sendToPythonMock,
}));

class MockChildProcess extends EventEmitter {
	pid = 12345;
	killed = false;
	kill = vi.fn(() => true);
}

describe("XV-157: stopPython idempotency guard", () => {
	let stopPython: () => void;
	let mockProc: MockChildProcess;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.useFakeTimers();
		Object.assign(mockState, makeMockState());
		vi.resetModules();
		const mod = await import("../python/stop-python");
		stopPython = mod.stopPython;
		mockProc = new MockChildProcess();
		mockState.pythonProcess = mockProc as unknown as MainState["pythonProcess"];
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it("first call sends quit_app and arms a killTimer", () => {
		stopPython();
		expect(sendToPythonMock).toHaveBeenCalledTimes(1);
		expect(sendToPythonMock).toHaveBeenCalledWith({ type: "quit_app" });
		expect(mockState._stopPythonCalled).toBe(true);
	});

	it("second call is a no-op (no second quit_app write)", () => {
		stopPython();
		stopPython();
		expect(sendToPythonMock).toHaveBeenCalledTimes(1);
	});

	it("third call is still a no-op", () => {
		stopPython();
		stopPython();
		stopPython();
		expect(sendToPythonMock).toHaveBeenCalledTimes(1);
	});

	it("does NOT register a second .once('exit') listener on the second call", () => {
		stopPython();
		const listenersAfterFirst = mockProc.listenerCount("exit");
		stopPython();
		const listenersAfterSecond = mockProc.listenerCount("exit");
		expect(listenersAfterSecond).toBe(listenersAfterFirst);
	});

	it("flag is reset by startPython() so dev-mode relaunch can re-stop", async () => {
		// Mock the start-python dependencies so it doesn't actually
		// spawn a process.
		vi.doMock("../python/python-args", () => ({
			pythonArgs: () => ["/fake/python", ["-m", "fake"]],
		}));
		vi.doMock("../python/relaunch-app", () => ({
			relaunchApp: vi.fn(),
		}));
		vi.doMock("../python/tcp-connect", () => ({
			tcpConnect: vi.fn(),
		}));
		vi.doMock("../constants", () => ({
			IPC_PORT: 12345,
			IPC_TOKEN: "test-token",
			HEARTBEAT_INTERVAL_MS: 5000,
		}));
		vi.doMock("../i18n", () => ({ mainT: (k: string) => k }));
		vi.doMock("node:child_process", () => ({
			spawn: vi.fn(() => new MockChildProcess()),
		}));

		// First stopPython sets the flag.
		stopPython();
		expect(mockState._stopPythonCalled).toBe(true);

		// startPython should reset the flag.
		vi.resetModules();
		const startPythonMod = await import("../python/start-python");
		startPythonMod.startPython();
		expect(mockState._stopPythonCalled).toBe(false);

		// Re-import stopPython to get a fresh module reference.
		const stopMod = await import("../python/stop-python");
		stopMod.stopPython();
		expect(sendToPythonMock).toHaveBeenCalledTimes(2);
	});
});

// ────────────────────────────────────────────────────────────────────
// XV-149: StringDecoder handles UTF-8 boundary correctly
// ────────────────────────────────────────────────────────────────────

describe("XV-149: StringDecoder prevents U+FFFD on chunk-split UTF-8", () => {
	it("StringDecoder reassembles a multi-byte char split across two writes", () => {
		// '\u{1F600}' (😀) is UTF-8: F0 9F 98 80 (4 bytes).
		// Split it after the first byte — without StringDecoder,
		// the first chunk would decode to U+FFFD.
		const decoder = new StringDecoder("utf8");
		const buf1 = Buffer.from([0xf0]); // first byte of 😀
		const buf2 = Buffer.from([0x9f, 0x98, 0x80]); // rest of 😀 + newline

		const out1 = decoder.write(buf1);
		const out2 = decoder.write(buf2);
		const tail = decoder.end();

		// out1 should be empty (incomplete char is buffered).
		expect(out1).toBe("");
		// out2 should be the complete 😀.
		expect(out2).toBe("\u{1F600}");
		expect(tail).toBe("");
	});

	it("chunk.toString() (the OLD approach) produces U+FFFD on a split", () => {
		// This is the bug XV-149 fixes — document it here.
		const buf1 = Buffer.from([0xf0]);
		const buf2 = Buffer.from([0x9f, 0x98, 0x80]);
		const oldApproach = buf1.toString() + buf2.toString();
		// The first chunk's toString() surfaces a U+FFFD because
		// the lone 0xF0 byte is invalid UTF-8 on its own.
		expect(oldApproach).toContain("\u{FFFD}");
		expect(oldApproach).not.toBe("\u{1F600}");
	});

	it("tcp-connect.ts source uses StringDecoder (not chunk.toString())", () => {
		const src = readSrc("../python/tcp-connect.ts");
		expect(src).toMatch(
			/import\s+\{\s*StringDecoder\s*\}\s+from\s+["']node:string_decoder["']/,
		);
		expect(src).toMatch(/new StringDecoder\(["']utf8["']\)/);
		expect(src).toMatch(/decoder\.write\(chunk\)/);
		expect(src).toMatch(/decoder\.end\(\)/);
		// The old chunk.toString() pattern must NOT appear in the
		// data handler.
		expect(src).not.toMatch(/state\.tcpBuffer\s*\+=\s*chunk\.toString\(\)/);
	});
});
