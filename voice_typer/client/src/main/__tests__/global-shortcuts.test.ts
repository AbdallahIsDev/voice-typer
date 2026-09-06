// @vitest-environment node
/**
 * Behavioral tests for the OS-global bubble-dismiss shortcut
 * (`shortcuts/global-shortcuts.ts`).
 *
 * The main process registers `CommandOrControl+Shift+D` as a
 * system-wide accelerator whose callback delegates to
 * `ipc/bubble-handlers.ts::dismissAndHideBubble` — the exact body the
 * bubble's own '×' button uses. These tests pin:
 *
 *   1. The accelerator constant value (single source of truth).
 *   2. The registered callback routes to the shared dismiss body:
 *      recording-mode → `toggle_dictation` sent + bubble hidden;
 *      idle-mode → just hidden.
 *   3. Registration failure (`register` returning `false`, e.g.
 *      accelerator taken by another app) logs a warning and does NOT
 *      throw (graceful degradation).
 *   4. Double register / double unregister are idempotent no-ops
 *      (never stacks duplicate callbacks or double-unregisters).
 *
 * Electron's own docs are the contract here: `globalShortcut.register`
 * returns `boolean` ("Whether or not the shortcut was registered
 * successfully") and silently fails when the accelerator is taken by
 * another application.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// --- Mocks (top-level so vi.mock factories can reference them) ---

const mocks = vi.hoisted(() => {
	return {
		gsRegister: vi.fn((_accelerator: string, _callback: () => void) => true),
		gsUnregister: vi.fn((_accelerator: string) => undefined),
		ipcOn: vi.fn(),
		sendToPython: vi.fn(() => Promise.resolve()),
		hideBubbleWindow: vi.fn(),
		showBubbleWindow: vi.fn(),
		consumeHideAnimationCallback: vi.fn(() => null),
		centerOnActiveDisplay: vi.fn(() => ({ x: 0, y: 0 })),
		resetSavedBubblePosition: vi.fn(),
		cancelScheduledDurablePersist: vi.fn(),
		suppressDurablePersistFor: vi.fn(),
		logWarn: vi.fn(),
		bubbleWindow: null as unknown,
	};
});

vi.mock("electron", () => ({
	globalShortcut: {
		register: mocks.gsRegister,
		unregister: mocks.gsUnregister,
	},
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
		warn: mocks.logWarn,
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

function registeredCallback(): () => void {
	const call = mocks.gsRegister.mock.calls.find(
		(c) => c[0] === "CommandOrControl+Shift+D",
	);
	expect(call).toBeDefined();
	return call?.[1] as () => void;
}

describe("global-shortcuts: bubble-dismiss accelerator", () => {
	beforeEach(async () => {
		vi.clearAllMocks();
		mocks.bubbleWindow = null;
		mocks.gsRegister.mockImplementation(() => true);
		vi.resetModules();
		await import("../ipc/bubble-handlers");
		const { _resetGlobalShortcutsForTest } = await import(
			"../shortcuts/global-shortcuts"
		);
		_resetGlobalShortcutsForTest();
	});

	it("exposes the canonical accelerator string", async () => {
		const { BUBBLE_DISMISS_ACCELERATOR } = await import(
			"../shortcuts/global-shortcuts"
		);
		expect(BUBBLE_DISMISS_ACCELERATOR).toBe("CommandOrControl+Shift+D");
	});

	it("registers the accelerator with a function callback", async () => {
		const { registerGlobalShortcuts } = await import(
			"../shortcuts/global-shortcuts"
		);
		registerGlobalShortcuts();

		expect(mocks.gsRegister).toHaveBeenCalledTimes(1);
		expect(mocks.gsRegister).toHaveBeenCalledWith(
			"CommandOrControl+Shift+D",
			expect.any(Function),
		);
		expect(mocks.logWarn).not.toHaveBeenCalled();
	});

	it("callback routes to the dismiss body while recording (toggle_dictation + hide)", async () => {
		const { setLastKnownBubbleMode } = await import("../ipc/bubble-handlers");
		const { registerGlobalShortcuts } = await import(
			"../shortcuts/global-shortcuts"
		);

		registerGlobalShortcuts();
		setLastKnownBubbleMode("recording");
		registeredCallback()();

		expect(mocks.sendToPython).toHaveBeenCalledTimes(1);
		expect(mocks.sendToPython).toHaveBeenCalledWith({
			type: "toggle_dictation",
		});
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("callback routes to the dismiss body while transcribing (toggle_dictation + hide)", async () => {
		const { setLastKnownBubbleMode } = await import("../ipc/bubble-handlers");
		const { registerGlobalShortcuts } = await import(
			"../shortcuts/global-shortcuts"
		);

		registerGlobalShortcuts();
		setLastKnownBubbleMode("transcribing");
		registeredCallback()();

		expect(mocks.sendToPython).toHaveBeenCalledTimes(1);
		expect(mocks.sendToPython).toHaveBeenCalledWith({
			type: "toggle_dictation",
		});
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("callback routes to the dismiss body while idle (hide only, no toggle)", async () => {
		const { setLastKnownBubbleMode } = await import("../ipc/bubble-handlers");
		const { registerGlobalShortcuts } = await import(
			"../shortcuts/global-shortcuts"
		);

		registerGlobalShortcuts();
		setLastKnownBubbleMode("idle");
		registeredCallback()();

		expect(mocks.sendToPython).not.toHaveBeenCalled();
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(1);
	});

	it("a second dismiss via the shortcut skips the toggle (idempotency guard)", async () => {
		const { setLastKnownBubbleMode } = await import("../ipc/bubble-handlers");
		const { registerGlobalShortcuts } = await import(
			"../shortcuts/global-shortcuts"
		);

		registerGlobalShortcuts();
		setLastKnownBubbleMode("recording");
		const cb = registeredCallback();
		cb();
		cb();

		expect(mocks.sendToPython).toHaveBeenCalledTimes(1);
		expect(mocks.hideBubbleWindow).toHaveBeenCalledTimes(2);
	});

	it("register returning false logs a warning without throwing", async () => {
		mocks.gsRegister.mockImplementation(() => false);
		const { registerGlobalShortcuts } = await import(
			"../shortcuts/global-shortcuts"
		);

		expect(() => registerGlobalShortcuts()).not.toThrow();
		expect(mocks.gsRegister).toHaveBeenCalledTimes(1);
		expect(mocks.logWarn).toHaveBeenCalledTimes(1);
		const warnText = String(mocks.logWarn.mock.calls[0]?.[0]);
		expect(warnText).toContain("CommandOrControl+Shift+D");
	});

	it("double register is a no-op (no duplicate callbacks)", async () => {
		const { registerGlobalShortcuts } = await import(
			"../shortcuts/global-shortcuts"
		);

		registerGlobalShortcuts();
		registerGlobalShortcuts();

		expect(mocks.gsRegister).toHaveBeenCalledTimes(1);
	});

	it("unregister unregisters exactly the same accelerator; double unregister is a no-op", async () => {
		const { registerGlobalShortcuts, unregisterGlobalShortcuts } = await import(
			"../shortcuts/global-shortcuts"
		);

		unregisterGlobalShortcuts();
		expect(mocks.gsUnregister).not.toHaveBeenCalled();

		registerGlobalShortcuts();
		unregisterGlobalShortcuts();
		unregisterGlobalShortcuts();

		expect(mocks.gsUnregister).toHaveBeenCalledTimes(1);
		expect(mocks.gsUnregister).toHaveBeenCalledWith("CommandOrControl+Shift+D");
	});

	it("re-registering after unregister registers again (resettable lifecycle)", async () => {
		const { registerGlobalShortcuts, unregisterGlobalShortcuts } = await import(
			"../shortcuts/global-shortcuts"
		);

		registerGlobalShortcuts();
		unregisterGlobalShortcuts();
		registerGlobalShortcuts();

		expect(mocks.gsRegister).toHaveBeenCalledTimes(2);
		expect(mocks.gsUnregister).toHaveBeenCalledTimes(1);
	});
});
