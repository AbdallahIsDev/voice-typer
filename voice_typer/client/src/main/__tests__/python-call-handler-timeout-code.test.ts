// @vitest-environment node
/**
 *  regression tests for the typed `err.code = "timeout"` contract
 * between `sendToPython` and `python-call-handler`.
 *
 * Background
 * ----------
 * Previously `python-call-handler.ts:95-112` classified timeouts via a
 * case-insensitive regex `/timeout/i` against the human-readable error
 * message string. `sendToPython` constructed the timeout error as
 *   `Timeout after ${timeoutMs / 1000}s for command: ${cmd}`.
 * If that string ever changed (localization, rewording, unit change
 * from seconds to ms), the regex silently failed and
 * `command_timeout` became `command_failed` — breaking the renderer's
 * retry logic.
 *
 *  fix:
 *   1. `sendToPython` attaches `err.code = "timeout"` to the Error
 *      object when it throws a timeout (mirroring the existing pattern
 *      at `handle-message.ts:68-72` where Python-side error codes are
 *      attached as `err.code`).
 *   2. `python-call-handler` checks `(err as { code?: string }).code
 *      === "timeout"` instead of regex-matching the message. Falls
 *      back to message regex ONLY if the code is missing (defense-
 *      in-depth).
 *
 * These tests:
 *   (a) Verify `sendToPython` attaches `err.code = "timeout"` when
 *       the timeout fires (runtime test with fake timers).
 *   (b) Verify `python-call-handler` classifies an Error with
 *       `code: "timeout"` as `command_timeout` (source-text assertion
 *       + runtime test via mocked `sendToPython`).
 *   (c) Verify the regex fallback still classifies legacy timeout-
 *       shaped Errors (defense-in-depth).
 *   (d) Verify non-timeout Errors are classified as `command_failed`.
 *
 * ON LINUX (sandbox): runtime test via vitest fake timers.
 * ON WINDOWS / macOS (not run here): same contract — the `err.code`
 *   property is platform-agnostic.
 */
import fs from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../state";

// ────────────────────────────────────────────────────────────────────
// Shared mocks. `vi.hoisted` ensures the spy references are available
// inside the hoisted `vi.mock` factories (vitest 4 pattern).
// ────────────────────────────────────────────────────────────────────

const mocks = vi.hoisted(() => ({
	// Fake socket.write — captures the outbound line so we can verify
	// the command was sent.
	socketWrite: vi.fn(),
}));

// Mock `electron` — `python-call-handler.ts` imports `ipcMain` from it.
// The handler also calls `logger.warn(...)` on the error path, which
// routes through `mainLogPath()` → `app.getPath("userData")`, so the
// mock must provide `getPath` too.
vi.mock("electron", () => ({
	app: {
		getPath: vi.fn(() => "/tmp/vt-fr31-test-userdata"),
		quit: vi.fn(),
		isPackaged: false,
		isQuitting: false,
	},
	dialog: { showErrorBox: vi.fn() },
	ipcMain: {
		handle: vi.fn(),
	},
}));

// Mock `allowed-commands` — `sendToPython` validates `msg.type` against
// this Set. We expose a known-good command so the test can pass the
// allowlist gate.
vi.mock("../allowed-commands", () => ({
	ALLOWED_COMMANDS: new Set<string>([
		"toggle_dictation",
		"download_model",
		"import_model",
		"delete_model",
		"cancel_model_download",
		"pause_model_download",
		"resume_model_download",
		"quit_app",
		"restart_app",
	]),
}));

// Mock `state` — `sendToPython` reads `state.tcpSocket`, writes to
// `state.pendingRequests`, and bumps `state.nextId`. We install a
// fake socket with a `write` method so the command line is captured.
function makeMockState(overrides: Partial<MainState> = {}): MainState {
	return {
		pythonProcess: null,
		tcpSocket: {
			write: mocks.socketWrite,
		} as unknown as MainState["tcpSocket"],
		mainWindow: null,
		bubbleWindow: null,
		pendingRequests: new Map(),
		nextId: 1,
		tcpBuffer: "",
		pythonReady: true,
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
		_tcpAuthed: true,
		_hadConnectedBefore: false,
		_relaunching: false,
		_restartTriggered: false,
		_stopPythonCalled: false,
		...overrides,
	} as MainState;
}

