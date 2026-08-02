// @vitest-environment node
/**
 * Unit tests for `src/main/windows/main-window.ts` ready-to-show gating.
 *
 * Verifies that `createMainWindow()`:
 *   • constructs the BrowserWindow with `show: false` (never `true`)
 *     regardless of the `forceShow` / `START_HIDDEN` inputs.
 *   • registers a `once("ready-to-show", ...)` listener that calls
 *     `.show()` only when `shouldShow` is true (preserves the
 *     START_HIDDEN autostart path).
 *
 * Regression coverage for the finding: previously `show: shouldShow`
 * flashed a blank white BrowserWindow for the 200-800ms between
 * BrowserWindow construction and the renderer's first paint.
 *
 * Uses `vi.hoisted` for all mock capture state so the hoisted
 * `vi.mock` factory can reference the spies (vitest 4 hoists
 * `vi.mock` above all top-level `const` bindings — the factory
 * closure can only see hoisted bindings).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// All mock capture state MUST be declared via `vi.hoisted` so the
// hoisted `vi.mock("electron", ...)` factory can reference them.
// Vitest 4 hoists `vi.mock` factories above all top-level `const`
// declarations — a factory that closes over a plain `const` throws
// "Cannot access X before initialization" at mock-evaluation time.
const mocks = vi.hoisted(() => {
	// Captured BrowserWindow ctor options + `once` handler so the
	// test can assert `show: false` and invoke the ready-to-show
	// handler directly.
	let capturedBrowserWindowOptions: Record<string, unknown> | null = null;
	let capturedReadyToShowHandler: (() => void) | null = null;

	const showMock = vi.fn();

	// Regular function declarations are constructible (arrow
	// functions are not). The BrowserWindow mock returns an
	// object whose `once` captures the `ready-to-show` handler.
	function MockBrowserWindow(options: Record<string, unknown>): {
		on: ReturnType<typeof vi.fn>;
		once: ReturnType<typeof vi.fn>;
		webContents: {
			setWindowOpenHandler: ReturnType<typeof vi.fn>;
			on: ReturnType<typeof vi.fn>;
			send: ReturnType<typeof vi.fn>;
			toggleDevTools: ReturnType<typeof vi.fn>;
			reload: ReturnType<typeof vi.fn>;
			id: number;
		};
		setIcon: ReturnType<typeof vi.fn>;
		isDestroyed: () => boolean;
		isVisible: () => boolean;
		isMinimized: () => boolean;
		show: ReturnType<typeof vi.fn>;
		focus: ReturnType<typeof vi.fn>;
		hide: ReturnType<typeof vi.fn>;
		setSkipTaskbar: ReturnType<typeof vi.fn>;
		restore: ReturnType<typeof vi.fn>;
		reload: ReturnType<typeof vi.fn>;
		loadURL: ReturnType<typeof vi.fn>;
		loadFile: ReturnType<typeof vi.fn>;
	} {
		capturedBrowserWindowOptions = options;
		return {
			on: vi.fn(),
			once: vi.fn((event: string, handler: () => void) => {
				if (event === "ready-to-show") capturedReadyToShowHandler = handler;
			}),
			webContents: {
				setWindowOpenHandler: vi.fn(),
				on: vi.fn(),
				send: vi.fn(),
				toggleDevTools: vi.fn(),
				reload: vi.fn(),
				id: 42,
			},
			setIcon: vi.fn(),
			isDestroyed: () => false,
			isVisible: () => true,
			isMinimized: () => false,
			show: showMock,
			focus: vi.fn(),
			hide: vi.fn(),
			setSkipTaskbar: vi.fn(),
			restore: vi.fn(),
			reload: vi.fn(),
			loadURL: vi.fn(() => Promise.resolve()),
			loadFile: vi.fn(() => Promise.resolve()),
		};
	}

	return {
		MockBrowserWindow,
		getCapturedOptions: () => capturedBrowserWindowOptions,
		getCapturedReadyToShowHandler: () => capturedReadyToShowHandler,
		resetCaptured: () => {
			capturedBrowserWindowOptions = null;
			capturedReadyToShowHandler = null;
		},
		showMock,
	};
});

vi.mock("electron", () => ({
	app: {
		isQuitting: false,
		isPackaged: false,
	},
	BrowserWindow: mocks.MockBrowserWindow,
	Menu: { setApplicationMenu: vi.fn() },
	nativeTheme: {
		shouldUseDarkColors: false,
		on: vi.fn(),
		off: vi.fn(),
	},
	dialog: { showErrorBox: vi.fn() },
	shell: { openExternal: vi.fn() },
}));

vi.mock("../constants", () => ({
	START_HIDDEN: false,
	RENDER_RELOAD_BACKOFF_MS: 2000,
}));
vi.mock("../ipc/channels", () => ({
	WindowChannels: { maximizedChanged: "window:maximized-changed" },
}));
vi.mock("../logging", () => ({
	cleanConsoleMsg: (s: string) => s,
	RENDERER_CLR: "",
	RESET: "",
	redactPii: (s: string) => s,
	log: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
	appendLogLine: vi.fn(),
	rendererErrorsLogPath: vi.fn(() => ""),
}));
// Mock the send-to-python helper so the closed handler (which
// we don't assert here) doesn't pull in the real send-to-python
// module's heavy import graph.
vi.mock("../python/send-to-python", () => ({
	_removeRendererFromBackpressure: vi.fn(),
}));

describe("main-window ready-to-show gating", () => {
	beforeEach(async () => {
		vi.clearAllMocks();
		mocks.resetCaptured();
		vi.resetModules();
	});

	it("createMainWindow constructs BrowserWindow with show:false (START_HIDDEN=false, forceShow=false)", async () => {
		const { createMainWindow } = await import("../windows/main-window");
		const { state } = await import("../state");
		(state as { mainWindow: unknown }).mainWindow = null;
		createMainWindow(false);

		const opts = mocks.getCapturedOptions();
		expect(opts).not.toBeNull();
		expect(opts?.show).toBe(false);
	});

	it("createMainWindow constructs BrowserWindow with show:false even when forceShow=true", async () => {
		const { createMainWindow } = await import("../windows/main-window");
		const { state } = await import("../state");
		(state as { mainWindow: unknown }).mainWindow = null;
		createMainWindow(true);

		// show must ALWAYS be false in the ctor — the ready-to-show
		// listener below is what actually calls .show().
		const opts = mocks.getCapturedOptions();
		expect(opts?.show).toBe(false);
	});

	it("createMainWindow registers a once('ready-to-show') listener", async () => {
		const { createMainWindow } = await import("../windows/main-window");
		const { state } = await import("../state");
		(state as { mainWindow: unknown }).mainWindow = null;
		createMainWindow(false);

		const handler = mocks.getCapturedReadyToShowHandler();
		expect(handler).not.toBeNull();
		expect(typeof handler).toBe("function");
	});

	it("ready-to-show handler calls .show() when shouldShow is true (START_HIDDEN=false)", async () => {
		const { createMainWindow } = await import("../windows/main-window");
		const { state } = await import("../state");
		(state as { mainWindow: unknown }).mainWindow = null;
		createMainWindow(false);

		// Fire the ready-to-show handler — should call .show()
		const handler = mocks.getCapturedReadyToShowHandler();
		handler?.();
		expect(mocks.showMock).toHaveBeenCalledTimes(1);
	});
});
