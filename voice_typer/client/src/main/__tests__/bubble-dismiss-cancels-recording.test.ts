// @vitest-environment node
/**
 * Behavioral tests for the bubble dismiss ('\u00d7') handler's
 * cancel-then-hide semantics.
 *
 * Pre-fix: the dismiss handler unconditionally called
 * `hideBubbleWindow()`. If the user clicked '\u00d7' while a recording
 * or transcription was in flight, the bubble vanished but the audio
 * capture / ctranslate2 thread kept running; the finalized text was
 * pasted into whatever field currently had focus.
 *
 * Post-fix: the dismiss handler checks the cached bubble mode
 * (set at the source by `handle-message.ts` via
 * `setLastKnownBubbleMode()` when a `bubble_set_state` push event
 * is dispatched to the bubble renderer). When the mode is
 * `recording` or `transcribing`, the handler forwards the dismiss
 * click to the Python backend as a `toggle_dictation` IPC (which
 * stops the in-flight pipeline) before hiding. For `idle` / `error`
 * / `fading` / unknown modes, it hides immediately.
 *
 * The previous design monkey-patched `webContents.send` inside the
 * `bubble:ready` handler to intercept outgoing `bubble:set-state`
 * sends; that patch accumulated on every bubble reload (wrapping
 * the already-wrapped `send`). The current design updates the mode
 * at the source (`handle-message.ts`) via `setLastKnownBubbleMode()`,
 * eliminating the patch entirely. These tests call
 * `setLastKnownBubbleMode()` directly to simulate the source update.
 *
 * Call-order contract:
 *   1. `setLastKnownBubbleMode(mode)` caches the mode
 *   2. `bubble:dismiss` -> if mode is recording/transcribing,
 *      `sendToPython({ type: "toggle_dictation" })` is called AND
 *      `hideBubbleWindow()` is called. Otherwise only `hideBubbleWindow()`.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// --- Mocks (top-level so vi.mock factories can reference them) ---

const mocks = vi.hoisted(() => {
	return {
		ipcOn: vi.fn(),
		sendToPython: vi.fn(() => Promise.resolve()),
		hideBubbleWindow: vi.fn(),
		showBubbleWindow: vi.fn(),
		consumeHideAnimationCallback: vi.fn(() => null),
		centerOnActiveDisplay: vi.fn(() => ({ x: 0, y: 0 })),
		resetSavedBubblePosition: vi.fn(),
		cancelScheduledDurablePersist: vi.fn(),
		suppressDurablePersistFor: vi.fn(),
		bubbleWindow: null as unknown,
		webContentsSend: vi.fn(),
	};
});

vi.mock("electron", () => ({
	ipcMain: { on: mocks.ipcOn, handle: vi.fn() },
	screen: {
		getDisplayMatching: vi.fn(() => ({
			workArea: { x: 0, y: 0, width: 1920, height: 1080 },
		})),
	},
}));

vi.mock("../constants", () => ({
	BUBBLE_WIDTH: 74,
	BUBBLE_HEIGHT: 27,
}));

vi.mock("../logging", () => ({
	log: {
		warn: vi.fn(),
		info: vi.fn(),
		error: vi.fn(),
		debug: vi.fn(),
	},
}));

vi.mock("../python", () => ({
	sendToPython: mocks.sendToPython,
}));

vi.mock("../state", () => ({
	get state() {
		return {
			bubbleWindow: mocks.bubbleWindow,
			bubbleDraggable: true,
			bubblePosition: "top",
			_bubblePageReady: false,
		};
	},
}));

vi.mock("../windows/bubble-window", () => ({
	cancelScheduledDurablePersist: mocks.cancelScheduledDurablePersist,
	centerOnActiveDisplay: mocks.centerOnActiveDisplay,
	consumeHideAnimationCallback: mocks.consumeHideAnimationCallback,
	hideBubbleWindow: mocks.hideBubbleWindow,
	resetSavedBubblePosition: mocks.resetSavedBubblePosition,
	showBubbleWindow: mocks.showBubbleWindow,
	suppressDurablePersistFor: mocks.suppressDurablePersistFor,
}));

// --- Helpers ---

function makeMockBubbleWindow() {
	const mainFrame = { id: "main-frame" };
	const wc = {
		mainFrame,
		send: mocks.webContentsSend,
	};
	const win = {
		webContents: wc,
		isDestroyed: vi.fn(() => false),
	};
	return { win, wc, mainFrame };
}

function makeMockBubbleEvent(mainFrame: unknown) {
	return { senderFrame: mainFrame } as unknown as Electron.IpcMainEvent;
}

function lookupHandler(
	channel: string,
): ((...args: unknown[]) => void) | undefined {
	for (const call of mocks.ipcOn.mock.calls) {
		if (call[0] === channel) {
			return call[1] as (...args: unknown[]) => void;
		}
	}
	return undefined;
}

// --- Tests ---

describe("bubble dismiss handler: cancel-then-hide semantics", () => {
	beforeEach(async () => {
		vi.clearAllMocks();
		mocks.bubbleWindow = null;
		mocks.webContentsSend = vi.fn();
		vi.resetModules();
		await import("../ipc/bubble-handlers");
	});

	it("dismiss while recording: sends toggle_dictation then hides", async () => {
		const { registerBubbleHandlers, setLastKnownBubbleMode } = await import(
			"../ipc/bubble-handlers"
		);
		const { win, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;

		registerBubbleHandlers();
		setLastKnownBubbleMode("recording");

		const dismissHandler = lookupHandler("bubble:dismiss");
		expect(dismissHandler).toBeDefined();
		dismissHandler?.(makeMockBubbleEvent(mainFrame));

		expect(mocks.sendToPython).toHaveBeenCalledTimes(1);
		expect(mocks.sendToPython).toHaveBeenCalledWith({
			type: "toggle_dictation",
		});
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("dismiss while transcribing: sends toggle_dictation then hides", async () => {
		const { registerBubbleHandlers, setLastKnownBubbleMode } = await import(
			"../ipc/bubble-handlers"
		);
		const { win, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;

		registerBubbleHandlers();
		setLastKnownBubbleMode("transcribing");

		lookupHandler("bubble:dismiss")?.(makeMockBubbleEvent(mainFrame));

		expect(mocks.sendToPython).toHaveBeenCalledTimes(1);
		expect(mocks.sendToPython).toHaveBeenCalledWith({
			type: "toggle_dictation",
		});
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("dismiss while idle: hides WITHOUT sending toggle_dictation", async () => {
		const { registerBubbleHandlers, setLastKnownBubbleMode } = await import(
			"../ipc/bubble-handlers"
		);
		const { win, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;

		registerBubbleHandlers();
		setLastKnownBubbleMode("idle");

		lookupHandler("bubble:dismiss")?.(makeMockBubbleEvent(mainFrame));

		expect(mocks.sendToPython).not.toHaveBeenCalled();
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("dismiss while error: hides WITHOUT sending toggle_dictation", async () => {
		const { registerBubbleHandlers, setLastKnownBubbleMode } = await import(
			"../ipc/bubble-handlers"
		);
		const { win, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;

		registerBubbleHandlers();
		setLastKnownBubbleMode("error");

		lookupHandler("bubble:dismiss")?.(makeMockBubbleEvent(mainFrame));

		expect(mocks.sendToPython).not.toHaveBeenCalled();
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("dismiss while fading: hides WITHOUT sending toggle_dictation", async () => {
		const { registerBubbleHandlers, setLastKnownBubbleMode } = await import(
			"../ipc/bubble-handlers"
		);
		const { win, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;

		registerBubbleHandlers();
		setLastKnownBubbleMode("fading");

		lookupHandler("bubble:dismiss")?.(makeMockBubbleEvent(mainFrame));

		expect(mocks.sendToPython).not.toHaveBeenCalled();
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("dismiss when bubbleWindow is null (assertFromBubble fails): no-op", async () => {
		const { registerBubbleHandlers } = await import("../ipc/bubble-handlers");
		const { mainFrame } = makeMockBubbleWindow();

		registerBubbleHandlers();

		const dismissHandler = lookupHandler("bubble:dismiss");
		expect(dismissHandler).toBeDefined();
		dismissHandler?.(makeMockBubbleEvent(mainFrame));

		expect(mocks.sendToPython).not.toHaveBeenCalled();
		expect(mocks.hideBubbleWindow).not.toHaveBeenCalled();
	});

	it("dismiss while recording: if toggle_dictation IPC rejects, hideBubbleWindow still fires", async () => {
		const { registerBubbleHandlers, setLastKnownBubbleMode } = await import(
			"../ipc/bubble-handlers"
		);
		const { win, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;
		mocks.sendToPython.mockImplementationOnce(() =>
			Promise.reject(new Error("Python backend disconnected")),
		);

		registerBubbleHandlers();
		setLastKnownBubbleMode("recording");

		lookupHandler("bubble:dismiss")?.(makeMockBubbleEvent(mainFrame));

		expect(mocks.sendToPython).toHaveBeenCalledTimes(1);
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
		await Promise.resolve();
		await Promise.resolve();
	});

	it("setLastKnownBubbleMode is idempotent: multiple calls overwrite (no accumulation)", async () => {
		const {
			registerBubbleHandlers,
			setLastKnownBubbleMode,
			getLastKnownBubbleMode,
		} = await import("../ipc/bubble-handlers");
		const { win, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;

		registerBubbleHandlers();

		setLastKnownBubbleMode("idle");
		setLastKnownBubbleMode("recording");
		setLastKnownBubbleMode("transcribing");

		expect(getLastKnownBubbleMode()).toBe("transcribing");

		lookupHandler("bubble:dismiss")?.(makeMockBubbleEvent(mainFrame));
		expect(mocks.sendToPython).toHaveBeenCalledTimes(1);
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("non-recording modes do not trigger toggle_dictation", async () => {
		const {
			registerBubbleHandlers,
			setLastKnownBubbleMode,
			_resetLastKnownBubbleMode,
		} = await import("../ipc/bubble-handlers");
		const { win, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;

		registerBubbleHandlers();

		const modes = ["idle", "error", "fading", "not-a-real-mode"];
		for (let i = 0; i < modes.length; i++) {
			const mode = modes[i];
			if (mode === undefined) {
				continue;
			}
			_resetLastKnownBubbleMode();
			setLastKnownBubbleMode(mode);
			lookupHandler("bubble:dismiss")?.(makeMockBubbleEvent(mainFrame));
			expect(mocks.sendToPython).not.toHaveBeenCalled();
			expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(i + 1);
		}
	});

	it("dismiss handler is SEC-016-restricted: events from non-bubble frames are dropped", async () => {
		const { registerBubbleHandlers, setLastKnownBubbleMode } = await import(
			"../ipc/bubble-handlers"
		);
		const { win } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;

		registerBubbleHandlers();
		setLastKnownBubbleMode("recording");

		const hostileEvent = makeMockBubbleEvent({
			id: "hostile-frame",
		});
		lookupHandler("bubble:dismiss")?.(hostileEvent);

		expect(mocks.sendToPython).not.toHaveBeenCalled();
		expect(mocks.hideBubbleWindow).not.toHaveBeenCalled();
	});
});

describe("dismissAndHideBubble (exported body shared with the global shortcut)", () => {
	beforeEach(async () => {
		vi.clearAllMocks();
		mocks.bubbleWindow = null;
		vi.resetModules();
		await import("../ipc/bubble-handlers");
	});

	it("recording mode: sends toggle_dictation then hides", async () => {
		const { dismissAndHideBubble, setLastKnownBubbleMode } = await import(
			"../ipc/bubble-handlers"
		);
		setLastKnownBubbleMode("recording");
		dismissAndHideBubble();

		expect(mocks.sendToPython).toHaveBeenCalledTimes(1);
		expect(mocks.sendToPython).toHaveBeenCalledWith({
			type: "toggle_dictation",
		});
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("transcribing mode: sends toggle_dictation then hides", async () => {
		const { dismissAndHideBubble, setLastKnownBubbleMode } = await import(
			"../ipc/bubble-handlers"
		);
		setLastKnownBubbleMode("transcribing");
		dismissAndHideBubble();

		expect(mocks.sendToPython).toHaveBeenCalledTimes(1);
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("idle mode: hides WITHOUT sending toggle_dictation", async () => {
		const { dismissAndHideBubble, setLastKnownBubbleMode } = await import(
			"../ipc/bubble-handlers"
		);
		setLastKnownBubbleMode("idle");
		dismissAndHideBubble();

		expect(mocks.sendToPython).not.toHaveBeenCalled();
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("clears the cached mode to idle after firing (rapid second call skips the toggle)", async () => {
		const {
			dismissAndHideBubble,
			getLastKnownBubbleMode,
			setLastKnownBubbleMode,
		} = await import("../ipc/bubble-handlers");
		setLastKnownBubbleMode("recording");
		dismissAndHideBubble();
		dismissAndHideBubble();

		expect(getLastKnownBubbleMode()).toBe("idle");
		expect(mocks.sendToPython).toHaveBeenCalledTimes(1);
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(2);
	});

	it("toggle_dictation IPC rejection does not prevent hiding", async () => {
		const { dismissAndHideBubble, setLastKnownBubbleMode } = await import(
			"../ipc/bubble-handlers"
		);
		mocks.sendToPython.mockImplementationOnce(() =>
			Promise.reject(new Error("Python backend disconnected")),
		);

		setLastKnownBubbleMode("recording");
		dismissAndHideBubble();

		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
		await Promise.resolve();
		await Promise.resolve();
	});
});
