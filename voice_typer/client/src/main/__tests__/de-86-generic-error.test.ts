// @vitest-environment node
/**
 * DE-86 unit tests: python-call-handler returns a generic localized
 * message for command_failed (not the raw Python traceback); the full
 * errMsg is logged server-side via logger.warn.
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
		expect(result._error).toBe("[i18n:dialog.criticalError.title]");
		expect(result._error).not.toContain("/home/user/.voice-typer");
		expect(result._error).not.toContain("user_utterance_text");
	});

	it("command_failed: logs the full errMsg server-side via logger.warn", async () => {
		const pythonErr = new Error(
			"KeyError: 'user_utterance_text' at /home/user/.voice-typer/history.db:42",
		);
		mocks.sendToPython.mockRejectedValueOnce(pythonErr);

		await handler({}, { type: "get_config" });

		// The full error (with paths / user data) MUST be logged
		// server-side so support staff can diagnose — just not
		// forwarded to the renderer.
		expect(mocks.loggerWarn).toHaveBeenCalledWith(
			"python-call failed",
			expect.objectContaining({
				code: "command_failed",
				error: expect.stringContaining("/home/user/.voice-typer/history.db"),
			}),
		);
	});

	it("command_timeout: returns the timeout message (already generic) with _code 'command_timeout'", async () => {
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
