// @vitest-environment node
/**
 * Unit tests for `showMainWindow()` raise-to-front behavior.
 *
 * Regression coverage: a tray-icon left-click pushed `show_window` to
 * Electron, but `showMainWindow()` only called `show()` + `focus()`.
 * The OS foreground lock (Windows refuses SetForegroundWindow from a
 * background process) meant `focus()` merely flashed the taskbar
 * button — the dashboard stayed visible but buried at the bottom of
 * the z-order behind other applications' windows. The fix raises the
 * window above everything via a momentary always-on-top lift +
 * `moveTop()`, then drops the flag.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({
	app: { isQuitting: false, isPackaged: false },
	BrowserWindow: { getAllWindows: vi.fn(() => []) },
	Menu: { setApplicationMenu: vi.fn() },
	nativeTheme: { shouldUseDarkColors: false, on: vi.fn(), off: vi.fn() },
	dialog: { showErrorBox: vi.fn() },
	shell: { openExternal: vi.fn(() => Promise.resolve()) },
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

interface FakeWin {
	isDestroyed: () => boolean;
	isMinimized: ReturnType<typeof vi.fn>;
	isVisible: ReturnType<typeof vi.fn>;
	restore: ReturnType<typeof vi.fn>;
	show: ReturnType<typeof vi.fn>;
	focus: ReturnType<typeof vi.fn>;
	moveTop: ReturnType<typeof vi.fn>;
	setAlwaysOnTop: ReturnType<typeof vi.fn>;
}

function makeFakeWin(overrides: Partial<FakeWin> = {}): FakeWin {
	return {
		isDestroyed: () => false,
		isMinimized: vi.fn(() => false),
		isVisible: vi.fn(() => true),
		restore: vi.fn(),
		show: vi.fn(),
		focus: vi.fn(),
		moveTop: vi.fn(),
		setAlwaysOnTop: vi.fn(),
		...overrides,
	};
}

describe("showMainWindow raise-to-front", () => {
	let showMainWindow: typeof import("../windows/main-window").showMainWindow;
	let state: typeof import("../state").state;

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.resetModules();
		({ showMainWindow } = await import("../windows/main-window"));
		({ state } = await import("../state"));
	});

	it("raises an already-visible window above all others (alwaysOnTop lift + moveTop + focus)", () => {
		const win = makeFakeWin();
		(state as { mainWindow: unknown }).mainWindow = win;

		showMainWindow();

		// The raise sequence must run: lift → show → focus → moveTop → drop.
		expect(win.setAlwaysOnTop).toHaveBeenNthCalledWith(1, true, "screen-saver");
		expect(win.show).toHaveBeenCalled();
		expect(win.focus).toHaveBeenCalled();
		expect(win.moveTop).toHaveBeenCalled();
		// Never leaves the window stuck always-on-top.
		expect(win.setAlwaysOnTop).toHaveBeenLastCalledWith(false);
	});

	it("shows a hidden window before raising it", () => {
		const win = makeFakeWin({ isVisible: vi.fn(() => false) });
		(state as { mainWindow: unknown }).mainWindow = win;

		showMainWindow();

		expect(win.show).toHaveBeenCalled();
		expect(win.setAlwaysOnTop).toHaveBeenNthCalledWith(1, true, "screen-saver");
		expect(win.moveTop).toHaveBeenCalled();
		expect(win.setAlwaysOnTop).toHaveBeenLastCalledWith(false);
	});

	it("restores a minimized window before raising it", () => {
		const win = makeFakeWin({ isMinimized: vi.fn(() => true) });
		(state as { mainWindow: unknown }).mainWindow = win;

		showMainWindow();

		expect(win.restore).toHaveBeenCalled();
		expect(win.show).toHaveBeenCalled();
		expect(win.moveTop).toHaveBeenCalled();
		expect(win.setAlwaysOnTop).toHaveBeenLastCalledWith(false);
	});
});
