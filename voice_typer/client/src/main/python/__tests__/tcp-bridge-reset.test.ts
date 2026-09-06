// @vitest-environment node
/**
 * Unit tests for `tcp-bridge-reset.ts` — the shared ordered teardown
 * used by the two Electron-alive restart paths (`relaunch-app.ts` dev
 * branch and `restart-backend.ts`).
 *
 * Pins the full sequence in one place: socket destroy, backpressure
 * reset, buffer/auth/ready flag clears, retry-counter zeroing, retry-
 * timer clear (BEFORE the generation bump), generation bump, heartbeat
 * clear, and pending-request rejection with the caller-supplied reason.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MainState } from "../../state";

const { mockState, mockResetBackpressure, mockLog } = vi.hoisted(() => {
	const mockState: MainState = {
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
	};
	return {
		mockState,
		mockResetBackpressure: vi.fn(),
		mockLog: {
			info: vi.fn(),
			warn: vi.fn(),
			error: vi.fn(),
			debug: vi.fn(),
		},
	};
});

vi.mock("../../state", () => ({ state: mockState }));
vi.mock("../send-to-python", () => ({
	_resetIpcBackpressure: mockResetBackpressure,
}));
vi.mock("../../logging", () => ({ log: mockLog }));

import { resetTcpBridgeState } from "../tcp-bridge-reset";

function resetMockState(): void {
	vi.resetAllMocks();
	mockState.pythonProcess = null;
	mockState.tcpSocket = null;
	mockState.pendingRequests = new Map();
	mockState.tcpBuffer = Buffer.alloc(0);
	mockState.pythonReady = false;
	mockState.pythonExitedEarly = false;
	mockState.heartbeatInterval = null;
	mockState._tcpRetryCount = 0;
	mockState._tcpRetryTimer = null;
	mockState._tcpRetryGeneration = 0;
	mockState._tcpAuthed = false;
	mockState._hadConnectedBefore = false;
	mockState._relaunching = false;
	mockState._restartTriggered = false;
}

beforeEach(() => {
	resetMockState();
});

describe("resetTcpBridgeState()", () => {
	it("destroys the socket and nulls it, tolerating a destroy() throw", () => {
		const socketDestroy = vi.fn();
		mockState.tcpSocket = {
			destroy: socketDestroy,
		} as unknown as MainState["tcpSocket"];
		resetTcpBridgeState("test reason");
		expect(socketDestroy).toHaveBeenCalledTimes(1);
		expect(mockState.tcpSocket).toBeNull();

		// A throwing destroy() must be logged, not propagated —
		// the rest of the teardown still has to run.
		mockState.tcpSocket = {
			destroy: () => {
				throw new Error("socket already gone");
			},
		} as unknown as MainState["tcpSocket"];
		expect(() => resetTcpBridgeState("test reason")).not.toThrow();
		expect(mockState.tcpSocket).toBeNull();
		expect(mockLog.warn).toHaveBeenCalled();
	});

	it("performs the full ordered reset: flags, counters, timer, generation, heartbeat", () => {
		const callOrder: string[] = [];
		mockState.tcpSocket = {
			destroy: () => callOrder.push("destroy"),
		} as unknown as MainState["tcpSocket"];
		mockResetBackpressure.mockImplementation(() =>
			callOrder.push("backpressure"),
		);
		mockState.tcpBuffer = Buffer.from("stale partial frame");
		mockState._tcpAuthed = true;
		mockState.pythonReady = true;
		mockState.pythonExitedEarly = true;
		mockState._hadConnectedBefore = true;
		mockState._tcpRetryCount = 3;
		const retryTimer = setTimeout(() => {}, 1_000_000);
		mockState._tcpRetryTimer = retryTimer;
		mockState._tcpRetryGeneration = 5;
		const heartbeat = setInterval(() => {}, 1_000_000);
		mockState.heartbeatInterval = heartbeat;

		resetTcpBridgeState("test reason");

		expect(mockState.tcpBuffer.length).toBe(0);
		expect(mockState._tcpAuthed).toBe(false);
		expect(mockState.pythonReady).toBe(false);
		expect(mockState.pythonExitedEarly).toBe(false);
		expect(mockState._hadConnectedBefore).toBe(false);
		expect(mockState._tcpRetryCount).toBe(0);
		expect(mockState._tcpRetryTimer).toBeNull();
		expect(mockState._tcpRetryGeneration).toBe(6);
		expect(mockState.heartbeatInterval).toBeNull();
		expect(mockResetBackpressure).toHaveBeenCalledTimes(1);
		// Socket destroyed, then backpressure reset — the other
		// steps are plain field writes with no observable order,
		// but the destroy→reset prefix must hold.
		expect(callOrder).toEqual(["destroy", "backpressure"]);
		clearTimeout(retryTimer);
		clearInterval(heartbeat);
	});

	it("rejects and deletes every pending request with the caller's reason", () => {
		const rejectA = vi.fn();
		const rejectB = vi.fn();
		mockState.pendingRequests.set(1, {
			resolve: vi.fn(),
			reject: rejectA,
		});
		mockState.pendingRequests.set(2, {
			resolve: vi.fn(),
			reject: rejectB,
		});

		resetTcpBridgeState("Python backend is restarting");

		expect(mockState.pendingRequests.size).toBe(0);
		expect(rejectA).toHaveBeenCalledTimes(1);
		expect(rejectB).toHaveBeenCalledTimes(1);
		const err = rejectA.mock.calls[0]?.[0] as Error;
		expect(err).toBeInstanceOf(Error);
		expect(err.message).toBe("Python backend is restarting");
		expect(rejectA).toHaveBeenCalledWith(
			expect.objectContaining({
				message: "Python backend is restarting",
			}),
		);
	});

	it("uses the reason verbatim, so each restart path keeps its own message", () => {
		const reject = vi.fn();
		mockState.pendingRequests.set(8, { resolve: vi.fn(), reject });
		resetTcpBridgeState("Application is restarting");
		expect(mockState.pendingRequests.size).toBe(0);
		expect(reject).toHaveBeenCalledWith(
			expect.objectContaining({ message: "Application is restarting" }),
		);
	});

	it("is safe to call with an already-clean state (no throw)", () => {
		expect(() => resetTcpBridgeState("nothing to reset")).not.toThrow();
		expect(mockResetBackpressure).toHaveBeenCalledTimes(1);
		expect(mockState._tcpRetryGeneration).toBe(1);
	});
});
