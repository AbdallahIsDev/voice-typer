// @vitest-environment node
/**
 * Unit tests for `restart-backend.ts` — the backend-only restart that the
 * renderer's "Lost connection" Retry button escalates to after a plain
 * reconnect probe fails (`useConnection` `handleRetryConnection`).
 *
 * Covers the result contract:
 *   - `{ ok: false, reason: "adopted" }` — the backend spawned us
 *     (VT_PYTHON_PORT + VT_IPC_TOKEN set); killing it kills the app.
 *   - `{ ok: false, reason: "relaunching" }` — full app relaunch in
 *     flight, or startPython() threw during the restart.
 *   - `{ ok: true }` — kill + full bridge-state reset + fresh spawn
 *     (mirrors the relaunch-app.ts dev branch).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MainState } from "../../state";
import { restartBackend } from "../restart-backend";

// ─── Mocks ────────────────────────────────────────────────────────────────
// vi.mock factories are hoisted above the static imports, so every
// variable they reference must come from vi.hoisted (otherwise the
// factory sees the const in its TDZ when the mocked module is first
// imported).
const {
	mockStartPython,
	mockKillPython,
	mockClearTcpStartupTimeout,
	mockResetBackpressure,
	mockLog,
	mockState,
} = vi.hoisted(() => {
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
		mockStartPython: vi.fn(),
		mockKillPython: vi.fn(),
		mockClearTcpStartupTimeout: vi.fn(),
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
vi.mock("../start-python", () => ({ startPython: mockStartPython }));
vi.mock("../kill-python", () => ({
	killPythonProcessWithSigkillFallback: mockKillPython,
}));
vi.mock("../tcp-connect", () => ({
	tcpConnect: vi.fn(),
	clearTcpStartupTimeout: mockClearTcpStartupTimeout,
}));
vi.mock("../send-to-python", () => ({
	_resetIpcBackpressure: mockResetBackpressure,
}));
vi.mock("../../logging", () => ({ log: mockLog }));

// The module-level instance is shared across tests (vitest mock
// factories are memoized), so each test must restore the state fields
// it mutates.
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
	delete process.env.VT_PYTHON_PORT;
	delete process.env.VT_IPC_TOKEN;
}

beforeEach(() => {
	resetMockState();
});

describe("restartBackend()", () => {
	it("refuses when the backend spawned us (adopted mode, VT_PYTHON_PORT set)", () => {
		process.env.VT_PYTHON_PORT = "7000";
		process.env.VT_IPC_TOKEN = "tok";

		const result = restartBackend();

		expect(result).toEqual({ ok: false, reason: "adopted" });
		expect(mockKillPython).not.toHaveBeenCalled();
		expect(mockStartPython).not.toHaveBeenCalled();
	});

	it("refuses while a full app relaunch is in flight", () => {
		mockState._relaunching = true;

		const result = restartBackend();

		expect(result).toEqual({ ok: false, reason: "relaunching" });
		expect(mockKillPython).not.toHaveBeenCalled();
		expect(mockStartPython).not.toHaveBeenCalled();
	});

	it("kills the old backend, resets bridge state, rejects pending IPC, and respawns", () => {
		// Pre-populate the state with the exact conditions a real
		// "dead/hung backend" leaves behind.
		const socketDestroy = vi.fn();
		mockState.tcpSocket = {
			destroy: socketDestroy,
		} as unknown as MainState["tcpSocket"];
		mockState.tcpBuffer = Buffer.from("stale partial frame");
		mockState._tcpAuthed = true;
		mockState.pythonReady = true;
		mockState.pythonExitedEarly = true;
		mockState._hadConnectedBefore = true;
		mockState._tcpRetryCount = 3;
		mockState._tcpRetryTimer = setTimeout(() => {}, 1_000_000);
		mockState._tcpRetryGeneration = 5;
		mockState.heartbeatInterval = setInterval(() => {}, 1_000_000);
		const rejectPending = vi.fn();
		mockState.pendingRequests.set(42, {
			resolve: vi.fn(),
			reject: rejectPending,
		});

		const result = restartBackend();

		expect(result).toEqual({ ok: true });
		// Clear TCP startup deadline first (ordering), then kill.
		expect(mockClearTcpStartupTimeout).toHaveBeenCalledBefore(mockKillPython);
		expect(mockKillPython).toHaveBeenCalledWith("dev");
		// Socket torn down.
		expect(socketDestroy).toHaveBeenCalled();
		expect(mockState.tcpSocket).toBeNull();
		// Bridge flags fully reset.
		expect(mockState.tcpBuffer.length).toBe(0);
		expect(mockState._tcpAuthed).toBe(false);
		expect(mockState.pythonReady).toBe(false);
		expect(mockState.pythonExitedEarly).toBe(false);
		expect(mockState._hadConnectedBefore).toBe(false);
		expect(mockState._tcpRetryCount).toBe(0);
		expect(mockState._tcpRetryTimer).toBeNull();
		expect(mockState._tcpRetryGeneration).toBe(6);
		expect(mockState.heartbeatInterval).toBeNull();
		// Per-renderer rate-limit map cleared.
		expect(mockResetBackpressure).toHaveBeenCalled();
		// Pending request rejected with the restart error, map emptied.
		expect(rejectPending).toHaveBeenCalledWith(
			expect.objectContaining({ message: "Python backend is restarting" }),
		);
		expect(mockState.pendingRequests.size).toBe(0);
		// Fresh spawn requested.
		expect(mockStartPython).toHaveBeenCalledTimes(1);
	});

	it("returns the relaunching-failure envelope when startPython() throws (cleanup still runs)", () => {
		mockState.tcpBuffer = Buffer.from("stale");
		mockState._tcpAuthed = true;
		mockState._tcpRetryGeneration = 2;
		mockStartPython.mockImplementationOnce(() => {
			throw new Error("spawn boom");
		});

		const result = restartBackend();

		expect(result).toEqual({ ok: false, reason: "relaunching" });
		// Cleanup ran even though spawn blew up.
		expect(mockState.tcpBuffer.length).toBe(0);
		expect(mockState._tcpAuthed).toBe(false);
		expect(mockState._tcpRetryGeneration).toBe(3);
		expect(mockKillPython).toHaveBeenCalledWith("dev");
		expect(mockLog.error).toHaveBeenCalled();
	});
});
