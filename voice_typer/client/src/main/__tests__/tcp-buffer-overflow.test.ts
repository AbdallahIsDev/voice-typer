// @vitest-environment node
/**
 * regression tests for TCP buffer-overflow handling in
 * `tcp-connect.ts`.
 *
 * Background
 * ----------
 * The previous implementation:
 *   - Hardcoded "4 MB" in the log message even though the actual cap
 *     comes from `TCP_FRAME_MAX_BYTES` (1 MiB in production). The
 *     message was stale after the cap was lowered to match the Python
 *     sidecar's 1 MiB outbound ceiling.
 *   - Silently destroyed the socket on overflow. Pending IPC requests
 *     were rejected later by the `close` handler with the generic
 *     "Python socket closed" error — the renderer never learned that
 *     the real cause was an oversized Python reply.
 *
 * The fix:
 *   - Computes the cap in MiB from `TCP_FRAME_MAX_BYTES` so the log
 *     message stays correct if the cap is ever tuned.
 *   - Pre-rejects every entry in `state.pendingRequests` with a
 *     structured "Python reply exceeded N MiB limit" Error BEFORE
 *     destroying the socket, so the renderer gets a debuggable error
 *     instead of a generic "socket closed". The close handler then
 *     finds an empty pending map and skips its own rejection loop.
 *
 * Test strategy
 * -------------
 * Runtime test: mock `node:net` so `new net.Socket()` returns a
 * controllable EventEmitter, plant a fake pending request in
 * `state.pendingRequests`, call `tcpConnect(port)`, then emit a `data`
 * event with a single chunk larger than `TCP_FRAME_MAX_BYTES` and no
 * newline. Assert:
 *   - The pending request's `reject` was called with an Error whose
 *     message contains "exceeded" and "MiB limit".
 *   - The pending map is empty after the overflow path runs.
 *   - `client.destroy()` was called.
 *
 * Source-text assertions: pin the structured-error contract so a
 * future refactor cannot silently regress to the generic rejection.
 *
 * ON LINUX (sandbox): runtime test via mocked net.Socket.
 * ON WINDOWS / macOS (not run here): same Buffer contract — the
 *   overflow path is platform-agnostic.
 */
import type { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../state";

// Use a SMALL cap so the test doesn't have to allocate 1 MiB. The
// production value lives in `constants.ts`; the mock here overrides
// it to 1 KiB so the overflow path is cheap to exercise.
const MOCK_TCP_FRAME_MAX_BYTES = 1024;

const { MockSocket, createdSockets } = vi.hoisted(() => {
	const { EventEmitter } = require("node:events");
	const createdSockets: unknown[] = [];

	class MockSocket extends EventEmitter {
		setNoDelay = vi.fn();
		connect = vi.fn((_port: number, _host: string, cb: () => void) => {
			cb();
			return this;
		});
		write = vi.fn();
		destroy = vi.fn();
		end = vi.fn();
		ref = vi.fn();
		unref = vi.fn();

		constructor() {
			super();
			createdSockets.push(this);
		}
	}

	return { MockSocket, createdSockets };
});

vi.mock("node:net", () => ({
	default: { Socket: MockSocket },
}));

vi.mock("electron", () => ({
	app: {
		quit: vi.fn(),
		exit: vi.fn(),
		getPath: vi.fn(() => "/tmp/vt-pvt041-test"),
		isQuitting: false,
		isPackaged: false,
	},
	dialog: { showErrorBox: vi.fn() },
}));

vi.mock("../constants", () => ({
	HEARTBEAT_INTERVAL_MS: 5_000,
	IPC_TOKEN: "test-token",
	TCP_FRAME_MAX_BYTES: MOCK_TCP_FRAME_MAX_BYTES,
}));

vi.mock("../ipc/channels", () => ({
	PythonChannels: { event: "python-event", call: "python-call" },
}));

vi.mock("../logging", () => ({
	log: {
		error: vi.fn(),
		warn: vi.fn(),
		info: vi.fn(),
		debug: vi.fn(),
	},
}));

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
} as MainState;

vi.mock("../state", () => ({ state: mockState }));

vi.mock("../windows", () => ({ createWindows: vi.fn() }));
vi.mock("../windows/main-window", () => ({
	broadcastToMainWindow: vi.fn(),
}));

const handleMessageMock = vi.fn();
vi.mock("../python/handle-message", () => ({
	handleMessage: handleMessageMock,
}));

vi.mock("../python/send-to-python", () => ({
	sendToPython: vi.fn(() => Promise.resolve()),
	_flushPendingOutbound: vi.fn(),
	_resetPendingOutbound: vi.fn(),
}));

function readTcpConnectSrc(): string {
	return fs.readFileSync(
		path.resolve(__dirname, "../python/tcp-connect.ts"),
		"utf-8",
	);
}