const mockState = makeMockState();
vi.mock("../state", () => ({
	state: mockState,
	MAX_PENDING_REQUESTS: 256,
	RATE_LIMIT_MAX_CALLS: 120,
	RATE_LIMIT_WINDOW_MS: 1000,
}));

// Mock `i18n` — `python-call-handler` doesn't currently use mainT, but
// the import resolves cleanly.
vi.mock("../i18n", () => ({ mainT: (k: string) => k }));

// ────────────────────────────────────────────────────────────────────
// (a) sendToPython attaches err.code = "timeout" on timeout.
// ────────────────────────────────────────────────────────────────────

describe("FR-31 (a): sendToPython attaches err.code = 'timeout' when the timeout fires", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		vi.useFakeTimers();
		Object.assign(mockState, makeMockState());
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it("rejects with an Error whose .code === 'timeout' when the timeout fires", async () => {
		const { sendToPython } = await import("../python/send-to-python");
		// toggle_dictation is NOT in _LONG_RUNNING_COMMANDS, so the
		// timeout is 15s (the short timeout). Advance past 15s to
		// fire the timeout.
		const promise = sendToPython({ type: "toggle_dictation" }, null);

		// Advance fake time past the 15s timeout.
		vi.advanceTimersByTime(16_000);

		let caughtErr: unknown = null;
		try {
			await promise;
		} catch (err) {
			caughtErr = err;
		}

		expect(caughtErr).toBeInstanceOf(Error);
		expect((caughtErr as Error).message).toMatch(/Timeout after 15s/);
		//the typed `code` property must be set to "timeout".
		expect((caughtErr as { code?: string }).code).toBe("timeout");
	});

	it("does NOT set code='timeout' for the not-connected rejection (only the timeout path sets it)", async () => {
		const { sendToPython } = await import("../python/send-to-python");
		// Tear down the socket — sendToPython should reject with
		// "Python backend is not connected" and NO code property.
		mockState.tcpSocket = null;

		let caughtErr: unknown = null;
		try {
			await sendToPython({ type: "toggle_dictation" }, null);
		} catch (err) {
			caughtErr = err;
		}

		expect(caughtErr).toBeInstanceOf(Error);
		expect((caughtErr as Error).message).toMatch(/not connected/i);
		// The not-connected path does NOT set code = "timeout".
		expect((caughtErr as { code?: string }).code).not.toBe("timeout");
	});
});

// ────────────────────────────────────────────────────────────────────
// (b) python-call-handler classifies err.code='timeout' as command_timeout.
// ────────────────────────────────────────────────────────────────────

