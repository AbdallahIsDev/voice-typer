// @vitest-environment node
/**
 * unit tests for `main/windows/theme-listener.ts` — the standalone
 * module extracted from `main-window.ts` that owns the single
 * `nativeTheme.on("updated", ...)` listener for the dashboard window's
 * taskbar icon.
 *
 * The module exposes three exports:
 *   • `registerNativeThemeListener()` — idempotent registration.
 *   • `_resetNativeThemeListenerForTest()` — test-only teardown.
 *   • `_nativeThemeListenerRegistered()` — test-only predicate.
 *
 * Behaviour under test:
 *   1. Emitting a `"updated"` event after registration calls
 *      `state.mainWindow.setIcon(...)` with the LIGHT icon path when
 *      `nativeTheme.shouldUseDarkColors === false`.
 *   2. Emitting a `"updated"` event with `shouldUseDarkColors === true`
 *      selects the DARK icon path.
 *   3. The handler is a no-op when `state.mainWindow.isDestroyed()`
 *      returns true (stale-closure guard from R6-F3).
 *   4. The handler is a no-op when `state.mainWindow` is null.
 *   5. Idempotent registration: calling `registerNativeThemeListener()`
 *      N times still leaves exactly ONE listener attached.
 *   6. `_resetNativeThemeListenerForTest()` removes the listener and
 *      flips the predicate back to false.
 *
 * NOTE: the listener sets the taskbar icon via `mainWindow.setIcon(...)` —
 * it does NOT call `mainWindow.webContents.send(channel, ...)` (the
 * dashboard renderer reads the theme via its own `useTheme` hook, not via
 * a main→renderer IPC push). The task spec mentioned asserting on
 * `webContents.send`, but the actual implementation calls `setIcon`; we
 * pin the real contract here.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// ── Mock state (hoisted so vi.mock factories can reference them) ─────
// vi.mock factories are hoisted to the top of the file by vitest and run
// before any other code, so any variable they close over must itself be
// hoisted via vi.hoisted().
const {
	nativeThemeListeners,
	mockNativeThemeOn,
	mockNativeThemeOff,
	mockShouldUseDarkColorsRef,
	mockMainWindow,
	mockState,
} = vi.hoisted(() => {
	// Listener registry: captures every handler passed to `nativeTheme.on`.
	const listeners: Array<() => void> = [];
	const on = vi.fn((event: string, handler: () => void) => {
		if (event === "updated") listeners.push(handler);
	});
	const off = vi.fn((event: string, handler: () => void) => {
		if (event === "updated") {
			const idx = listeners.indexOf(handler);
			if (idx >= 0) listeners.splice(idx, 1);
		}
	});
	// `shouldUseDarkColors` is read at handler-invocation time, so tests
	// flip `current` before firing the listener.
	const darkRef = { current: false };
	const win = {
		setIcon: vi.fn(),
		isDestroyed: vi.fn(() => false),
	};
	const state = { mainWindow: null as typeof win | null };
	return {
		nativeThemeListeners: listeners,
		mockNativeThemeOn: on,
		mockNativeThemeOff: off,
		mockShouldUseDarkColorsRef: darkRef,
		mockMainWindow: win,
		mockState: state,
	};
});

vi.mock("electron", () => ({
	nativeTheme: {
		get shouldUseDarkColors() {
			return mockShouldUseDarkColorsRef.current;
		},
		on: mockNativeThemeOn,
		off: mockNativeThemeOff,
	},
}));

vi.mock("../../state", () => ({
	state: mockState,
}));

// ── Import AFTER mocks so the module picks up the mocked electron ────
import {
	_nativeThemeListenerRegistered,
	_resetNativeThemeListenerForTest,
	registerNativeThemeListener,
} from "../theme-listener";

/** Fire every registered `"updated"` listener (typically just one). */
function emitNativeThemeUpdated(): void {
	for (const fn of nativeThemeListeners) fn();
}

