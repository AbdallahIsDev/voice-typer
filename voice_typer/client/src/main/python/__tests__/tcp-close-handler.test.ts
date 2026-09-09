// @vitest-environment node
/**
 * Typed-rejection contract for `tcp/close-handler.ts` (the socket `close`
 * teardown owner).
 *
 * When the OWNING socket closes, every pending IPC request must be
 * rejected with a typed `PythonIpcError` carrying a `PythonCallErrorCode`:
 *
 *   - mid-flight disconnect (normal close, `_relaunching` false) →
 *     `backend_not_connected` — the SAME code the `python-call` handler's
 *     pre-flight check returns when `state.tcpSocket` is null, so a
 *     disconnect mid-command shows the renderer's curated
 *     "lost connection" message instead of the generic
 *     "command failed" fallback (consistency contract).
 *   - relaunch teardown (`_relaunching` true) → `command_failed`,
 *     matching the existing typed pre-flight rejection in
 *     `send-to-python.ts` ("Application is restarting").
 *
 * A STALE socket close (an old retry-generation socket finishing teardown
 * after a newer socket connected) must NOT touch the live pending map.
 */

import type { Mock } from "vitest";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../../state";
import { PythonIpcError } from "../errors";
import { installTcpCloseHandler } from "../tcp/close-handler";

// ─── Mocks (vi.hoisted so the vi.mock factories can reference them) ──────

const mocks = vi.hoisted(() => {
	const state: MainState = {
		pythonProcess: null,
		tcpSocket: null,
		mainWindow: null,
		bubbleWindow: null,
		pendingRequests: new Map(),
		nextId: 1,
		tcpBuffer: Buffer.alloc(0),
		pythonReady: false,
		pythonExitedEarly: false,
		heartbeatInterval: null,
		sessionNonce: "",
		bubblePosition: "top",
		bubbleDraggable: true,
		_hideTimeout: null,
		_tcpRetryCount: 0,
		_tcpRetryTimer: null,
		_tcpRetryGeneration: 0,
		_tcpAuthed: false,
		_hadConnectedBefore: false,
		_relaunching: false,
		_restartTriggered: false,
		_stopPythonCalled: false,
	} as MainState;
	return {
		state,
		resetPendingOutbound: vi.fn(),
		scheduleTcpRetryAfterClose: vi.fn(),
	};
});

vi.mock("../../state", () => ({ state: mocks.state }));
vi.mock("../send-to-python", () => ({
	resetPendingOutbound: mocks.resetPendingOutbound,
}));
// close-handler.ts lives in ../tcp/, so its "./retry-scheduler" import
// resolves to src/main/python/tcp/retry-scheduler — mock THAT path.
vi.mock("../tcp/retry-scheduler", () => ({
	scheduleTcpRetryAfterClose: mocks.scheduleTcpRetryAfterClose,
}));

// ─── Helpers ──────────────────────────────────────────────────────────────

/**
 * Minimal Socket double: an EventEmitter whose `on` registration we can
 * drive by emitting "close".
 */
function makeSocket() {
	const { EventEmitter } = require("node:events");
	return new EventEmitter() as unknown as {
		on: (ev: string, cb: (...args: unknown[]) => void) => void;
		emit: (ev: string, ...args: unknown[]) => boolean;
	};
}

/** Install the close handler and return the registered "close" callback. */
function captureCloseCb(client: ReturnType<typeof makeSocket>) {
	const onSpy = vi.spyOn(client, "on");
	installTcpCloseHandler({
		client: client as never,
		port: 9999,
		retryGen: 0,
		tryConnect: vi.fn(),
	});
	const closeCall = onSpy.mock.calls.find((c) => c[0] === "close");
	if (!closeCall) throw new Error('close handler never registered "close"');
	return closeCall[1] as () => void;
}

// ─── Tests ────────────────────────────────────────────────────────────────

describe("installTcpCloseHandler: typed pending-request rejections", () => {
	// Typed as `(reason: unknown) => void` so the mock satisfies
	// `PendingRequest.reject` (the bare `vi.fn()` default Mock signature
	// is not assignable to it).
	let rejectMock: Mock<(reason: unknown) => void>;

	beforeEach(() => {
		vi.clearAllMocks();
		rejectMock = vi.fn<(reason: unknown) => void>();
		mocks.state.pendingRequests = new Map();
		mocks.state.pendingRequests.set(42, {
			resolve: vi.fn(),
			reject: rejectMock,
		});
		mocks.state._relaunching = false;
		mocks.state._tcpRetryGeneration = 0;
		mocks.state.heartbeatInterval = null;
		mocks.state.tcpBuffer = Buffer.from("partial");
	});

	it("rejects pending requests with PythonIpcError code backend_not_connected on a normal close", () => {
		const client = makeSocket();
		// Owning socket: state.tcpSocket === client.
		mocks.state.tcpSocket = client as never;
		const closeCb = captureCloseCb(client);

		closeCb();

		expect(rejectMock).toHaveBeenCalledTimes(1);
		const err = rejectMock.mock.calls[0]?.[0];
		// The mid-flight disconnect must carry the SAME code the
		// pre-flight check produces so the renderer shows the
		// curated "lost connection" message, not the generic
		// "command failed" fallback.
		expect(err).toBeInstanceOf(PythonIpcError);
		expect((err as PythonIpcError).code).toBe("backend_not_connected");
		expect((err as PythonIpcError).message).toBe("Python socket closed");
		expect(mocks.state.pendingRequests.size).toBe(0);
	});

	it("rejects pending requests with PythonIpcError code command_failed while relaunching", () => {
		const client = makeSocket();
		mocks.state.tcpSocket = client as never;
		mocks.state._relaunching = true;
		const closeCb = captureCloseCb(client);

		closeCb();

		expect(rejectMock).toHaveBeenCalledTimes(1);
		const err = rejectMock.mock.calls[0]?.[0];
		// Matches the typed pre-flight "Application is restarting"
		// rejection site in send-to-python.ts (command_failed).
		expect(err).toBeInstanceOf(PythonIpcError);
		expect((err as PythonIpcError).code).toBe("command_failed");
		expect((err as PythonIpcError).message).toBe("Application is restarting");
		// The outbound replay queue is also rejected (process is
		// about to exit — queued commands would never flush).
		expect(mocks.resetPendingOutbound).toHaveBeenCalledWith(
			"Application is restarting",
		);
	});

	it("does NOT reject the live socket's pending requests on a STALE socket close", () => {
		const stale = makeSocket();
		const live = makeSocket();
		// A newer socket owns the state; the stale one is finishing
		// its own teardown.
		mocks.state.tcpSocket = live as never;
		const closeCb = captureCloseCb(stale);

		closeCb();

		expect(rejectMock).not.toHaveBeenCalled();
		expect(mocks.state.pendingRequests.size).toBe(1);
		expect(mocks.resetPendingOutbound).not.toHaveBeenCalled();
	});

	it("still schedules a TCP retry for the current generation after the owning teardown", () => {
		const client = makeSocket();
		mocks.state.tcpSocket = client as never;
		const closeCb = captureCloseCb(client);

		closeCb();

		expect(mocks.scheduleTcpRetryAfterClose).toHaveBeenCalledWith(
			9999,
			expect.any(Function),
		);
	});
});
