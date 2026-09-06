// @vitest-environment node
/**
 * Regression tests for TCP UTF-8 reassembly in `tcp-connect.ts`.
 *
 * Background
 * ----------
 * The previous implementation stored `state.tcpBuffer` as a `string` and
 * appended each chunk via `state.tcpBuffer += chunk.toString()`. This
 * decoded each chunk INDEPENDENTLY, so a multi-byte UTF-8 character split
 * across two TCP chunks (e.g. F0 9F 98 80 split after byte one) surfaced
 * U+FFFD in the reassembled line — corrupting transcription_final events
 * containing non-ASCII text (CJK, emoji, accented Latin, etc.).
 *
 * The fix keeps `state.tcpBuffer` as a `Buffer`, concatenates raw bytes via
 * `Buffer.concat`, scans for the newline byte (0x0a) via `Buffer.indexOf`,
 * and decodes each complete line exactly once via `subarray(0, idx).toString("utf8")`.
 *
 * Test strategy
 * -------------
 * Runtime test: mock `node:net` so `new net.Socket()` returns a controllable
 * EventEmitter, call `tcpConnect(port)`, then emit `data` events with a
 * multi-byte UTF-8 char split across two chunks. Assert the `handleMessage`
 * mock receives the CORRECT message (no U+FFFD).
 *
 * Source-text assertions: pin the Buffer-based reassembly contract so a
 * future refactor cannot silently regress to the per-chunk toString() approach.
 *
 * ON LINUX (sandbox): runtime test via mocked net.Socket.
 * ON WINDOWS / macOS: same Buffer contract — the reassembly is byte-level
 *   and platform-agnostic.
 */
