// @vitest-environment node
/**
 * : behavioral tests for `src/main/ipc/python-call-handler.ts`.
 *
 * Extends the existing `generic-error-dialog.test.ts` (which only
 * covers the `command_failed` envelope) to exercise ALL 4 structured
 * error codes declared in the `PythonCallErrorCode` union:
 *
 *   - `backend_not_connected` — when `state.tcpSocket === null` and
 *     `pythonExitedEarly === false`.
 *   - `backend_exited_early` — when `state.tcpSocket === null` and
 *     `pythonExitedEarly === true`.
 *   - `command_timeout` — when `sendToPython` rejects with an error
 *     matching `/timeout/i`.
 *   - `command_failed` — when `sendToPython` rejects with any other
 *     error.
 *
 * Also verifies the renderer-visible `_error` / `_code` envelope
 * shape and the `logger.warn` diagnostic logging.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
	return {
		ipcHandle: vi.fn(),
		loggerWarn: vi.fn(),
		sendToPython: vi.fn(),
	};
});

vi.mock("electron", () => ({
	ipcMain: { handle: mocks.ipcHandle },
}));

vi.mock("../i18n", () => ({
	mainT: (k: string) => `[i18n:${k}]`,
}));

vi.mock("../logging", async () => {
	// Use the REAL dedupeRepeatedLogs (pure TS, no electron deps) so
	// the handler's `python-call rejected` collapse is exercised
	// end-to-end; only `logger` is mocked.
	const { dedupeRepeatedLogs } = await import("../logging/dedupeRepeatedLogs");
	return {
		dedupeRepeatedLogs,
		logger: {
			warn: mocks.loggerWarn,
			info: vi.fn(),
			error: vi.fn(),
			debug: vi.fn(),
		},
	};
});

vi.mock("../python", () => ({
	sendToPython: mocks.sendToPython,
}));

// Default mock state — overridden per-test via `_setMockState`.
const _mockState = {
	tcpSocket: {} as unknown,
	pythonExitedEarly: false,
};
vi.mock("../state", () => ({
	state: _mockState,
}));

describe("XS-78: python-call-handler.ts — structured {_error, _code} envelope", () => {
	let handler: (
		event: unknown,
		msg: Record<string, unknown>,
	) => Promise<unknown>;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.resetModules();
		// Restore default mock state.
		_mockState.tcpSocket = {};
		_mockState.pythonExitedEarly = false;
		const mod = await import("../ipc/python-call-handler");
		mod.registerPythonCallHandler();
		const call = mocks.ipcHandle.mock.calls.find(
			(c: unknown[]) => c[0] === "python-call",
		);
		if (!call) throw new Error("python-call handler not registered");
		handler = call[1] as typeof handler;
	});

	it("backend_not_connected: returns _code 'backend_not_connected' when socket is null and not exited-early", async () => {
		_mockState.tcpSocket = null;
		_mockState.pythonExitedEarly = false;

		const result = (await handler({}, { type: "get_config" })) as {
			_error: string;
			_code: string;
		};

		expect(result._code).toBe("backend_not_connected");
		expect(result._error).toMatch(/not connected/i);
		expect(mocks.sendToPython).not.toHaveBeenCalled();
		expect(mocks.loggerWarn).toHaveBeenCalledWith(
			"python-call rejected",
			expect.objectContaining({ code: "backend_not_connected" }),
		);
	});

	it("collapses consecutive identical backend_not_connected rejects into an (xN) summary", async () => {
		_mockState.tcpSocket = null;
		_mockState.pythonExitedEarly = false;

		// Two identical get_config rejects (the renderer retry burst).
		await handler({}, { type: "get_config" });
		await handler({}, { type: "get_config" });
		// Streak breaks with a different command.
		await handler({}, { type: "get_status" });

		// First occurrence plain, then ONE (x2) summary for the collapsed
		// get_config pair, then the get_status first occurrence.
		expect(mocks.loggerWarn).toHaveBeenCalledTimes(3);
		expect(mocks.loggerWarn.mock.calls[0]).toEqual([
			"python-call rejected",
			expect.objectContaining({
				cmd: "get_config",
				code: "backend_not_connected",
			}),
		]);
		expect(mocks.loggerWarn.mock.calls[1]).toEqual([
			"python-call rejected",
			expect.objectContaining({
				cmd: "get_config",
				code: "backend_not_connected",
			}),
			"(x2)",
		]);
		expect(mocks.loggerWarn.mock.calls[2]).toEqual([
			"python-call rejected",
			expect.objectContaining({
				cmd: "get_status",
				code: "backend_not_connected",
			}),
		]);
	});

	it("backend_exited_early: returns _code 'backend_exited_early' when socket is null AND pythonExitedEarly is true", async () => {
		_mockState.tcpSocket = null;
		_mockState.pythonExitedEarly = true;

		const result = (await handler({}, { type: "get_config" })) as {
			_error: string;
			_code: string;
		};

		expect(result._code).toBe("backend_exited_early");
		expect(result._error).toMatch(/exited during startup/i);
		expect(mocks.sendToPython).not.toHaveBeenCalled();
		expect(mocks.loggerWarn).toHaveBeenCalledWith(
			"python-call rejected",
			expect.objectContaining({ code: "backend_exited_early" }),
		);
	});

	it("command_timeout: returns _code 'command_timeout' when sendToPython rejects with a PythonIpcError timeout", async () => {
		const { PythonIpcError } = await import("../python/errors");
		mocks.sendToPython.mockRejectedValueOnce(
			new PythonIpcError(
				"command_timeout",
				"Timeout after 15s for command: get_config",
			),
		);

		const result = (await handler({}, { type: "get_config" })) as {
			_error: string;
			_code: string;
		};

		expect(result._code).toBe("command_timeout");
		//timeout messages ARE safe to forward (they don't
		// contain filesystem paths or user data), so the generic
		// message is appended with the raw errMsg for clarity.
		expect(result._error).toMatch(/timed out/i);
		expect(result._error).toContain("Timeout after 15s");
	});

	it("command_failed: returns _code 'command_failed' for non-timeout sendToPython rejections", async () => {
		mocks.sendToPython.mockRejectedValueOnce(
			new Error(
				"KeyError: 'user_utterance' at /home/user/.voice-typer/history.db:42",
			),
		);

		const result = (await handler({}, { type: "get_config" })) as {
			_error: string;
			_code: string;
		};

		expect(result._code).toBe("command_failed");
		//the raw Python traceback MUST NOT be forwarded to
		// the renderer — the generic message replaces it.
		expect(result._error).toBe("Python command failed.");
		expect(result._error).not.toContain("/home/user/.voice-typer");
		expect(result._error).not.toContain("user_utterance");
	});

	it("forwards the sender's WebContents.id to sendToPython so the per-renderer rate limit applies", async () => {
		mocks.sendToPython.mockResolvedValueOnce({ ok: true });

		const fakeEvent = {
			sender: { id: 4242 },
		};
		await handler(fakeEvent, { type: "get_config" });

		expect(mocks.sendToPython).toHaveBeenCalledWith(
			{ type: "get_config" },
			4242,
		);
	});

	it("passes senderId === null when event.sender is missing (main-process internal callers)", async () => {
		mocks.sendToPython.mockResolvedValueOnce({ ok: true });

		await handler({}, { type: "get_config" });

		expect(mocks.sendToPython).toHaveBeenCalledWith(
			{ type: "get_config" },
			null,
		);
	});

	it("resolves with the raw sendToPython result on success (no envelope wrapping)", async () => {
		const pythonResult = { transcription: "hello world" };
		mocks.sendToPython.mockResolvedValueOnce(pythonResult);

		const result = await handler({}, { type: "get_config" });

		// Success path: the result is forwarded as-is (NO {_error, _code}
		// envelope — that's only for the failure paths).
		expect(result).toBe(pythonResult);
		expect(mocks.loggerWarn).not.toHaveBeenCalled();
	});
});