describe("theme-listener — nativeTheme 'updated' → mainWindow.setIcon", () => {
	beforeEach(() => {
		// clearMocks in vitest.config.ts already clears call history,
		// but we additionally reset the listener registry + dark flag
		// because module-scope arrays aren't tracked by clearMocks.
		nativeThemeListeners.length = 0;
		mockShouldUseDarkColorsRef.current = false;
		mockMainWindow.setIcon.mockClear();
		mockMainWindow.isDestroyed.mockReset();
		mockMainWindow.isDestroyed.mockImplementation(() => false);
		mockState.mainWindow = mockMainWindow;
		// Reset module state so each test starts unregistered.
		_resetNativeThemeListenerForTest();
	});

	it("registers exactly one listener on first call", () => {
		expect(_nativeThemeListenerRegistered()).toBe(false);
		registerNativeThemeListener();
		expect(_nativeThemeListenerRegistered()).toBe(true);
		expect(nativeThemeListeners.length).toBe(1);
	});

	it("is idempotent — calling N times still leaves exactly one listener", () => {
		for (let i = 0; i < 5; i++) {
			registerNativeThemeListener();
		}
		expect(nativeThemeListeners.length).toBe(1);
		expect(_nativeThemeListenerRegistered()).toBe(true);
	});

	it("emitting 'updated' selects the LIGHT icon when shouldUseDarkColors=false", () => {
		registerNativeThemeListener();
		mockShouldUseDarkColorsRef.current = false;

		emitNativeThemeUpdated();

		expect(mockMainWindow.setIcon).toHaveBeenCalledTimes(1);
		const path = mockMainWindow.setIcon.mock.calls[0]?.[0] as string;
		expect(path).toContain("icon.png");
		expect(path).not.toContain("icon-dark.png");
	});

	it("emitting 'updated' selects the DARK icon when shouldUseDarkColors=true", () => {
		registerNativeThemeListener();
		mockShouldUseDarkColorsRef.current = true;

		emitNativeThemeUpdated();

		expect(mockMainWindow.setIcon).toHaveBeenCalledTimes(1);
		const path = mockMainWindow.setIcon.mock.calls[0]?.[0] as string;
		expect(path).toContain("icon-dark.png");
	});

	it("reads shouldUseDarkColors LIVE (no stale closure capture at registration)", () => {
		// Register while dark=false (light icon expected if captured).
		registerNativeThemeListener();
		// Flip to dark AFTER registration but BEFORE the event fires.
		mockShouldUseDarkColorsRef.current = true;

		emitNativeThemeUpdated();

		expect(mockMainWindow.setIcon).toHaveBeenCalledTimes(1);
		const path = mockMainWindow.setIcon.mock.calls[0]?.[0] as string;
		expect(path).toContain("icon-dark.png");
	});

	it("reads state.mainWindow LIVE (post-registration install is observed)", () => {
		// Start with no window installed at registration time.
		mockState.mainWindow = null;
		registerNativeThemeListener();

		// Install a fresh window AFTER registration.
		mockState.mainWindow = mockMainWindow;
		emitNativeThemeUpdated();

		expect(mockMainWindow.setIcon).toHaveBeenCalledTimes(1);
	});

	it("is a no-op when state.mainWindow is null", () => {
		mockState.mainWindow = null;
		registerNativeThemeListener();

		expect(() => emitNativeThemeUpdated()).not.toThrow();
		expect(mockMainWindow.setIcon).not.toHaveBeenCalled();
	});

	it("is a no-op when state.mainWindow.isDestroyed() returns true", () => {
		registerNativeThemeListener();
		mockMainWindow.isDestroyed.mockImplementation(() => true);

		emitNativeThemeUpdated();

		expect(mockMainWindow.setIcon).not.toHaveBeenCalled();
	});

	it("_resetNativeThemeListenerForTest removes the listener via nativeTheme.off", () => {
		registerNativeThemeListener();
		expect(nativeThemeListeners.length).toBe(1);

		_resetNativeThemeListenerForTest();

		expect(nativeThemeListeners.length).toBe(0);
		expect(_nativeThemeListenerRegistered()).toBe(false);
		expect(mockNativeThemeOff).toHaveBeenCalledWith(
			"updated",
			expect.any(Function),
		);
	});

	it("survives multiple register→reset cycles (re-registration after reset works)", () => {
		// Cycle 1.
		registerNativeThemeListener();
		expect(nativeThemeListeners.length).toBe(1);
		_resetNativeThemeListenerForTest();
		expect(nativeThemeListeners.length).toBe(0);

		// Cycle 2 — the module-level handler closure must have been
		// nulled by the prior reset, otherwise registerNativeThemeListener
		// would early-return and the listener count would stay 0.
		registerNativeThemeListener();
		expect(nativeThemeListeners.length).toBe(1);
		expect(_nativeThemeListenerRegistered()).toBe(true);

		// And the re-registered handler still fires correctly.
		emitNativeThemeUpdated();
		expect(mockMainWindow.setIcon).toHaveBeenCalledTimes(1);
	});
});
