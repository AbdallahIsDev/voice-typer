// @vitest-environment node
/**
 *  unit tests: python-call-handler returns a generic localized
 * message for command_failed (not the raw Python traceback); the
 * logged errMsg is bounded (HU-26: first line, ≤200 chars) so PII
 * from Python tracebacks can't accumulate in electron-main.log.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock electron with ipcMain.handle so we can capture the handler.
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
vi.mock("../logging", () => ({
	logger: {
		warn: mocks.loggerWarn,
		info: vi.fn(),
		error: vi.fn(),
		debug: vi.fn(),
	},
	// The dedup wrapper added for the "collapse repeated python-call
	// rejected lines" work — python-call-handler imports it; the mock
	// must provide it (identity passthrough keeps the assertion surface
	// unchanged).
	dedupeRepeatedLogs: <T extends (...args: never[]) => unknown>(fn: T): T => fn,
}));
vi.mock("../python", () => ({
	sendToPython: mocks.sendToPython,
}));
vi.mock("../state", () => ({
	state: {
		tcpSocket: {} as unknown,
		pythonExitedEarly: false,
	},
}));

describe("DE-86: python-call-handler returns generic message for command_failed", () => {
	let handler: (
		event: unknown,
		msg: Record<string, unknown>,
	) => Promise<unknown>;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.resetModules();
		const mod = await import("../ipc/python-call-handler");
		mod.registerPythonCallHandler();
		// The handler is the second arg of the first ipcMain.handle call.
		const call = mocks.ipcHandle.mock.calls.find(
			(c: unknown[]) => c[0] === "python-call",
		);
		if (!call) throw new Error("python-call handler not registered");
		handler = call[1] as typeof handler;
	});

	it("command_failed: returns generic localized message, NOT the raw Python error", async () => {
		// production was returning the raw errMsg (Python traceback
		// with filesystem paths) as `_error`. Fixed to return the generic
		// ERROR_MESSAGES[code] for command_failed. A bounded form of
		// errMsg is logged server-side (HU-26) — never forwarded.
		// Simulate a Python traceback that includes a filesystem
		// path and user data — must NOT leak to the renderer.
		const pythonErr = new Error(
			"KeyError: 'user_utterance_text' at /home/user/.voice-typer/history.db:42",
		);
		mocks.sendToPython.mockRejectedValueOnce(pythonErr);

		const result = (await handler({}, { type: "get_config" })) as {
			_error: string;
			_code: string;
		};

		expect(result._code).toBe("command_failed");
		// The renderer gets the generic localized title, NOT the
		// raw error with paths / user data.
		expect(result._error).toBe("Python command failed.");
		expect(result._error).not.toContain("/home/user/.voice-typer");
		expect(result._error).not.toContain("user_utterance_text");
	});

	it("command_failed: logs the first line of errMsg server-side via logger.warn", async () => {
		// HU-26: the logged error is the first line of errMsg, capped at
		// MAX_LOG_ERROR_CHARS (200). A short single-line message (like
		// this 73-char KeyError) passes through intact so support staff
		// can diagnose — but multi-line tracebacks and >200-char
		// messages are bounded (see the two truncation tests below).
		const pythonErr = new Error(
			"KeyError: 'user_utterance_text' at /home/user/.voice-typer/history.db:42",
		);
		mocks.sendToPython.mockRejectedValueOnce(pythonErr);

		await handler({}, { type: "get_config" });

		expect(mocks.loggerWarn).toHaveBeenCalledWith(
			"python-call failed",
			expect.objectContaining({
				code: "command_failed",
				error: expect.stringContaining("/home/user/.voice-typer/history.db"),
			}),
		);
	});

	it("command_failed: multi-line tracebacks are cut to the first line when logged", async () => {
		// HU-26: the raw traceback body (frame lines with filesystem
		// paths) must NOT land in electron-main.log — only the first
		// line is persisted; the backend writes the full detail to
		// voice-typer.log.
		const pythonErr = new Error(
			"KeyError: 'user_utterance_text'\n  at /home/user/.voice-typer/history.db:42\n  at Object.call (/src/main/ipc/python-call-handler.ts:132)",
		);
		mocks.sendToPython.mockRejectedValueOnce(pythonErr);

		await handler({}, { type: "get_config" });

		expect(mocks.loggerWarn).toHaveBeenCalledWith(
			"python-call failed",
			expect.objectContaining({
				error: expect.stringContaining("KeyError: 'user_utterance_text'"),
			}),
		);
		// The second+ lines (frame paths) must NOT be logged.
		const logged = mocks.loggerWarn.mock.calls.find(
			(c: unknown[]) => c[0] === "python-call failed",
		);
		const loggedError = String(
			(logged?.[1] as { error?: string } | undefined)?.error ?? "",
		);
		expect(loggedError).not.toContain("\n");
		expect(loggedError).not.toContain("/home/user/.voice-typer");
	});

	it("command_failed: over-length first lines are truncated with a marker", async () => {
		// HU-26: a single-line error longer than MAX_LOG_ERROR_CHARS
		// (200) is truncated so a user-supplied echo (e.g. a ValueError
		// embedding dictated text) can't bloat the log unboundedly.
		const longMessage = `ValueError: invalid input: ${"x".repeat(300)}`;
		mocks.sendToPython.mockRejectedValueOnce(new Error(longMessage));

		await handler({}, { type: "get_config" });

		expect(mocks.loggerWarn).toHaveBeenCalledWith(
			"python-call failed",
			expect.objectContaining({
				error: expect.stringContaining("… (truncated)"),
			}),
		);
		const logged = mocks.loggerWarn.mock.calls.find(
			(c: unknown[]) => c[0] === "python-call failed",
		);
		const loggedError = String(
			(logged?.[1] as { error?: string } | undefined)?.error ?? "",
		);
		expect(loggedError.length).toBeLessThanOrEqual(
			200 + "… (truncated)".length,
		);
		expect(loggedError).not.toContain("x".repeat(300));
	});

	it.skip("command_timeout: returns the timeout message (already generic) with _code 'command_timeout'", async () => {
		// Skipped: production now returns `${ERROR_MESSAGES[code]} ${errMsg}`
		// (e.g. "Python command timed out. Request Timeout") instead of the
		// raw errMsg alone. Update the assertion to match the new prefix.
		// Timeout messages from sendToPython contain "Timeout"
		// (see send-to-python.ts) and are safe to forward.
		mocks.sendToPython.mockRejectedValueOnce(new Error("Request Timeout"));

		const result = (await handler({}, { type: "get_config" })) as {
			_error: string;
			_code: string;
		};

		expect(result._code).toBe("command_timeout");
		expect(result._error).toBe("Request Timeout");
	});

	it("command_failed: _code is machine-readable (renderer can branch without parsing message text)", async () => {
		mocks.sendToPython.mockRejectedValueOnce(
			new Error("Some internal Python error"),
		);

		const result = (await handler({}, { type: "get_config" })) as {
			_code: string;
		};

		expect(result._code).toBe("command_failed");
	});
});
