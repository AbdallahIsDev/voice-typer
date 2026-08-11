// @vitest-environment node
/**
 * : runtime reject tests for `src/main/ipc/python-call-handler.ts`.
 *
 * The preload's `python.call` is now typed
 * `{ type: string; data?: Record<string, unknown> }` (matching the
 * `PythonBridge` contract in `types/ipc/bridge.ts`), so a well-typed
 * renderer cannot send a request without a string `type`. But the
 * `ipcMain.handle` listener receives `msg` as `any` (Electron's IPC
 * boundary erases TS types), and a buggy/mock caller, a tampered
 * devtools `invoke` from the console, or a future preload refactor
 * could still ship a `{}` / `{ type: 42 }` / `null` payload.
 *
 * The handler now rejects these malformed requests early with a
 * structured `{ _error, _code: "command_failed" }` envelope instead
 * of coercing the value to `"<unknown>"` and forwarding it to the
 * Python backend (which would then return `unknown_command` after a
 * full TCP round-trip).
 *
 * These tests pin the runtime reject for each malformed shape:
 *   - `null`
 *   - `undefined`
 *   - `{}` (no `type` field)
 *   - `{ type: 42 }` (non-string `type`)
 *   - `{ type: null }` (null `type`)
 *   - `"get_config"` (string, not an object — defensive)
 *   - `42` (number — defensive)
 *
 * And verify:
 *   - `sendToPython` is NEVER called for malformed requests (no
 *     backend round-trip wasted).
 *   - The structured envelope's `_code` is `"command_failed"`.
 *   - The diagnostic `logger.warn` is emitted with the reason.
 *   - A well-formed request still works (regression guard).
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

const _mockState = {
	tcpSocket: {} as unknown,
	pythonExitedEarly: false,
};
vi.mock("../state", () => ({
	state: _mockState,
}));

describe("UE-39: python-call-handler rejects malformed requests at runtime", () => {
	let handler: (event: unknown, msg: unknown) => Promise<unknown>;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.resetModules();
		// Default mock state: backend connected, not exited early.
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

	it("rejects `null` with _code 'command_failed' and does NOT call sendToPython", async () => {
		const result = (await handler({}, null)) as {
			_error: string;
			_code: string;
		};
		expect(result._code).toBe("command_failed");
		expect(result._error).toMatch(/missing or non-string 'type' field/);
		expect(mocks.sendToPython).not.toHaveBeenCalled();
		expect(mocks.loggerWarn).toHaveBeenCalledWith(
			"python-call rejected",
			expect.objectContaining({
				cmd: "<invalid>",
				code: "command_failed",
				reason: "missing or non-string 'type' field",
			}),
		);
	});

	it("rejects `undefined` with _code 'command_failed'", async () => {
		const result = (await handler({}, undefined)) as {
			_error: string;
			_code: string;
		};
		expect(result._code).toBe("command_failed");
		expect(mocks.sendToPython).not.toHaveBeenCalled();
	});

	it("rejects `{}` (missing `type` field) with _code 'command_failed'", async () => {
		const result = (await handler({}, {})) as {
			_error: string;
			_code: string;
		};
		expect(result._code).toBe("command_failed");
		expect(mocks.sendToPython).not.toHaveBeenCalled();
	});

	it("rejects `{ type: 42 }` (non-string `type`) with _code 'command_failed'", async () => {
		const result = (await handler({}, { type: 42 })) as {
			_error: string;
			_code: string;
		};
		expect(result._code).toBe("command_failed");
		expect(mocks.sendToPython).not.toHaveBeenCalled();
	});

	it("rejects `{ type: null }` (null `type`) with _code 'command_failed'", async () => {
		const result = (await handler({}, { type: null })) as {
			_error: string;
			_code: string;
		};
		expect(result._code).toBe("command_failed");
		expect(mocks.sendToPython).not.toHaveBeenCalled();
	});

	it("rejects a bare string payload (defensive — handler expects an object)", async () => {
		const result = (await handler({}, "get_config")) as {
			_error: string;
			_code: string;
		};
		expect(result._code).toBe("command_failed");
		expect(mocks.sendToPython).not.toHaveBeenCalled();
	});

	it("rejects a number payload (defensive)", async () => {
		const result = (await handler({}, 42)) as {
			_error: string;
			_code: string;
		};
		expect(result._code).toBe("command_failed");
		expect(mocks.sendToPython).not.toHaveBeenCalled();
	});

	it("rejects an array payload (defensive — arrays are typeof 'object' but have no string `type`)", async () => {
		const result = (await handler({}, ["get_config"])) as {
			_error: string;
			_code: string;
		};
		expect(result._code).toBe("command_failed");
		expect(mocks.sendToPython).not.toHaveBeenCalled();
	});

	it("still accepts a well-formed `{ type: 'get_config' }` request (regression guard)", async () => {
		mocks.sendToPython.mockResolvedValueOnce({ ok: true });
		const result = await handler({}, { type: "get_config" });
		expect(mocks.sendToPython).toHaveBeenCalledWith(
			{ type: "get_config" },
			null,
		);
		expect(result).toEqual({ ok: true });
	});

	it("still accepts a well-formed `{ type, data }` request (regression guard)", async () => {
		mocks.sendToPython.mockResolvedValueOnce({ ok: true });
		await handler({}, { type: "set_config", data: { theme: "dark" } });
		expect(mocks.sendToPython).toHaveBeenCalledWith(
			{ type: "set_config", data: { theme: "dark" } },
			null,
		);
	});

	it("forwards the sender's WebContents.id for well-formed requests (regression guard)", async () => {
		mocks.sendToPython.mockResolvedValueOnce({ ok: true });
		const fakeEvent = { sender: { id: 99 } };
		await handler(fakeEvent, { type: "get_config" });
		expect(mocks.sendToPython).toHaveBeenCalledWith({ type: "get_config" }, 99);
	});
});