describe("FR-31 (b): python-call-handler classifies err.code='timeout' as command_timeout", () => {
	const mockUserData = "/tmp/vt-fr31-test-userdata";

	beforeEach(() => {
		vi.clearAllMocks();
		Object.assign(mockState, makeMockState());
		// Create the mock userData dir so the logger.warn → appendLogLine
		// → fs.appendFileSync calls against `electron-main.log` don't
		// spew ENOENT warnings. The append is best-effort swallowed
		// anyway, but creating the dir silences the noise.
		try {
			fs.mkdirSync(mockUserData, { recursive: true });
		} catch {
			/* already exists */
		}
	});

	afterEach(() => {
		vi.restoreAllMocks();
		// Clean up the mock userData dir between tests so each test
		// starts fresh.
		try {
			fs.rmSync(mockUserData, { recursive: true, force: true });
		} catch {
			/* ignore */
		}
	});

	/**
	 * Helper: build a fake `event` arg for the `python-call` IPC
	 * handler. The handler reads `event.sender.id` for the per-
	 * renderer rate limit.
	 */
	function makeFakeEvent(senderId: number | null): unknown {
		if (senderId === null) return undefined;
		return { sender: { id: senderId } };
	}

	/**
	 * Helper: invoke the `python-call` handler with a mocked
	 * `sendToPython` that throws the given Error.
	 *
	 * `registerPythonCallHandler` calls `ipcMain.handle("python-call",
	 * handler)`. We capture the handler via the ipcMain.handle mock,
	 * then invoke it directly.
	 */
	async function invokeHandler(
		sendToPythonImpl: (
			msg: unknown,
			senderId: number | null,
		) => Promise<unknown>,
		msg: Record<string, unknown>,
	): Promise<unknown> {
		// Mock send-to-python with the provided implementation.
		vi.doMock("../python", () => ({
			sendToPython: sendToPythonImpl,
		}));

		// Re-import python-call-handler so it picks up the mocked
		// send-to-python.
		vi.resetModules();
		const { registerPythonCallHandler } = await import(
			"../ipc/python-call-handler"
		);

		// The electron mock's ipcMain.handle captures the handler.
		const { ipcMain } = await import("electron");
		const handleSpy = ipcMain.handle as unknown as {
			mock: { calls: unknown[][] };
		};
		handleSpy.mock.calls.length = 0;
		registerPythonCallHandler();

		expect(handleSpy.mock.calls.length).toBeGreaterThanOrEqual(1);
		const lastCall = handleSpy.mock.calls[handleSpy.mock.calls.length - 1];
		// ipcMain.handle("python-call", handler) — handler is 2nd arg.
		const handler = lastCall[1] as (
			event: unknown,
			msg: Record<string, unknown>,
		) => Promise<unknown>;

		// Need a fake sender for the rate limit. Use senderId=1.
		return handler(makeFakeEvent(1), msg);
	}

	it("returns _code='command_timeout' when sendToPython rejects with err.code='timeout'", async () => {
		const timeoutErr = new Error(
			"Timeout after 15s for command: toggle_dictation",
		);
		(timeoutErr as Error & { code: string }).code = "timeout";

		const result = await invokeHandler(
			async () => {
				throw timeoutErr;
			},
			{ type: "toggle_dictation" },
		);

		expect(result).toEqual({
			_error: expect.stringContaining("Python command timed out"),
			_code: "command_timeout",
		});
	});

	it("returns _code='command_failed' when sendToPython rejects with a non-timeout error (no code)", async () => {
		const otherErr = new Error("Some random Python failure");

		const result = await invokeHandler(
			async () => {
				throw otherErr;
			},
			{ type: "toggle_dictation" },
		);

		expect(result).toEqual({
			_error: "Python command failed.",
			_code: "command_failed",
		});
	});

	it("returns _code='command_failed' when sendToPython rejects with err.code='disallowed' (non-timeout code)", async () => {
		//a non-"timeout" code must NOT be misclassified as a
		// timeout. The check is strict equality on the string "timeout".
		const otherErr = new Error("Disallowed IPC command: foo");
		(otherErr as Error & { code: string }).code = "disallowed";

		const result = await invokeHandler(
			async () => {
				throw otherErr;
			},
			{ type: "toggle_dictation" },
		);

		expect(result).toEqual({
			_error: "Python command failed.",
			_code: "command_failed",
		});
	});

	it("regex fallback still classifies a legacy timeout-shaped Error (defense-in-depth)", async () => {
		//the regex fallback exists for callers that throw
		// timeout-shaped Errors without setting `code`. The fallback
		// must still classify them as `command_timeout`.
		const legacyErr = new Error("Request Timeout");

		const result = await invokeHandler(
			async () => {
				throw legacyErr;
			},
			{ type: "toggle_dictation" },
		);

		expect(result).toEqual({
			_error: expect.stringContaining("Python command timed out"),
			_code: "command_timeout",
		});
	});
});

// ────────────────────────────────────────────────────────────────────
// (c) Source-text assertions pinning the contract.
// ────────────────────────────────────────────────────────────────────

describe("FR-31 (c): source-text contract for the typed err.code", () => {
	it("send-to-python.ts source attaches err.code = 'timeout' on the timeout path", async () => {
		const fs = await import("node:fs");
		const path = await import("node:path");
		const src = fs.readFileSync(
			path.resolve(__dirname, "../python/send-to-python.ts"),
			"utf-8",
		);
		// The timeout-reject branch must construct an Error AND set
		// its .code to "timeout".
		expect(src).toMatch(/\.code\s*=\s*["']timeout["']/);
	});

	it("python-call-handler.ts source checks err.code === 'timeout' (not just regex)", async () => {
		const fs = await import("node:fs");
		const path = await import("node:path");
		const src = fs.readFileSync(
			path.resolve(__dirname, "../ipc/python-call-handler.ts"),
			"utf-8",
		);
		// The classification must include a strict-equality check on
		// the typed `code` property.
		expect(src).toMatch(/errCode\s*===\s*["']timeout["']/);
		// The regex fallback must still be present (defense-in-depth).
		expect(src).toMatch(/\/timeout\/i\.test\(/);
	});
});
