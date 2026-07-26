// @vitest-environment node
/**
 * R6-F3 unit tests for `main-window.ts` `nativeTheme` listener registration.
 *
 * Verifies that `registerNativeThemeListener()` is idempotent (calling it
 * N times registers exactly ONE listener), and that the test-only reset
 * helper restores the unregistered state.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock electron's `app`, `BrowserWindow`, `Menu`, `nativeTheme` so the
// module can be imported in a vitest runner. We need to track listener
// registration on `nativeTheme`.
const nativeThemeListeners: Array<() => void> = [];
const mockNativeThemeOn = vi.fn((event: string, handler: () => void) => {
	if (event === "updated") nativeThemeListeners.push(handler);
});
const mockNativeThemeOff = vi.fn((event: string, handler: () => void) => {
	if (event === "updated") {
		const idx = nativeThemeListeners.indexOf(handler);
		if (idx >= 0) nativeThemeListeners.splice(idx, 1);
	}
});

vi.mock("electron", () => ({
	app: {
		isQuitting: false,
		isPackaged: false,
	},
	BrowserWindow: vi.fn(),
	Menu: { setApplicationMenu: vi.fn() },
	nativeTheme: {
		shouldUseDarkColors: false,
		on: mockNativeThemeOn,
		off: mockNativeThemeOff,
	},
}));

vi.mock("../constants", () => ({
	START_HIDDEN: false,
	BUBBLE_WIDTH: 74,
	BUBBLE_HEIGHT: 27,
}));
vi.mock("../logging", () => ({
	cleanConsoleMsg: (s: string) => s,
	RENDERER_CLR: "",
	RESET: "",
	BUBBLE_CLR: "",
	ts: () => "",
	log: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
	appendLogLine: vi.fn(),
	rendererErrorsLogPath: vi.fn(() => ""),
}));
vi.mock("../state", () => ({
	state: { mainWindow: null, bubbleWindow: null, bubblePosition: "bottom" },
}));

describe("R6-F3: nativeTheme listener registration", () => {
	beforeEach(async () => {
		vi.clearAllMocks();
		nativeThemeListeners.length = 0;
		vi.resetModules();
	});

	it("registerNativeThemeListener registers exactly one listener", async () => {
		const mod = await import("../windows/main-window");
		mod._resetNativeThemeListenerForTest();
		expect(mod._nativeThemeListenerRegistered()).toBe(false);

		mod.registerNativeThemeListener();
		expect(mod._nativeThemeListenerRegistered()).toBe(true);
		expect(nativeThemeListeners.length).toBe(1);
	});

	it("is idempotent — calling N times still leaves exactly one listener", async () => {
		const mod = await import("../windows/main-window");
		mod._resetNativeThemeListenerForTest();

		for (let i = 0; i < 5; i++) {
			mod.registerNativeThemeListener();
		}
		expect(nativeThemeListeners.length).toBe(1);
		expect(mod._nativeThemeListenerRegistered()).toBe(true);
	});

	it("_resetNativeThemeListenerForTest removes the listener", async () => {
		const mod = await import("../windows/main-window");
		mod._resetNativeThemeListenerForTest();
		mod.registerNativeThemeListener();
		expect(nativeThemeListeners.length).toBe(1);

		mod._resetNativeThemeListenerForTest();
		expect(nativeThemeListeners.length).toBe(0);
		expect(mod._nativeThemeListenerRegistered()).toBe(false);
	});

	it("the handler reads state.mainWindow live (no stale closure)", async () => {
		const mod = await import("../windows/main-window");
		const { state } = await import("../state");
		mod._resetNativeThemeListenerForTest();
		mod.registerNativeThemeListener();

		const setIcon = vi.fn();
		// Install a fresh mainWindow after registration — the handler
		// should observe it because it reads `state.mainWindow` at call
		// time, not at registration time.
		(state as { mainWindow: unknown }).mainWindow = {
			setIcon,
			isDestroyed: () => false,
		};

		// Fire the handler.
		nativeThemeListeners[0]?.();
		expect(setIcon).toHaveBeenCalledTimes(1);
	});

	it("the handler is a no-op when state.mainWindow is destroyed", async () => {
		const mod = await import("../windows/main-window");
		const { state } = await import("../state");
		mod._resetNativeThemeListenerForTest();
		mod.registerNativeThemeListener();

		const setIcon = vi.fn();
		(state as { mainWindow: unknown }).mainWindow = {
			setIcon,
			isDestroyed: () => true, // destroyed — handler must bail
		};

		nativeThemeListeners[0]?.();
		expect(setIcon).not.toHaveBeenCalled();
	});
});
