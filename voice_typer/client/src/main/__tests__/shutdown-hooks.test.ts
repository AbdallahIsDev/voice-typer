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
		_bubblePageReady: false,
		_hideTimeout: null,
		_tcpRetryCount: 0,
		_tcpRetryTimer: null,
		_tcpRetryGeneration: 0,
		_tcpAuthed: false,
		_hadConnectedBefore: false,
		_relaunching: false,
		_restartTriggered: false,
		...overrides,
	} as MainState;
}

const mockState = makeMockState();
vi.mock("../state", () => ({ state: mockState }));

vi.mock("../i18n", () => ({ mainT: (k: string) => k }));
// G4-H-24 + PVT-G5-006: bootstrap.ts imports `stopPython` from
// `../python` (the index). The index re-exports `stopPython` from
// `./python/stop-python`, but it also pulls in `./send-to-python` →
// `../index` (the heavy main entry that fires Electron APIs at
// module-eval time). Mocking `../python` short-circuits the whole chain
// so the test can import `bootstrap.ts` without triggering the main
// entry's side effects. Both the barrel and the leaf module are mocked
// because bootstrap.ts's value import resolves through the barrel.
vi.mock("../python", () => ({ stopPython: vi.fn() }));
vi.mock("../python/stop-python", () => ({ stopPython: vi.fn() }));
vi.mock("../single_instance", () => ({
	computeConfigDir: () => "/mock",
	// G4-H-24: bootstrap.ts also imports `clearElectronPidFile` for the
	// production exit hook. The test never exercises that hook (it
	// injects its own `exit` mock), but the import + symbol binding
	// still needs to resolve.
	clearElectronPidFile: vi.fn(),
}));

describe("R6-F7: index.ts registers app.on('will-quit', stopPython)", () => {
	it("index.ts source contains an app.on('will-quit', ...) block that calls stopPython", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../index.ts"),
			"utf-8",
		);
		// PVT-G5-005 (R6-F7): the will-quit handler must exist as a
		// belt-and-suspenders shutdown hook (before-quit can be
		// suppressed by event.preventDefault(), macOS logout paths,
		// or tray close-to-tray on some platforms).
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
		// PVT-G5-006: locate the onUncaught handler definition
		// (more precise than searching for the "uncaughtException"
		// string, which also appears in JSDoc comments above the
		// handler). Assert stopPython is called within the
		// handler body.
		const handlerIdx = src.indexOf("const onUncaught");
		expect(handlerIdx).toBeGreaterThan(-1);
		const block = src.slice(handlerIdx, handlerIdx + 2500);
		expect(block).toContain("stopPython");
	});

	it("bootstrap.ts source calls stopPython() inside the unhandledRejection handler", () => {
		const src = fs.readFileSync(
			path.resolve(__dirname, "../bootstrap.ts"),
			"utf-8",
		);
		// PVT-G5-006: same check for the onRejection handler.
		const handlerIdx = src.indexOf("const onRejection");
		expect(handlerIdx).toBeGreaterThan(-1);
		const block = src.slice(handlerIdx, handlerIdx + 2500);
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
