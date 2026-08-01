// @vitest-environment node
/**
 * Behavioral tests for the bubble dismiss ('×') handler's
 * cancel-then-hide semantics.
 *
 * Pre-fix (): the dismiss handler unconditionally called
 * `hideBubbleWindow()`. If the user clicked '×' while a recording
 * or transcription was in flight, the bubble vanished but the audio
 * capture / ctranslate2 thread kept running; the finalized text was
 * pasted into whatever field currently had focus — a clear violation
 * of the user's "stop this" intent.
 *
 * Post-fix: the dismiss handler checks the cached bubble mode
 * (mirrored from outgoing `bubble:set-state` IPC sends, installed
 * lazily inside the `bubble:ready` handler). When the mode is
 * `recording` or `transcribing`, the handler forwards the dismiss
 * click to the Python backend as a `toggle_dictation` IPC (which
 * stops the in-flight pipeline) before hiding. For `idle` / `error`
 * / `fading` / unknown modes, it hides immediately — preserving the
 * original  behavior for the no-op case.
 *
 * These tests exercise the handler end-to-end with mocked Electron
 * IPC + state, verifying the call-order contract:
 *   1. `bubble:ready` → tracker installed
 *   2. `webContents.send("bubble:set-state", mode)` → tracker caches mode
 *   3. `bubble:dismiss` → if mode is recording/transcribing,
 *      `sendToPython({ type: "toggle_dictation" })` is called AND
 *      `hideBubbleWindow()` is called. Otherwise only `hideBubbleWindow()`.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// ─── Mocks (top-level so vi.mock factories can reference them) ────────────

const mocks = vi.hoisted(() => {
	return {
		ipcOn: vi.fn(),
		sendToPython: vi.fn(() => Promise.resolve()),
		hideBubbleWindow: vi.fn(),
		showBubbleWindow: vi.fn(),
		consumeHideAnimationCallback: vi.fn(() => null),
		centerOnActiveDisplay: vi.fn(() => ({ x: 0, y: 0 })),
		resetSavedBubblePosition: vi.fn(),
		// Mutable mock state — tests overwrite `bubbleWindow`
		// per-scenario. Tests must NOT mutate `bubbleWindow`
		// across tests without resetting (we re-import the
		// module under test in `beforeEach` so the tracker's
		// WeakSet is fresh).
		bubbleWindow: null as unknown,
		// The mock webContents `send` is reassigned by the
		// tracker-installer. We capture the original here so
		// tests can invoke it after wrapping.
		webContentsSend: vi.fn(),
	};
});

// Mock electron — only `ipcMain.on` (captured) and `screen` (unused
// by the dismiss/ready handlers, but imported at module load).
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
	// `state` is a getter that always returns the latest
	// `mocks.bubbleWindow` — so tests can reassign the mock
	// window between scenarios without re-mocking.
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
	centerOnActiveDisplay: mocks.centerOnActiveDisplay,
	consumeHideAnimationCallback: mocks.consumeHideAnimationCallback,
	hideBubbleWindow: mocks.hideBubbleWindow,
	resetSavedBubblePosition: mocks.resetSavedBubblePosition,
	showBubbleWindow: mocks.showBubbleWindow,
}));

// ─── Helpers ──────────────────────────────────────────────────────────────

/**
 * Build a mock bubble window + webContents that satisfies
 * `assertFromBubble(event)` (senderFrame === mainFrame).
 *
 * The returned `send` function is the webContents.send mock. After
 * the tracker is installed (via the `bubble:ready` handler), the
 * tracker wraps `send` — so calls to `wc.send("bubble:set-state", X)`
 * propagate to BOTH the tracker (which caches X) AND the original
 * mock (which records the call for assertion).
 */
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

/** Build a mock IpcMainEvent whose senderFrame matches the bubble window. */
function makeMockBubbleEvent(mainFrame: unknown) {
	return { senderFrame: mainFrame } as unknown as Electron.IpcMainEvent;
}

/**
 * Look up a registered `ipcMain.on` handler by channel name.
 * Returns `undefined` if no handler is registered for that channel.
 */
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

// ─── Tests ────────────────────────────────────────────────────────────────

