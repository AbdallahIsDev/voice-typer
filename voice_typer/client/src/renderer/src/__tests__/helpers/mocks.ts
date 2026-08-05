/**
 * Shared mock installers for the renderer vitest suite.
 *
 * Before this file existed, 7+ test files each declared their own
 * `makeMockBubble()` factory (`Bubble.test.tsx`, `Bubble-keyboard-move.test.tsx`,
 * `Bubble-axe.test.tsx`, `Bubble-transcript.test.tsx`, `bubble_rAF_pause.test.tsx`,
 * `bubble-raf-gating.test.tsx`, ...). When `window.bubble`'s API surface
 * changed (e.g. when `onConfig` / `toggleDictation` / `dismiss` were added),
 * every copy went stale until a test failed for an unrelated reason.
 *
 * `installBubbleBridgeMock()` collapses those copies into a single source
 * of truth. It mirrors the most complete existing factory (the one in
 * `Bubble.test.tsx`) so the existing call sites can be migrated one at a
 * time without behaviour drift. The returned object exposes the listener
 * bookkeeping via `_listeners` so tests that need to fire `bubble:config`
 * or `bubble:show` events can do so via the same `_listeners.config` /
 * `_listeners.show` accessors they already use today.
 *
 * This file is intended to be imported directly by tests — its only side
 * effect is overwriting `window.bubble`, which is the explicit purpose of
 * the function. Tests are responsible for calling it inside `beforeEach`
 * (so each test gets a fresh mock) and for cleaning up via `afterEach`
 * (or by relying on the centralised `cleanup()` in `test-setup.ts`).
 */
import { vi } from "vitest";

/**
 * The mock `window.bubble` bridge installed by `installBubbleBridgeMock`.
 *
 * Every method is a `vi.fn()` so tests can assert on call counts / args.
 * The `onLevel` / `onShow` / `onHide` / `onSetState` / `onDraggable` /
 * `onConfig` subscribers each return a `vi.fn()` unsubscribe stub (the
 * real Bubble.tsx subscribes at module-load time and would crash on
 * `undefined` returns).
 */
export interface MockBubbleBridge {
	onLevel: ReturnType<typeof vi.fn>;
	onShow: ReturnType<typeof vi.fn>;
	onHide: ReturnType<typeof vi.fn>;
	onSetState: ReturnType<typeof vi.fn>;
	onDraggable: ReturnType<typeof vi.fn>;
	onConfig: ReturnType<typeof vi.fn>;
	signalReady: ReturnType<typeof vi.fn>;
	hideComplete: ReturnType<typeof vi.fn>;
	resizeTo: ReturnType<typeof vi.fn>;
	moveBy: ReturnType<typeof vi.fn>;
	toggleDictation: ReturnType<typeof vi.fn>;
	dismiss: ReturnType<typeof vi.fn>;
	/**
	 * Internal listener bookkeeping — exposed so tests can simulate
	 * backend events (`bubble:config`, `bubble:show`, `bubble:hide`,
	 * `bubble:set-state`) by invoking the registered callback. This
	 * mirrors the `_listeners` field on the local `makeMockBubble()`
	 * factories that already exist in `Bubble.test.tsx` et al.
	 */
	_listeners: {
		show: Array<() => void>;
		hide: Array<() => void>;
		setState: Array<(state: string) => void>;
		config?: (cfg: Record<string, unknown>) => void;
	};
}

/**
 * Build a `vi.fn()`-instrumented `window.bubble` and overwrite the
 * global. Returns the mock so the caller can assert on it.
 *
 * Typical usage:
 *
 *   let bubble: MockBubbleBridge;
 *   beforeEach(() => { bubble = installBubbleBridgeMock(); });
 *
 * The mock OVERWRITES the no-op default installed by `test-setup.ts`
 * (see the comment block above the `window.bubble` default stub there).
 * Tests that don't care about bubble behaviour can simply NOT call
 * this function — the default stub keeps components mounting.
 */
export function installBubbleBridgeMock(): MockBubbleBridge {
	const listeners: MockBubbleBridge["_listeners"] = {
		show: [],
		hide: [],
		setState: [],
	};
	const mock: MockBubbleBridge = {
		onLevel: vi.fn(() => vi.fn()),
		onShow: vi.fn((cb: () => void) => {
			listeners.show.push(cb);
			return () => {
				listeners.show = listeners.show.filter((l) => l !== cb);
			};
		}),
		onHide: vi.fn((cb: () => void) => {
			listeners.hide.push(cb);
			return () => {
				listeners.hide = listeners.hide.filter((l) => l !== cb);
			};
		}),
		onSetState: vi.fn((cb: (state: string) => void) => {
			listeners.setState.push(cb);
			return () => {
				listeners.setState = listeners.setState.filter((l) => l !== cb);
			};
		}),
		onDraggable: vi.fn(() => vi.fn()),
		// Bubble config + mic-button toggle (sandboxed renderer).
		onConfig: vi.fn((cb: (cfg: Record<string, unknown>) => void) => {
			listeners.config = cb;
			return () => {
				listeners.config = undefined;
			};
		}),
		signalReady: vi.fn(),
		hideComplete: vi.fn(),
		resizeTo: vi.fn(),
		moveBy: vi.fn(),
		toggleDictation: vi.fn(),
		// dismiss IPC send (sandboxed renderer).
		dismiss: vi.fn(),
		_listeners: listeners,
	};
	(window as unknown as Record<string, unknown>).bubble = mock;
	return mock;
}

/**
 * Remove the mock installed by `installBubbleBridgeMock`.
 *
 * Tests that want a hard reset between cases can call this in
 * `afterEach` to delete `window.bubble` entirely. Tests that re-install
 * on every `beforeEach` don't need this — the next install overwrites
 * the previous mock. Provided for parity with the existing local
 * `afterEach(() => { delete (window as ...).bubble; })` pattern in
 * `Bubble.test.tsx` / `Bubble-keyboard-move.test.tsx`.
 */
export function uninstallBubbleBridgeMock(): void {
	delete (window as unknown as Record<string, unknown>).bubble;
}
