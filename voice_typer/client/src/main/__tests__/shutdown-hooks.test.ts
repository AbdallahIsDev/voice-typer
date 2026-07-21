// @vitest-environment node
/**
 * R6-F7 unit tests for the belt-and-suspenders `stopPython()` shutdown
 * hooks in `index.ts` (will-quit) and `bootstrap.ts` (uncaughtException).
 *
 * These tests verify the static contract: the modules register handlers
 * that call `stopPython()`. Since we can't import `index.ts` (it calls
 * Electron APIs at module top level), we assert the registration logic
 * indirectly by reading the source and importing only the testable
 * pieces (bootstrap's `setupErrorHandlers`).
 */
import fs from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MainState } from "../state";

// Mock electron.
const mockAppOn = vi.fn();
const mockAppQuit = vi.fn();
vi.mock("electron", () => ({
	app: {
		on: mockAppOn,
		quit: mockAppQuit,
		exit: vi.fn(),
		isPackaged: false,
		isQuitting: false,
		getPath: vi.fn(() => "/tmp"),
		setPath: vi.fn(),
	},
	dialog: { showErrorBox: vi.fn() },
	session: {
		defaultSession: {
			webRequest: { onHeadersReceived: vi.fn() },
		},
	},
}));

function makeMockState(overrides: Partial<MainState> = {}): MainState {
	return {
		pythonProcess: null,
		tcpSocket: null,
		mainWindow: null,
		bubbleWindow: null,
		pendingRequests: new Map(),
		nextId: 1,
		tcpBuffer: "",
		pythonReady: false,
		pythonExitedEarly: false,
		heartbeatInterval: null,
		sessionNonce: "",
		bubblePosition: "top",
		bubbleDraggable: true,
		bubbleDragging: false,
		_bubblePageReady: false,
		_hideTimeout: null,
		_tcpRetryCount: 0,
		_tcpRetryTimer: null,
		_tcpRetryGeneration: 0,
		_tcpAuthed: false,
		_hadConnectedBefore: false,
		_relaunching: false,
		_restartTriggered: false,
		preMaximizeBounds: null,
		...overrides,
	} as MainState;
}

const mockState = makeMockState();
vi.mock("../state", () => ({ state: mockState }));

vi.mock("../i18n", () => ({ mainT: (k: string) => k }));
vi.mock("../python/stop-python", () => ({ stopPython: vi.fn() }));
vi.mock("../single_instance", () => ({ computeConfigDir: () => "/mock" }));

describe("R6-F7: index.ts registers app.on('will-quit', stopPython)", () => {
	it("index.ts source contains an app.on('will-quit', ...) block that calls stopPython", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../index.ts"),
			"utf-8",
		);
		// The will-quit handler must exist...
		expect(src).toMatch(/app\.on\(\s*["']will-quit["']/);
		// ...and it must call stopPython() (possibly inside a try/catch).
		// Find the will-quit block and assert stopPython appears after it
		// within ~500 chars.
		const idx = src.indexOf("will-quit");
		expect(idx).toBeGreaterThan(-1);
		const block = src.slice(idx, idx + 500);
		expect(block).toContain("stopPython");
	});

	it("index.ts source still contains the before-quit handler (unchanged behavior)", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../index.ts"),
			"utf-8",
		);
		expect(src).toMatch(/app\.on\(\s*["']before-quit["']/);
	});
});

describe("R6-F7: bootstrap.ts uncaughtException calls stopPython", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		Object.assign(mockState, makeMockState());
		vi.resetModules();
	});

	it("bootstrap.ts source imports stopPython", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../bootstrap.ts"),
			"utf-8",
		);
		expect(src).toMatch(
			/import\s+\{[^}]*stopPython[^}]*\}\s+from\s+["']\.\/python["']/,
		);
	});

	it("bootstrap.ts source calls stopPython() inside the uncaughtException handler", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../bootstrap.ts"),
			"utf-8",
		);
		const uncaughtIdx = src.indexOf('"uncaughtException"');
		expect(uncaughtIdx).toBeGreaterThan(-1);
		// Find the uncaughtException block and assert stopPython is called
		// somewhere AFTER it (within ~2000 chars of the handler body).
		const block = src.slice(uncaughtIdx, uncaughtIdx + 2500);
		expect(block).toContain("stopPython");
	});

	it("bootstrapRuntime registers the uncaughtException + unhandledRejection handlers", async () => {
		const onSpy = vi.spyOn(process, "on");
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const restore: Array<() => void> = [];
		// Avoid clobbering the real process listeners — capture only.
		const originalOn = process.on.bind(process);
		const captured = new Set<string>();
		onSpy.mockImplementation(
			(event: string | symbol, handler: (...args: unknown[]) => void) => {
				captured.add(String(event));
				restore.push(() => originalOn(event, handler));
				return process;
			},
		);
		try {
			const { bootstrapRuntime } = await import("../bootstrap");
			// Avoid actually firing the CSP setup (which calls session.webRequest)
			// — it's already mocked. Just call bootstrap.
			expect(() => bootstrapRuntime()).not.toThrow();
			expect(captured).toContain("uncaughtException");
			expect(captured).toContain("unhandledRejection");
		} finally {
			onSpy.mockRestore();
		}
	});
});