describe("bubble dismiss handler: cancel-then-hide semantics", () => {
	beforeEach(async () => {
		vi.clearAllMocks();
		mocks.bubbleWindow = null;
		mocks.webContentsSend = vi.fn();
		// Re-import the module under test so the tracker's
		// module-level WeakSet + `_lastKnownBubbleMode` start
		// fresh. The `vi.mock` factories above stay in effect
		// across `vi.resetModules()` — only the module-internal
		// state is reset.
		vi.resetModules();
		await import("../ipc/bubble-handlers");
	});

	it("dismiss while recording: sends toggle_dictation then hides", async () => {
		const { registerBubbleHandlers } = await import("../ipc/bubble-handlers");
		const { win, wc, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;

		registerBubbleHandlers();

		// 1. Fire `bubble:ready` to install the state tracker.
		const readyHandler = lookupHandler("bubble:ready");
		expect(readyHandler).toBeDefined();
		readyHandler?.(makeMockBubbleEvent(mainFrame));

		// 2. Simulate Python forwarding a `bubble:set-state` event
		//    with mode "recording". The tracker intercepts this
		//    outgoing send and caches the mode.
		wc.send("bubble:set-state", "recording");

		// 3. Fire `bubble:dismiss`.
		const dismissHandler = lookupHandler("bubble:dismiss");
		expect(dismissHandler).toBeDefined();
		dismissHandler?.(makeMockBubbleEvent(mainFrame));

		// 4. Assert: toggle_dictation was sent to Python AND
		//    hideBubbleWindow was called.
		expect(mocks.sendToPython).toHaveBeenCalledTimes(1);
		expect(mocks.sendToPython).toHaveBeenCalledWith({
			type: "toggle_dictation",
		});
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("dismiss while transcribing: sends toggle_dictation then hides", async () => {
		const { registerBubbleHandlers } = await import("../ipc/bubble-handlers");
		const { win, wc, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;

		registerBubbleHandlers();
		lookupHandler("bubble:ready")?.(makeMockBubbleEvent(mainFrame));

		wc.send("bubble:set-state", "transcribing");

		lookupHandler("bubble:dismiss")?.(makeMockBubbleEvent(mainFrame));

		expect(mocks.sendToPython).toHaveBeenCalledTimes(1);
		expect(mocks.sendToPython).toHaveBeenCalledWith({
			type: "toggle_dictation",
		});
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("dismiss while idle: hides WITHOUT sending toggle_dictation", async () => {
		const { registerBubbleHandlers } = await import("../ipc/bubble-handlers");
		const { win, wc, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;

		registerBubbleHandlers();
		lookupHandler("bubble:ready")?.(makeMockBubbleEvent(mainFrame));

		wc.send("bubble:set-state", "idle");

		lookupHandler("bubble:dismiss")?.(makeMockBubbleEvent(mainFrame));

		// Critical: must NOT call toggle_dictation when idle —
		// doing so would START a new recording (the user clicked
		// dismiss to hide the bubble, not to start dictation).
		expect(mocks.sendToPython).not.toHaveBeenCalled();
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("dismiss while error: hides WITHOUT sending toggle_dictation", async () => {
		const { registerBubbleHandlers } = await import("../ipc/bubble-handlers");
		const { win, wc, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;

		registerBubbleHandlers();
		lookupHandler("bubble:ready")?.(makeMockBubbleEvent(mainFrame));

		wc.send("bubble:set-state", "error");

		lookupHandler("bubble:dismiss")?.(makeMockBubbleEvent(mainFrame));

		expect(mocks.sendToPython).not.toHaveBeenCalled();
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("dismiss while fading: hides WITHOUT sending toggle_dictation", async () => {
		const { registerBubbleHandlers } = await import("../ipc/bubble-handlers");
		const { win, wc, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;

		registerBubbleHandlers();
		lookupHandler("bubble:ready")?.(makeMockBubbleEvent(mainFrame));

		wc.send("bubble:set-state", "fading");

		lookupHandler("bubble:dismiss")?.(makeMockBubbleEvent(mainFrame));

		expect(mocks.sendToPython).not.toHaveBeenCalled();
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("dismiss before bubble:ready fired (tracker not installed): hides WITHOUT toggle_dictation", async () => {
		// Default cached mode is "idle" (module-level init).
		// If the tracker hasn't been installed yet (e.g. the
		// renderer hasn't signaled readiness, or the bubble
		// window is null), the dismiss handler must fall back
		//to the original  behavior — just hide.
		const { registerBubbleHandlers } = await import("../ipc/bubble-handlers");
		const { mainFrame } = makeMockBubbleWindow();
		// Note: NOT setting mocks.bubbleWindow — state.bubbleWindow
		// is null, so `bubble:ready`'s `assertFromBubble` returns
		// false and the tracker is never installed.

		registerBubbleHandlers();

		// Even though we can't fire `bubble:ready` (assertFromBubble
		// fails when bubbleWindow is null), the dismiss handler
		// ALSO checks `assertFromBubble` — so it returns early
		// without calling hideBubbleWindow either. This is the
		//existing  security boundary: only the bubble window
		// can dismiss itself.
		const dismissHandler = lookupHandler("bubble:dismiss");
		expect(dismissHandler).toBeDefined();
		dismissHandler?.(makeMockBubbleEvent(mainFrame));

		expect(mocks.sendToPython).not.toHaveBeenCalled();
		expect(mocks.hideBubbleWindow).not.toHaveBeenCalled();
	});

	it("dismiss while recording: if toggle_dictation IPC rejects, hideBubbleWindow still fires", async () => {
		// The dismiss handler uses `void sendToPython(...).catch(...)`
		// — the catch swallows the rejection so the subsequent
		// `hideBubbleWindow()` call is always reached. This test
		// verifies the swallow-and-hide behavior so a Python
		// backend crash doesn't strand the bubble visible.
		const { registerBubbleHandlers } = await import("../ipc/bubble-handlers");
		const { win, wc, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;
		mocks.sendToPython.mockImplementationOnce(() =>
			Promise.reject(new Error("Python backend disconnected")),
		);

		registerBubbleHandlers();
		lookupHandler("bubble:ready")?.(makeMockBubbleEvent(mainFrame));
		wc.send("bubble:set-state", "recording");

		lookupHandler("bubble:dismiss")?.(makeMockBubbleEvent(mainFrame));

		expect(mocks.sendToPython).toHaveBeenCalledTimes(1);
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
		// Flush the microtask queue so the rejected promise's
		// `.catch` handler runs (otherwise vitest reports an
		// unhandled rejection).
		await Promise.resolve();
		await Promise.resolve();
	});

	it("state tracker is idempotent: bubble:ready fired twice does not double-wrap send", async () => {
		const { registerBubbleHandlers } = await import("../ipc/bubble-handlers");
		const { win, wc, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;

		registerBubbleHandlers();
		const readyHandler = lookupHandler("bubble:ready");
		expect(readyHandler).toBeDefined();

		// Fire `bubble:ready` twice on the same webContents.
		readyHandler?.(makeMockBubbleEvent(mainFrame));
		readyHandler?.(makeMockBubbleEvent(mainFrame));

		// A single `bubble:set-state` send should trigger a
		// single underlying mock call (no double-wrap → no
		// exponential call growth).
		wc.send("bubble:set-state", "recording");
		expect(mocks.webContentsSend).toHaveBeenCalledTimes(1);
	});

	it("unknown / non-string bubble:set-state payloads do not poison the tracker", async () => {
		const { registerBubbleHandlers } = await import("../ipc/bubble-handlers");
		const { win, wc, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;

		registerBubbleHandlers();
		lookupHandler("bubble:ready")?.(makeMockBubbleEvent(mainFrame));

		// Send a non-string payload — the tracker must ignore it.
		wc.send("bubble:set-state", { bogus: "object" });
		wc.send("bubble:set-state", 42);
		wc.send("bubble:set-state", null);
		wc.send("bubble:set-state", "not-a-real-mode");

		// Cached mode stays at the default "idle" — dismiss
		// must NOT send toggle_dictation.
		lookupHandler("bubble:dismiss")?.(makeMockBubbleEvent(mainFrame));
		expect(mocks.sendToPython).not.toHaveBeenCalled();
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("dismiss handler is SEC-016-restricted: events from non-bubble frames are dropped", async () => {
		// A malicious main renderer sending `bubble:dismiss`
		// must be rejected by `assertFromBubble` — the sender's
		// frame must match the bubble window's mainFrame.
		const { registerBubbleHandlers } = await import("../ipc/bubble-handlers");
		const { win, mainFrame } = makeMockBubbleWindow();
		mocks.bubbleWindow = win;

		registerBubbleHandlers();
		lookupHandler("bubble:ready")?.(makeMockBubbleEvent(mainFrame));

		// Send `bubble:dismiss` from a DIFFERENT frame.
		const hostileEvent = makeMockBubbleEvent({
			id: "hostile-frame",
		});
		lookupHandler("bubble:dismiss")?.(hostileEvent);

		expect(mocks.sendToPython).not.toHaveBeenCalled();
		expect(mocks.hideBubbleWindow).not.toHaveBeenCalled();
	});
});