import type { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../state";

// ────────────────────────────────────────────────────────────────────
// vi.hoisted: define MockSocket + createdSockets BEFORE vi.mock() calls.
// vitest hoists vi.mock() to the top of the file, so any variables
// referenced inside the factory must also be hoisted.
// ────────────────────────────────────────────────────────────────────

const { MockSocket, createdSockets } = vi.hoisted(() => {
	// Use require inside hoisted block — vi.hoisted() runs before ESM
	// imports are resolved, so we cannot reference the top-level
	// `import { EventEmitter }` here. require("node:events") works
	// because Node.js built-in modules are synchronously available.
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

// ────────────────────────────────────────────────────────────────────
// Mock remaining dependencies of tcp-connect.ts.
// ────────────────────────────────────────────────────────────────────

vi.mock("electron", () => ({
	app: {
		quit: vi.fn(),
		exit: vi.fn(),
		getPath: vi.fn(() => "/tmp/vt-uu2-test"),
		isQuitting: false,
		isPackaged: false,
	},
	dialog: { showErrorBox: vi.fn() },
}));

vi.mock("../constants", () => ({
	HEARTBEAT_INTERVAL_MS: 5_000,
	IPC_TOKEN: "test-token",
	TCP_FRAME_MAX_BYTES: 4 * 1024 * 1024,
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
	flushPendingOutbound: vi.fn(),
	resetPendingOutbound: vi.fn(),
}));

// tcpConnect is imported dynamically inside beforeEach (after vi.resetModules)
// to ensure the mocked modules are fully initialized before the module under
// test is loaded.

// ────────────────────────────────────────────────────────────────────
// Helper: read tcp-connect.ts source for text assertions.
// ────────────────────────────────────────────────────────────────────

function readTcpConnectSrc(): string {
	// The reassembly contract spans two leaves of the tcp split:
	// frame-reader.ts owns the Buffer.concat / indexOf(0x0a) /
	// subarray+toString("utf8") decode, and close-handler.ts owns the
	// Buffer.alloc(0) reset on close. Asserting against their combined
	// source preserves every original assertion's strength.
	const frameReader = fs.readFileSync(
		path.resolve(__dirname, "../python/tcp/frame-reader.ts"),
		"utf-8",
	);
	const closeHandler = fs.readFileSync(
		path.resolve(__dirname, "../python/tcp/close-handler.ts"),
		"utf-8",
	);
	return `${frameReader}\n${closeHandler}`;
}

// ────────────────────────────────────────────────────────────────────
// Tests
// ────────────────────────────────────────────────────────────────────

describe("TCP UTF-8 reassembly (Buffer-based)", () => {
	let tcpConnect: (port: number) => void;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.useFakeTimers();
		vi.resetModules();
		// Dynamic import AFTER mocks are set up. vitest hoists vi.mock()
		// calls above this import, so the mocked modules are used.
		const mod = await import("../python/tcp-connect");
		tcpConnect = mod.tcpConnect;
		createdSockets.length = 0;
		// Reset state to a clean connected-ready baseline.
		mockState.tcpBuffer = Buffer.alloc(0);
		mockState.tcpSocket = null;
		mockState._tcpAuthed = false;
		mockState._tcpRetryGeneration = 0;
		mockState._tcpRetryCount = 0;
		mockState._tcpRetryTimer = null;
		mockState.heartbeatInterval = null;
		mockState._hadConnectedBefore = false;
		mockState._relaunching = false;
		// Pretend Python is alive so the close handler's retry branch
		// doesn't fire if the socket emits an unexpected "close".
		mockState.pythonProcess = {
			pid: 12345,
		} as unknown as MainState["pythonProcess"];
	});

	afterEach(() => {
		// Clear any heartbeat interval that tcpConnect armed so it
		// doesn't fire between tests.
		if (mockState.heartbeatInterval) {
			clearInterval(mockState.heartbeatInterval);
			mockState.heartbeatInterval = null;
		}
		vi.useRealTimers();
	});

	it("reassembles a 4-byte UTF-8 char (U+1F600) split across two chunks without U+FFFD", () => {
		// U+1F600 = UTF-8 F0 9F 98 80 (4 bytes).
		// Split after the first byte — without Buffer-based reassembly,
		// the first chunk's toString() surfaces U+FFFD for the lone 0xF0.
		const fullLine = `{"type":"transcription_final","text":"\u{1F600}"}\n`;
		const fullBytes = Buffer.from(fullLine, "utf-8");
		// Find the byte offset of the 0xF0 (start of the emoji) and split there.
		const splitIdx = fullBytes.indexOf(0xf0);
		expect(splitIdx).toBeGreaterThan(0);
		const chunk1 = fullBytes.subarray(0, splitIdx + 1); // ...F0
		const chunk2 = fullBytes.subarray(splitIdx + 1); // 9F 98 80...}\n

		tcpConnect(9999);

		// The connect callback ran synchronously (MockSocket.connect calls
		// cb() immediately), so the "data" handler is already registered.
		expect(createdSockets.length).toBeGreaterThanOrEqual(1);
		const socket = createdSockets[0] as EventEmitter;

		// Emit the two chunks — simulating TCP splitting the message
		// mid-character.
		socket.emit("data", chunk1);
		socket.emit("data", chunk2);

		// handleMessage should have been called exactly once with the
		// complete, correctly-decoded message.
		expect(handleMessageMock).toHaveBeenCalledTimes(1);
		const passedMsg = handleMessageMock.mock.calls[0]?.[0] as Record<
			string,
			unknown
		>;
		expect(passedMsg).toBeDefined();
		expect(passedMsg.type).toBe("transcription_final");
		// The critical assertion: the text field is the correct emoji,
		// NOT U+FFFD (the replacement character).
		expect(passedMsg.text).toBe("\u{1F600}");
		expect(passedMsg.text).not.toContain("\u{FFFD}");
	});

	it("reassembles a 3-byte UTF-8 char (U+20AC) split across two chunks", () => {
		// U+20AC = UTF-8 E2 82 AC (3 bytes). Split after the first byte.
		const fullLine = `{"type":"final","amount":"\u{20AC}5.00"}\n`;
		const fullBytes = Buffer.from(fullLine, "utf-8");
		const splitIdx = fullBytes.indexOf(0xe2);
		expect(splitIdx).toBeGreaterThan(0);
		const chunk1 = fullBytes.subarray(0, splitIdx + 1);
		const chunk2 = fullBytes.subarray(splitIdx + 1);

		tcpConnect(9999);

		const socket = createdSockets[0] as EventEmitter;
		socket.emit("data", chunk1);
		socket.emit("data", chunk2);

		expect(handleMessageMock).toHaveBeenCalledTimes(1);
		const passedMsg = handleMessageMock.mock.calls[0]?.[0] as Record<
			string,
			unknown
		>;
		expect(passedMsg.amount).toBe("\u{20AC}5.00");
		expect(passedMsg.amount).not.toContain("\u{FFFD}");
	});

	it("handles multiple lines in a single chunk (byte-correct)", () => {
		// Two complete lines in one chunk, both containing multi-byte chars.
		const line1 = `{"type":"a","text":"\u{65E5}\u{672C}"}\n`;
		const line2 = `{"type":"b","text":"\u{D55C}\u{AD6D}"}\n`;
		const chunk = Buffer.from(line1 + line2, "utf-8");

		tcpConnect(9999);

		const socket = createdSockets[0] as EventEmitter;
		socket.emit("data", chunk);

		expect(handleMessageMock).toHaveBeenCalledTimes(2);
		const msg1 = handleMessageMock.mock.calls[0]?.[0] as Record<
			string,
			unknown
		>;
		const msg2 = handleMessageMock.mock.calls[1]?.[0] as Record<
			string,
			unknown
		>;
		expect(msg1.text).toBe("\u{65E5}\u{672C}");
		expect(msg2.text).toBe("\u{D55C}\u{AD6D}");
	});

	it("handles a line split across THREE chunks (byte-correct)", () => {
		// Split a line with emoji into 3 chunks at arbitrary byte boundaries.
		const fullLine = `{"type":"msg","text":"Hello \u{1F30D} World"}\n`;
		const fullBytes = Buffer.from(fullLine, "utf-8");
		// U+1F30D = UTF-8 F0 9F 8C 8D (4 bytes).
		const emojiStart = fullBytes.indexOf(0xf0);
		// Chunk 1: up to and including the first 2 bytes of the emoji.
		// Chunk 2: the next 1 byte of the emoji.
		// Chunk 3: the last byte of the emoji + rest of the line.
		const chunk1 = fullBytes.subarray(0, emojiStart + 2);
		const chunk2 = fullBytes.subarray(emojiStart + 2, emojiStart + 3);
		const chunk3 = fullBytes.subarray(emojiStart + 3);

		tcpConnect(9999);

		const socket = createdSockets[0] as EventEmitter;
		socket.emit("data", chunk1);
		socket.emit("data", chunk2);
		socket.emit("data", chunk3);

		expect(handleMessageMock).toHaveBeenCalledTimes(1);
		const passedMsg = handleMessageMock.mock.calls[0]?.[0] as Record<
			string,
			unknown
		>;
		expect(passedMsg.text).toBe("Hello \u{1F30D} World");
		expect(passedMsg.text).not.toContain("\u{FFFD}");
	});
});

// ────────────────────────────────────────────────────────────────────
// Source-text contract: pin the Buffer-based reassembly so a future
// refactor cannot silently regress to per-chunk toString().
// ────────────────────────────────────────────────────────────────────

describe("tcp-connect.ts source-text contract (Buffer-based reassembly)", () => {
	const src = readTcpConnectSrc();

	it("does NOT use the old per-chunk toString() append pattern", () => {
		// The old buggy pattern decoded each chunk independently.
		expect(src).not.toMatch(/state\.tcpBuffer\s*\+=\s*chunk\.toString\(\)/);
	});

	it("uses Buffer.concat to merge the incoming chunk with the buffer", () => {
		expect(src).toMatch(/Buffer\.concat\(\[state\.tcpBuffer/);
	});

	it("scans for the newline byte via indexOf(0x0a) (not string split)", () => {
		// Buffer.indexOf(0x0a) scans raw bytes; string.split operates on
		// an already-decoded string (the source of the bug).
		expect(src).toMatch(/state\.tcpBuffer\.indexOf\(0x0a\)/);
	});

	it("decodes each line via subarray + toString utf8", () => {
		// subarray extracts the line bytes; toString("utf8") decodes
		// the complete line exactly once. They are on separate lines
		// (subarray on state.tcpBuffer, toString on lineBuf) — assert
		// both are present, not chained.
		expect(src).toMatch(/\.subarray\(/);
		expect(src).toMatch(/\.toString\("utf8"\)/);
	});

	it("does NOT use StringDecoder (the fix is Buffer.concat, not StringDecoder)", () => {
		// The XV-149 characterization test documented that StringDecoder
		// was NOT applied. The fix uses Buffer.concat instead — a different
		// (simpler) fix that achieves the same byte-correct result.
		expect(src).not.toMatch(
			/import\s+\{\s*StringDecoder\s*\}\s+from\s+["']node:string_decoder["']/,
		);
	});

	it("resets tcpBuffer to Buffer.alloc(0) on close and overflow", () => {
		// The reset must use Buffer.alloc(0) — assigning an empty string
		// would store a string in a Buffer-typed slot (type drift).
		expect(src).toMatch(/state\.tcpBuffer\s*=\s*Buffer\.alloc\(0\)/);
		// No remaining empty-string assignments to tcpBuffer in the source.
		expect(src).not.toMatch(/state\.tcpBuffer\s*=\s*""/);
	});
});
