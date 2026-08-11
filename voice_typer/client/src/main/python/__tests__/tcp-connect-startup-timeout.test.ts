// @vitest-environment node
/**
 * TC-41: runtime tests for the 60s TCP startup timeout in `tcp-connect.ts`.
 *
 * The audit found the 60s startup timeout had ZERO runtime coverage —
 * the only tests were a cluster of `it.skip` source-text blocks in
 * `python-ipc-contracts.test.ts` (and stale `clearTcpStartupTimeout`
 * skips in the start/relaunch tests). These tests drive the REAL
 * `tcpConnect()` with fake timers and assert the timeout actually
 * behaves:
 *
 *   - at 60s it logs the error, shows the "Python backend failed to
 *     start" dialog, and calls `app.quit()`;
 *   - the three safety short-circuits (already connected / quitting /
 *     no pythonProcess) suppress the dialog + quit;
 *   - `clearTcpStartupTimeout()` cancels the armed timer;
 *   - repeated `tcpConnect()` calls arm exactly ONE timer (the
 *     `_tcpStartupTimeoutTimer === null` guard).
 *
 * The `node:net` Socket is mocked to a no-op (its connect callback
 * never fires) so nothing else in `tryConnect` interferes with the
 * timer under test.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../../state";

// ─── Mocks (hoisted so vi.mock factories can reference them) ──────────────

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
	};
	return {
		state,
		app: { quit: vi.fn(), isQuitting: false, isPackaged: false },
		dialog: { showErrorBox: vi.fn() },
		log: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() },
		// No-op Socket: `connect` never invokes its callback, so the
		// auth/connect path stays inert and the startup timer remains
		// armed for the duration of each test.
		Socket: vi.fn(function FakeSocket(this: {
			handlers: Record<string, Array<(...a: unknown[]) => void>>;
		}) {
			this.handlers = {};
			return {
				handlers: this.handlers,
				setNoDelay: () => {},
				connect: () => {},
				on: (ev: string, cb: (...a: unknown[]) => void) => {
					if (!this.handlers[ev]) this.handlers[ev] = [];
					this.handlers[ev].push(cb);
					return this;
				},
				destroy: () => {},
				write: () => true,
			};
		}),
	};
});

vi.mock("node:net", () => ({ default: { Socket: mocks.Socket } }));

vi.mock("electron", () => ({ app: mocks.app, dialog: mocks.dialog }));
vi.mock("../../constants", () => ({
	IPC_TOKEN: "test-token",
	HEARTBEAT_INTERVAL_MS: 5000,
	TCP_FRAME_MAX_BYTES: 4 * 1024 * 1024,
}));
vi.mock("../../logging", () => ({ log: mocks.log }));
vi.mock("../../state", () => ({ state: mocks.state }));
vi.mock("../../ipc/channels", () => ({ PythonChannels: { event: "event" } }));
vi.mock("../../windows", () => ({ createWindows: vi.fn() }));
vi.mock("../../windows/main-window", () => ({
	broadcastToMainWindow: vi.fn(),
}));
vi.mock("./handle-message", () => ({ handleMessage: vi.fn() }));
vi.mock("./send-to-python", () => ({
	sendToPython: vi.fn(() => Promise.resolve()),
	_flushPendingOutbound: vi.fn(),
	_resetPendingOutbound: vi.fn(),
}));

// 60s — mirrors TCP_STARTUP_TIMEOUT_MS in tcp-connect.ts (kept in sync by
// the test below asserting the dialog text contains "60s").
const TCP_STARTUP_TIMEOUT_MS = 60_000;

describe("tcp-connect.ts: 60s TCP startup timeout (TC-41)", () => {
	let tcpConnect: (port: number) => void;
	let clearTcpStartupTimeout: () => void;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.useFakeTimers();
		Object.assign(mocks.state, {
			pythonProcess: null,
			tcpSocket: null,
			pendingRequests: new Map(),
			heartbeatInterval: null,
			_tcpRetryCount: 0,
			_tcpRetryTimer: null,
			_tcpRetryGeneration: 0,
			_tcpAuthed: false,
			_hadConnectedBefore: false,
			_relaunching: false,
			_restartTriggered: false,
			_stopPythonCalled: false,
		});
		mocks.app.isQuitting = false;
		// Fresh module instance so the module-level
		// `_tcpStartupTimeoutTimer` does not leak between tests.
		vi.resetModules();
		const mod = await import("../tcp-connect");
		tcpConnect = mod.tcpConnect;
		clearTcpStartupTimeout = mod.clearTcpStartupTimeout;
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it("fires the error dialog + app.quit() exactly at 60s when Python never connects", () => {
		// Python spawned but never accepted the socket: proc alive,
		// tcpSocket still null, app not quitting.
		mocks.state.pythonProcess = {} as MainState["pythonProcess"];
		mocks.state.tcpSocket = null;

		tcpConnect(9876);

		// Just before the deadline nothing has fired.
		vi.advanceTimersByTime(TCP_STARTUP_TIMEOUT_MS - 1);
		expect(mocks.log.error).not.toHaveBeenCalled();
		expect(mocks.dialog.showErrorBox).not.toHaveBeenCalled();
		expect(mocks.app.quit).not.toHaveBeenCalled();

		// Cross the 60s deadline.
		vi.advanceTimersByTime(1);
		expect(mocks.log.error).toHaveBeenCalledWith(
			expect.stringContaining("failed to start within 60s"),
		);
		expect(mocks.dialog.showErrorBox).toHaveBeenCalledWith(
			"Python backend failed to start",
			expect.stringContaining("60 seconds"),
		);
		expect(mocks.app.quit).toHaveBeenCalledTimes(1);
	});

	it("safety short-circuit: already connected (tcpSocket set) suppresses the dialog + quit", () => {
		mocks.state.pythonProcess = {} as MainState["pythonProcess"];
		mocks.state.tcpSocket = {} as MainState["tcpSocket"];

		tcpConnect(9876);
		vi.advanceTimersByTime(TCP_STARTUP_TIMEOUT_MS);

		expect(mocks.dialog.showErrorBox).not.toHaveBeenCalled();
		expect(mocks.app.quit).not.toHaveBeenCalled();
	});

	it("safety short-circuit: app.isQuitting suppresses the dialog + quit", () => {
		mocks.state.pythonProcess = {} as MainState["pythonProcess"];
		mocks.state.tcpSocket = null;
		mocks.app.isQuitting = true;

		tcpConnect(9876);
		vi.advanceTimersByTime(TCP_STARTUP_TIMEOUT_MS);

		expect(mocks.dialog.showErrorBox).not.toHaveBeenCalled();
		expect(mocks.app.quit).not.toHaveBeenCalled();
	});

	it("safety short-circuit: no pythonProcess suppresses the dialog + quit", () => {
		mocks.state.pythonProcess = null;
		mocks.state.tcpSocket = null;

		tcpConnect(9876);
		vi.advanceTimersByTime(TCP_STARTUP_TIMEOUT_MS);

		expect(mocks.dialog.showErrorBox).not.toHaveBeenCalled();
		expect(mocks.app.quit).not.toHaveBeenCalled();
	});

	it("clearTcpStartupTimeout() cancels the armed timer (no dialog, no quit)", () => {
		mocks.state.pythonProcess = {} as MainState["pythonProcess"];
		mocks.state.tcpSocket = null;

		tcpConnect(9876);
		clearTcpStartupTimeout();
		vi.advanceTimersByTime(TCP_STARTUP_TIMEOUT_MS);

		expect(mocks.dialog.showErrorBox).not.toHaveBeenCalled();
		expect(mocks.app.quit).not.toHaveBeenCalled();
	});

	it("repeated tcpConnect() calls arm exactly ONE timer (single firing)", () => {
		mocks.state.pythonProcess = {} as MainState["pythonProcess"];
		mocks.state.tcpSocket = null;

		tcpConnect(9876);
		tcpConnect(9877);
		vi.advanceTimersByTime(TCP_STARTUP_TIMEOUT_MS);

		expect(mocks.dialog.showErrorBox).toHaveBeenCalledTimes(1);
		expect(mocks.app.quit).toHaveBeenCalledTimes(1);
	});

	it("the timer is armed only while a connect attempt is in flight (firing clears it)", () => {
		mocks.state.pythonProcess = {} as MainState["pythonProcess"];
		mocks.state.tcpSocket = null;

		tcpConnect(9876);
		vi.advanceTimersByTime(TCP_STARTUP_TIMEOUT_MS);
		// The callback already fired (dialog + quit above); advancing
		// further must NOT fire it a second time.
		vi.advanceTimersByTime(TCP_STARTUP_TIMEOUT_MS);
		expect(mocks.dialog.showErrorBox).toHaveBeenCalledTimes(1);
		expect(mocks.app.quit).toHaveBeenCalledTimes(1);
	});
});