describe("TCP buffer-overflow rejects pending requests with structured error", () => {
	let tcpConnect: (port: number) => void;
	let rejectMock: ReturnType<typeof vi.fn>;
	let resolveMock: ReturnType<typeof vi.fn>;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.useFakeTimers();
		vi.resetModules();
		const mod = await import("../python/tcp-connect");
		tcpConnect = mod.tcpConnect;
		createdSockets.length = 0;
		mockState.tcpBuffer = Buffer.alloc(0);
		mockState.tcpSocket = null;
		mockState._tcpAuthed = false;
		mockState._tcpRetryGeneration = 0;
		mockState._tcpRetryCount = 0;
		mockState._tcpRetryTimer = null;
		mockState.heartbeatInterval = null;
		mockState._hadConnectedBefore = false;
		mockState._relaunching = false;
		mockState.pendingRequests = new Map();
		mockState.pythonProcess = {
			pid: 12345,
		} as unknown as MainState["pythonProcess"];

		rejectMock = vi.fn<(reason: unknown) => void>();
		resolveMock = vi.fn<(value: unknown) => void>();
		mockState.pendingRequests.set(42, {
			resolve: resolveMock as unknown as (value: unknown) => void,
			reject: rejectMock as unknown as (reason: unknown) => void,
		});
	});

	afterEach(() => {
		if (mockState.heartbeatInterval) {
			clearInterval(mockState.heartbeatInterval);
			mockState.heartbeatInterval = null;
		}
		vi.useRealTimers();
	});

	it("rejects pending requests with a 'reply exceeded' error when buffer overflows", () => {
		tcpConnect(9999);

		const socket = createdSockets[0] as EventEmitter;
		// Emit a single chunk larger than TCP_FRAME_MAX_BYTES with no
		// newline — this triggers the overflow branch.
		const oversizeChunk = Buffer.alloc(MOCK_TCP_FRAME_MAX_BYTES + 1, 0x41);
		socket.emit("data", oversizeChunk);

		expect(rejectMock).toHaveBeenCalledTimes(1);
		const err = rejectMock.mock.calls[0]?.[0];
		expect(err).toBeInstanceOf(Error);
		expect((err as Error).message).toMatch(/exceeded/i);
		expect((err as Error).message).toMatch(/MiB limit/i);
		// The pending map must be drained so the close handler does
		// not re-reject with the generic "Python socket closed" error.
		expect(mockState.pendingRequests.size).toBe(0);
		// The resolve must NOT have been called.
		expect(resolveMock).not.toHaveBeenCalled();
		// The socket must be destroyed.
		expect(
			(socket as unknown as { destroy: ReturnType<typeof vi.fn> }).destroy,
		).toHaveBeenCalled();
	});

	it("does NOT call handleMessage for the oversize chunk", () => {
		tcpConnect(9999);

		const socket = createdSockets[0] as EventEmitter;
		const oversizeChunk = Buffer.alloc(MOCK_TCP_FRAME_MAX_BYTES + 1, 0x41);
		socket.emit("data", oversizeChunk);

		expect(handleMessageMock).not.toHaveBeenCalled();
	});
});

describe("tcp-connect.ts source-text contract (structured overflow error)", () => {
	const src = readTcpConnectSrc();

	it("does NOT hardcode '4 MB' in the overflow log message", () => {
		// The previous stale log string hard-coded "4 MB" even though
		// the actual cap was lowered to 1 MiB. The fix computes the
		// cap from TCP_FRAME_MAX_BYTES, so no literal "4 MB" should
		// appear in the overflow branch.
		// Anchor on the overflow log line `tcpBuffer exceeded ${capMiB}`
		// and slice a small window around it.
		const logIdx = src.indexOf("tcpBuffer exceeded");
		expect(logIdx).toBeGreaterThan(-1);
		const branch = src.slice(logIdx, logIdx + 400);
		expect(branch).not.toMatch(/4\s*MB/);
	});

	it("computes the cap in MiB from TCP_FRAME_MAX_BYTES", () => {
		const logIdx = src.indexOf("tcpBuffer exceeded");
		expect(logIdx).toBeGreaterThan(-1);
		// The cap computation must appear BEFORE the log line.
		const prelude = src.slice(
			src.lastIndexOf("TCP_FRAME_MAX_BYTES)", logIdx),
			logIdx,
		);
		expect(prelude).toMatch(/TCP_FRAME_MAX_BYTES\s*\/\s*\(1024\s*\*\s*1024\)/);
	});

	it("rejects pending requests inside the overflow branch with a structured error", () => {
		// Anchor on the `overflowErr` declaration inside the overflow
		// branch, then look forward for the reject + delete loop.
		const errIdx = src.indexOf("overflowErr");
		expect(errIdx).toBeGreaterThan(-1);
		const branch = src.slice(errIdx, errIdx + 1200);
		expect(branch).toMatch(/pendingRequests/);
		expect(branch).toMatch(/\.reject\(overflowErr\)/);
		expect(branch).toMatch(/exceeded/);
		expect(branch).toMatch(/MiB limit/);
	});

	it("deletes each pending entry as it is rejected (drains the map before destroy)", () => {
		// Without `delete`, the close handler would re-reject the
		// same entries with the generic "Python socket closed"
		// error, producing confusing double-rejections in the
		// renderer's promise chain.
		const errIdx = src.indexOf("overflowErr");
		expect(errIdx).toBeGreaterThan(-1);
		const branch = src.slice(errIdx, errIdx + 1200);
		expect(branch).toMatch(/pendingRequests\.delete\(id\)/);
	});
});
