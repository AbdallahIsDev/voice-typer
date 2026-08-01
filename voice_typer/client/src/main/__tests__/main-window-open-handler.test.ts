// @vitest-environment node
/**
 * Unit tests for the main-window `setWindowOpenHandler` hardening.
 *
 * Verifies that `createMainWindow()` installs a `setWindowOpenHandler`
 * on the main window's `webContents` that:
 *   • denies every renderer-initiated window.open / target=_blank navigation
 *     (returns `{ action: "deny" }` regardless of URL scheme);
 *   • routes http(s) URLs to the user's default browser via
 *     `shell.openExternal` (fire-and-forget);
 *   • does NOT call `shell.openExternal` for non-http(s) schemes
 *     (file://, javascript:, data:, blob:).
 *
 * Regression coverage for main-window-open-handler (main-window half).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// Captured handler instance — set inside the BrowserWindow mock
// constructor so the test can invoke it directly.
let capturedOpenHandler:
	| ((details: { url: string }) => { action: "deny" | "allow" })
	| null = null;

const shellOpenExternal = vi.fn<(url: string) => Promise<void>>();

// Regular function declarations are constructible (arrow functions are
// not). The BrowserWindow mock returns an object whose webContents
// exposes setWindowOpenHandler so the test can capture the registered
// handler.
function MockBrowserWindow(): {
	webContents: {
		setWindowOpenHandler: (h: typeof capturedOpenHandler) => void;
		on: ReturnType<typeof vi.fn>;
		send: ReturnType<typeof vi.fn>;
		toggleDevTools: ReturnType<typeof vi.fn>;
		reload: ReturnType<typeof vi.fn>;
	};
	on: ReturnType<typeof vi.fn>;
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
	return {
		webContents: {
			setWindowOpenHandler: (handler: typeof capturedOpenHandler) => {
				capturedOpenHandler = handler;
			},
			on: vi.fn(),
			send: vi.fn(),
			toggleDevTools: vi.fn(),
			reload: vi.fn(),
		},
		on: vi.fn(),
		setIcon: vi.fn(),
		isDestroyed: () => false,
		isVisible: () => true,
		isMinimized: () => false,
		show: vi.fn(),
		focus: vi.fn(),
		hide: vi.fn(),
		setSkipTaskbar: vi.fn(),
		restore: vi.fn(),
		reload: vi.fn(),
		loadURL: vi.fn(),
		loadFile: vi.fn(),
	};
}

vi.mock("electron", () => ({
	app: {
		isQuitting: false,
		isPackaged: false,
	},
	BrowserWindow: MockBrowserWindow,
	Menu: { setApplicationMenu: vi.fn() },
	nativeTheme: {
		shouldUseDarkColors: false,
		on: vi.fn(),
		off: vi.fn(),
	},
	dialog: { showErrorBox: vi.fn() },
	shell: { openExternal: shellOpenExternal },
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

describe("main-window setWindowOpenHandler hardening", () => {
	beforeEach(async () => {
		vi.clearAllMocks();
		capturedOpenHandler = null;
		shellOpenExternal.mockResolvedValue(undefined);
		vi.resetModules();
	});

	it("createMainWindow installs a setWindowOpenHandler on webContents", async () => {
		const { createMainWindow } = await import("../windows/main-window");
		const { state } = await import("../state");
		(state as { mainWindow: unknown }).mainWindow = null;
		createMainWindow(false);
		expect(capturedOpenHandler).not.toBeNull();
		expect(typeof capturedOpenHandler).toBe("function");
	});

	it("handler returns { action: 'deny' } for an https URL", async () => {
		const { createMainWindow } = await import("../windows/main-window");
		const { state } = await import("../state");
		(state as { mainWindow: unknown }).mainWindow = null;
		createMainWindow(false);
		const result = capturedOpenHandler?.({ url: "https://example.com" });
		expect(result).toEqual({ action: "deny" });
	});

	it("handler returns { action: 'deny' } for an http URL", async () => {
		const { createMainWindow } = await import("../windows/main-window");
		const { state } = await import("../state");
		(state as { mainWindow: unknown }).mainWindow = null;
		createMainWindow(false);
		const result = capturedOpenHandler?.({ url: "http://example.com" });
		expect(result).toEqual({ action: "deny" });
	});

	it("handler routes http(s) URLs to shell.openExternal", async () => {
		const { createMainWindow } = await import("../windows/main-window");
		const { state } = await import("../state");
		(state as { mainWindow: unknown }).mainWindow = null;
		createMainWindow(false);
		capturedOpenHandler?.({ url: "https://example.com/path?q=1" });
		// The handler is synchronous but the openExternal call is fire-and-forget.
		await Promise.resolve();
		expect(shellOpenExternal).toHaveBeenCalledTimes(1);
		expect(shellOpenExternal).toHaveBeenCalledWith(
			"https://example.com/path?q=1",
		);
	});

	it("handler does NOT call shell.openExternal for non-http(s) schemes", async () => {
		const { createMainWindow } = await import("../windows/main-window");
		const { state } = await import("../state");
		(state as { mainWindow: unknown }).mainWindow = null;
		createMainWindow(false);
		for (const url of [
			"file:///etc/passwd",
			"javascript:alert(1)",
			"data:text/html,<script>require('child_process')</script>",
			"blob:https://example.com/abc",
		]) {
			capturedOpenHandler?.({ url });
		}
		await Promise.resolve();
		expect(shellOpenExternal).not.toHaveBeenCalled();
	});

	it("handler still denies when shell.openExternal rejects", async () => {
		const { createMainWindow } = await import("../windows/main-window");
		const { state } = await import("../state");
		(state as { mainWindow: unknown }).mainWindow = null;
		createMainWindow(false);
		shellOpenExternal.mockRejectedValueOnce(new Error("no default browser"));
		const result = capturedOpenHandler?.({ url: "https://example.com" });
		// Drain microtasks so the rejected openExternal promise's .catch runs.
		await Promise.resolve();
		await Promise.resolve();
		expect(result).toEqual({ action: "deny" });
		// The error path must NOT throw out of the handler.
		expect(shellOpenExternal).toHaveBeenCalledTimes(1);
	});

	it("handler is case-insensitive on the scheme", async () => {
		const { createMainWindow } = await import("../windows/main-window");
		const { state } = await import("../state");
		(state as { mainWindow: unknown }).mainWindow = null;
		createMainWindow(false);
		capturedOpenHandler?.({ url: "HTTPS://Example.COM" });
		await Promise.resolve();
		expect(shellOpenExternal).toHaveBeenCalledTimes(1);
		expect(shellOpenExternal).toHaveBeenCalledWith("HTTPS://Example.COM");
	});
});
