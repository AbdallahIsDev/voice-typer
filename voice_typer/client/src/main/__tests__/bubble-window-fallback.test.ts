// @vitest-environment node
/**
 * R6-F4 unit tests for `bubble-window.ts` hideBubbleWindow fallback path.
 *
 * Verifies that the fallback timeout callback removes the
 * `bubble:hidden` one-shot listener BEFORE calling `win.hide()`,
 * preventing a stale listener from firing on a subsequent
 * (slow) renderer `bubble:hidden` emit.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MainState } from "../state";

// Track ipcMain listener registrations so we can assert removal.
type IpcHandler = (...args: unknown[]) => void;
const ipcListeners = new Map<string, IpcHandler[]>();
const mockIpcOnce = vi.fn((channel: string, handler: IpcHandler) => {
	const arr = ipcListeners.get(channel) ?? [];
	arr.push(handler);
	ipcListeners.set(channel, arr);
});
const mockIpcRemoveListener = vi.fn((channel: string, handler: IpcHandler) => {
	const arr = ipcListeners.get(channel);
	if (!arr) return;
	const idx = arr.indexOf(handler);
	if (idx >= 0) arr.splice(idx, 1);
});
const mockIpcRemoveAllListeners = vi.fn((channel: string) => {
	ipcListeners.delete(channel);
});

vi.mock("electron", () => ({
	BrowserWindow: vi.fn(),
	ipcMain: {
		on: vi.fn(),
		once: mockIpcOnce,
		removeListener: mockIpcRemoveListener,
		removeAllListeners: mockIpcRemoveAllListeners,
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

describe("R6-F4: hideBubbleWindow fallback removes bubble:hidden listener", () => {
	let hideBubbleWindow: () => void;
	let win: {
		isDestroyed: ReturnType<typeof vi.fn>;
		isVisible: ReturnType<typeof vi.fn>;
		hide: ReturnType<typeof vi.fn>;
		webContents: { send: ReturnType<typeof vi.fn> };
	};

	beforeEach(async () => {
		vi.clearAllMocks();
		vi.useFakeTimers();
		ipcListeners.clear();
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
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it("registers a one-shot bubble:hidden listener on hide", () => {
		hideBubbleWindow();
		expect(mockIpcOnce).toHaveBeenCalledWith(
			"bubble:hidden",
			expect.any(Function),
		);
		expect(ipcListeners.get("bubble:hidden")?.length).toBe(1);
	});

	it("fallback timeout calls ipcMain.removeListener('bubble:hidden', onHidden) BEFORE win.hide()", () => {
		hideBubbleWindow();
		// At this point the onHidden listener is registered.
		expect(ipcListeners.get("bubble:hidden")?.length).toBe(1);

		// Advance past the 300ms fallback timeout.
		vi.advanceTimersByTime(300);

		// The listener must have been removed...
		expect(mockIpcRemoveListener).toHaveBeenCalledWith(
			"bubble:hidden",
			expect.any(Function),
		);
		expect(ipcListeners.get("bubble:hidden")?.length ?? 0).toBe(0);
		// ...AND win.hide() must have been called.
		expect(win.hide).toHaveBeenCalledTimes(1);
	});

	it("removeListener is called BEFORE win.hide() (ordering assertion)", () => {
		hideBubbleWindow();
		vi.advanceTimersByTime(300);

		// Inspect call order across both mocks.
		const removeListenerCallOrder =
			mockIpcRemoveListener.mock.invocationCallOrder[0];
		const hideCallOrder = win.hide.mock.invocationCallOrder[0];
		expect(removeListenerCallOrder).toBeDefined();
		expect(hideCallOrder).toBeDefined();
		expect(removeListenerCallOrder).toBeLessThan(hideCallOrder);
	});

	it("subsequent renderer bubble:hidden emit does NOT trigger a second win.hide()", () => {
		hideBubbleWindow();
		vi.advanceTimersByTime(300); // fallback fires, removes listener, hides
		// Reset mock to count fresh calls.
		win.hide.mockClear();

		// Now the renderer (slowly) emits bubble:hidden — but the
		// listener was removed, so the emit is a no-op.
		const remainingListeners = ipcListeners.get("bubble:hidden") ?? [];
		for (const h of remainingListeners) {
			h();
		}
		expect(win.hide).not.toHaveBeenCalled();
	});

	it("animated path (renderer signals before timeout) still hides + leaves no listener", () => {
		hideBubbleWindow();
		// Renderer signals animation complete BEFORE the 300ms timeout.
		const onHidden = ipcListeners.get("bubble:hidden")?.[0];
		expect(onHidden).toBeDefined();
		onHidden?.();
		// Once-handler auto-removes after firing (simulate via the mock
		// having removed it on the once() call — we cleared the list
		// manually to mimic ipcMain.once semantics).
		ipcListeners.delete("bubble:hidden");

		expect(win.hide).toHaveBeenCalledTimes(1);
		// Advance past the timeout — it should NOT hide again because
		// state._hideTimeout was cleared by onHidden.
		vi.advanceTimersByTime(500);
		expect(win.hide).toHaveBeenCalledTimes(1);
	});
});
