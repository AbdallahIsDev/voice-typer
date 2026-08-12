// @vitest-environment node
/**
 * Unit tests for `src/main/windows/main-window.ts` cross-platform window
 * chrome.
 *
 * The app uses a custom title bar everywhere (the OS frame doesn't blend
 * with the app theme), but the window-CONTROL buttons are
 * platform-convention-dependent:
 *   - macOS: native traffic lights (red/yellow/green) on the left, drawn
 *     by the OS via `titleBarStyle: "hiddenInset"` + a traffic-light
 *     position. The renderer's TitleBar then omits its own
 *     minimize/maximize/close buttons and reserves a gutter.
 *   - Windows/Linux: `frame: false` + the renderer draws the three
 *     buttons on the right (the convention on both platforms).
 *
 * Regression coverage for: previously `frame: false` was set on EVERY
 * platform, so macOS users saw Windows-style buttons on the right and
 * no traffic lights at all — a cross-platform UX error.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// All mock capture state MUST be declared via `vi.hoisted` so the
// hoisted `vi.mock("electron", ...)` factory can reference them
// (vitest 4 hoists `vi.mock` above all top-level `const` bindings).
const mocks = vi.hoisted(() => {
	let capturedBrowserWindowOptions: Record<string, unknown> | null = null;
	let capturedWindow: Record<string, unknown> | null = null;
	// Mutable per-test value — the window's isMaximized reads it through a
	// closure so the leave-full-screen test can simulate the macOS
	// zoom-then-fullscreen flow (window still maximized on return).
	let isMaximizedValue = false;

	const showMock = vi.fn();

	function MockBrowserWindow(
		options: Record<string, unknown>,
	): Record<string, unknown> {
		capturedBrowserWindowOptions = options;
		const win = {
			on: vi.fn(),
			once: vi.fn(),
			webContents: {
				setWindowOpenHandler: vi.fn(),
				on: vi.fn(),
				send: vi.fn(),
				toggleDevTools: vi.fn(),
				reload: vi.fn(),
				id: 42,
			},
			isDestroyed: () => false,
			isVisible: () => true,
			isMinimized: () => false,
			isMaximized: () => isMaximizedValue,
			show: showMock,
			focus: vi.fn(),
			hide: vi.fn(),
			setSkipTaskbar: vi.fn(),
			restore: vi.fn(),
			reload: vi.fn(),
			loadURL: vi.fn(() => Promise.resolve()),
			loadFile: vi.fn(() => Promise.resolve()),
		};
		capturedWindow = win;
		return win;
	}
	// `broadcastMaximized` fans out via BrowserWindow.getAllWindows().
	MockBrowserWindow.getAllWindows = vi.fn(() =>
		capturedWindow ? [capturedWindow] : [],
	);

	return {
		MockBrowserWindow,
		getCapturedOptions: () => capturedBrowserWindowOptions,
		getCapturedWindow: () => capturedWindow,
		resetCaptured: () => {
			capturedBrowserWindowOptions = null;
			capturedWindow = null;
			isMaximizedValue = false;
		},
		setIsMaximized: (v: boolean) => {
			isMaximizedValue = v;
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
vi.mock("../python/send-to-python", () => ({
	_removeRendererFromBackpressure: vi.fn(),
}));

describe("main-window cross-platform window chrome", () => {
	beforeEach(async () => {
		vi.clearAllMocks();
		mocks.resetCaptured();
		vi.resetModules();
		vi.restoreAllMocks();
	});

	async function createWindowWithPlatform(
		platform: "win32" | "darwin" | "linux",
	) {
		// process.platform is read inside createMainWindow at call time.
		vi.spyOn(process, "platform", "get").mockReturnValue(platform);
		const { createMainWindow } = await import("../windows/main-window");
		const { state } = await import("../state");
		(state as { mainWindow: unknown }).mainWindow = null;
		createMainWindow(false);
		return mocks.getCapturedOptions();
	}

	it("Windows: frameless window (frame:false) — renderer draws the window-control buttons", async () => {
		const opts = await createWindowWithPlatform("win32");
		expect(opts).not.toBeNull();
		expect(opts?.frame).toBe(false);
		// macOS-only options must NOT leak onto other platforms.
		expect(opts?.titleBarStyle).toBeUndefined();
		expect(opts?.trafficLightPosition).toBeUndefined();
	});

	it("Linux: frameless window (frame:false) — same custom window-control buttons as Windows", async () => {
		const opts = await createWindowWithPlatform("linux");
		expect(opts).not.toBeNull();
		expect(opts?.frame).toBe(false);
		expect(opts?.titleBarStyle).toBeUndefined();
	});

	it("macOS: native traffic lights via titleBarStyle hiddenInset + trafficLightPosition (no frame:false)", async () => {
		const opts = await createWindowWithPlatform("darwin");
		expect(opts).not.toBeNull();
		// `titleBarStyle: "hiddenInset"` hides the bar but keeps the
		// OS-drawn red/yellow/green dots; `frame: false` would strip
		// them, so it must NOT be set on macOS.
		expect(opts?.titleBarStyle).toBe("hiddenInset");
		expect(opts?.trafficLightPosition).toEqual({ x: 12, y: 10 });
		expect(opts?.frame).toBeUndefined();
	});

	it("macOS: enter/leave-full-screen (green traffic light) mirror onto maximized-changed", async () => {
		await createWindowWithPlatform("darwin");
		const win = mocks.getCapturedWindow();
		expect(win).not.toBeNull();

		// Find the on() registrations and invoke the fullscreen handlers.
		const onMock = win?.on as ReturnType<typeof vi.fn> | undefined;
		expect(onMock).toBeDefined();
		const registrations = onMock?.mock.calls as
			| [event: string, handler: () => void][]
			| undefined;
		const enter = registrations?.find(([e]) => e === "enter-full-screen");
		const leave = registrations?.find(([e]) => e === "leave-full-screen");
		expect(enter?.[1]).toBeTypeOf("function");
		expect(leave?.[1]).toBeTypeOf("function");

		enter?.[1]();
		const winRecord = win as {
			webContents: { send: ReturnType<typeof vi.fn> };
		};
		const sendMock = winRecord.webContents.send;
		expect(sendMock).toHaveBeenCalledWith("window:maximized-changed", true);
		leave?.[1]();
		expect(sendMock).toHaveBeenCalledWith("window:maximized-changed", false);
	});

	it("macOS: leave-full-screen from a pre-maximized window re-broadcasts true (no stale is-maximized)", async () => {
		await createWindowWithPlatform("darwin");
		const win = mocks.getCapturedWindow();
		expect(win).not.toBeNull();
		const onMock = win?.on as ReturnType<typeof vi.fn> | undefined;
		const registrations = onMock?.mock.calls as
			| [event: string, handler: () => void][]
			| undefined;
		const leave = registrations?.find(([e]) => e === "leave-full-screen");
		expect(leave?.[1]).toBeTypeOf("function");

		// Simulate: window was zoomed (maximized) before fullscreen;
		// on return it is STILL maximized → must broadcast true.
		mocks.setIsMaximized(true);
		leave?.[1]();
		const sendMock = (
			win as {
				webContents: { send: ReturnType<typeof vi.fn> };
			}
		).webContents.send;
		expect(sendMock).toHaveBeenLastCalledWith("window:maximized-changed", true);
	});
});
