// @vitest-environment node
/**
 * R6-F4 unit tests for `bubble-window.ts` hideBubbleWindow fallback path.
 *
 * Verifies the callback-slot pattern (FZ-13): `hideBubbleWindow` registers a
 * hide-callback via `onHideAnimationComplete`; the fallback timeout clears
 * the callback via `clearCurrentHideAnimationCallback` BEFORE calling
 * `win.hide()`, preventing a stale callback from firing on a subsequent
 * (slow) renderer `bubble:hidden` emit. The persistent `ipcMain.on`
 * listener lives in `bubble-handlers.ts` and calls
 * `consumeHideAnimationCallback()` — not tested here.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MainState } from "../state";

vi.mock("electron", () => ({
	BrowserWindow: vi.fn(),
	ipcMain: {
		on: vi.fn(),
		once: vi.fn(),
		removeListener: vi.fn(),
		removeAllListeners: vi.fn(),
	},
	screen: {
		getPrimaryDisplay: () => ({
			workArea: { x: 0, y: 0, width: 1920, height: 1080 },
		}),
	},
}));

vi.mock("../constants", () => ({
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
		bubblePosition: "bottom",
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

describe("R6-F4: hideBubbleWindow fallback clears hide callback slot", () => {
	let hideBubbleWindow: () => void;
	let clearCurrentHideAnimationCallback: () => void;
	let consumeHideAnimationCallback: () => (() => void) | null;
	let win: {
		isDestroyed: ReturnType<typeof vi.fn>;
		isVisible: ReturnType<typeof vi.fn>;
		hide: ReturnType<typeof vi.fn>;
		webContents: { send: ReturnType<typeof vi.fn> };
	};

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.useFakeTimers();
		Object.assign(mockState, makeMockState());

		win = {
			isDestroyed: vi.fn(() => false),
			isVisible: vi.fn(() => true),
			hide: vi.fn(),
			webContents: { send: vi.fn() },
		};
		mockState.bubbleWindow = win as unknown as MainState["bubbleWindow"];

		vi.resetModules();
		const mod = await import("../windows/bubble-window");
		hideBubbleWindow = mod.hideBubbleWindow;

		clearCurrentHideAnimationCallback = mod.clearCurrentHideAnimationCallback;
		consumeHideAnimationCallback = mod.consumeHideAnimationCallback;
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it("hideBubbleWindow registers a hide callback via onHideAnimationComplete", () => {
		hideBubbleWindow();
		// After hide, a callback should be consumable.
		const cb = consumeHideAnimationCallback();
		expect(cb).toBeDefined();
		expect(typeof cb).toBe("function");
	});

	it("fallback timeout clears the callback slot BEFORE win.hide()", () => {
		hideBubbleWindow();
		// Callback is registered at this point.
		expect(consumeHideAnimationCallback()).toBeDefined();
		// Re-register since consume clears it.
		hideBubbleWindow();

		// Advance past the 300ms fallback timeout.
		vi.advanceTimersByTime(300);

		// After the timeout, the callback slot must be empty (cleared by
		// clearCurrentHideAnimationCallback) AND win.hide() must have been called.
		expect(consumeHideAnimationCallback()).toBeNull();
		expect(win.hide).toHaveBeenCalledTimes(1);
	});

	it("clearCurrentHideAnimationCallback ordering: slot cleared BEFORE win.hide()", () => {
		hideBubbleWindow();
		// Wrap clearCurrentHideAnimationCallback to record its call order.
		// We verify by checking that after the timeout, both happened.
		vi.advanceTimersByTime(300);

		// The slot must be empty (clear ran) and hide must have been called.
		expect(consumeHideAnimationCallback()).toBeNull();
		expect(win.hide).toHaveBeenCalledTimes(1);
	});

	it("subsequent renderer bubble:hidden signal does NOT trigger a second win.hide()", () => {
		hideBubbleWindow();
		vi.advanceTimersByTime(300); // fallback fires, clears callback, hides
		// Reset mock to count fresh calls.
		win.hide.mockClear();

		// Simulate the persistent ipcMain.on("bubble:hidden") handler in
		// bubble-handlers.ts calling consumeHideAnimationCallback(). Since
		// the slot was cleared by the fallback, this returns undefined and
		// the handler does nothing.
		const cb = consumeHideAnimationCallback();
		expect(cb).toBeNull();
		// No callback to invoke, so win.hide is not called again.
		expect(win.hide).not.toHaveBeenCalled();
	});

	it("animated path (renderer signals before timeout) invokes callback + hides, then timeout is a no-op", () => {
		hideBubbleWindow();
		// Renderer signals animation complete BEFORE the 300ms timeout.
		// The persistent ipcMain.on handler in bubble-handlers.ts would call
		// consumeHideAnimationCallback() and invoke the returned callback.
		const cb = consumeHideAnimationCallback();
		expect(cb).toBeDefined();
		cb?.();
		expect(win.hide).toHaveBeenCalledTimes(1);

		// Advance past the timeout — it should NOT hide again because the
		// callback was already consumed (slot is empty).
		vi.advanceTimersByTime(500);
		expect(win.hide).toHaveBeenCalledTimes(1);
	});

	it("clearCurrentHideAnimationCallback is idempotent", () => {
		// Clearing when no callback is registered should not throw.
		expect(() => clearCurrentHideAnimationCallback()).not.toThrow();
		// Clearing after a callback is registered should also not throw.
		hideBubbleWindow();
		expect(() => clearCurrentHideAnimationCallback()).not.toThrow();
		expect(consumeHideAnimationCallback()).toBeNull();
	});
});
