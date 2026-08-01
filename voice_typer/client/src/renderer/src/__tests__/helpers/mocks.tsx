/**
 *  — shared renderer test mocks.
 *
 * This file collects the mock factories that recur across the test
 * suite (`window.python`, `window.window_`, `window.bubble`, the
 * `@hugeicons/*` icon stubs, the `sonner` toast spy, etc.) so each test
 * file no longer has to redefine them. The helpers are intentionally
 * side-effect-free on import — tests opt in by calling the installers
 * in `beforeEach` (matching the existing convention in
 * `__tests__/App.test.tsx` and `__tests__/behavior-rewrite/*.test.tsx`).
 *
 * Mocks that need `vi.hoisted` (because they're referenced inside
 * `vi.mock` factory functions that run before top-level imports) still
 * live in the test file itself — `vi.hoisted` cannot be shared across
 * files because it depends on vitest's per-file module graph.
 */
import type { ReactNode } from "react";
import { vi } from "vitest";

// ── Type declarations for the bridge objects the preload injects ────────
// These mirror the surface exposed by `src/preload/index.ts` and
// `src/preload/bubble.ts`. Inlining them here keeps the test helpers
// decoupled from the (electron-gated) preload source.

export interface PythonBridgeMock {
	call: ReturnType<typeof vi.fn>;
	onEvent: ReturnType<typeof vi.fn>;
}

export interface WindowBridgeMock {
	openLogs?: ReturnType<typeof vi.fn>;
	restart?: ReturnType<typeof vi.fn>;
	minimize?: ReturnType<typeof vi.fn>;
	maximize?: ReturnType<typeof vi.fn>;
	close?: ReturnType<typeof vi.fn>;
	isMaximized?: ReturnType<typeof vi.fn>;
	onMaximizeChange?: ReturnType<typeof vi.fn>;
}

export interface BubbleBridgeMock {
	onLevel: ReturnType<typeof vi.fn>;
	onShow: ReturnType<typeof vi.fn>;
	onHide: ReturnType<typeof vi.fn>;
	onSetState: ReturnType<typeof vi.fn>;
	onDraggable: ReturnType<typeof vi.fn>;
	signalReady: ReturnType<typeof vi.fn>;
	hideComplete: ReturnType<typeof vi.fn>;
	resizeTo: ReturnType<typeof vi.fn>;
	moveBy: ReturnType<typeof vi.fn>;
}

// ── Bridge installers ──────────────────────────────────────────────────

/**
 * Install a minimal `window.python` mock so the real `usePython` /
 * `usePythonEvent` hooks (which read `window.python.call` /
 * `window.python.onEvent`) work without the real preload bridge.
 *
 * Returns the mock functions so the caller can wire up
 * `mockCall.mockImplementation(...)` per-test.
 */
export function installPythonBridgeMock(): PythonBridgeMock {
	const bridge: PythonBridgeMock = {
		call: vi.fn(),
		onEvent: vi.fn(() => () => {}),
	};
	(
		window as unknown as {
			python?: PythonBridgeMock;
		}
	).python = bridge;
	return bridge;
}

/** Remove the `window.python` mock installed by `installPythonBridgeMock`. */
export function removePythonBridgeMock(): void {
	delete (window as unknown as { python?: unknown }).python;
}

/**
 * Install stubs for the `window.window_` Electron bridge (used by
 * Settings → "Open Log Folder", restart, window controls, etc.).
 * Pass partial overrides for the methods a test cares about; missing
 * methods default to no-op `vi.fn()`s.
 */
export function installWindowBridgeMock(
	overrides: WindowBridgeMock = {},
): WindowBridgeMock {
	const bridge: Required<WindowBridgeMock> = {
		openLogs:
			overrides.openLogs ?? vi.fn(() => Promise.resolve({ success: true })),
		restart:
			overrides.restart ?? vi.fn(() => Promise.resolve({ success: true })),
		minimize: overrides.minimize ?? vi.fn(),
		maximize: overrides.maximize ?? vi.fn(),
		close: overrides.close ?? vi.fn(),
		isMaximized: overrides.isMaximized ?? vi.fn(() => false),
		onMaximizeChange: overrides.onMaximizeChange ?? vi.fn(() => () => {}),
	};
	(
		window as unknown as {
			window_?: WindowBridgeMock;
		}
	).window_ = bridge;
	return bridge;
}

/** Remove the `window.window_` mock installed by `installWindowBridgeMock`. */
export function removeWindowBridgeMock(): void {
	delete (window as unknown as { window_?: unknown }).window_;
}

/**
 * Build a fresh `window.bubble` mock for the bubble renderer tests.
 * `onDraggable` invokes `cb(true)` so the keyboard-move handler is
 * active by default; tests that need `draggable: false` should call
 * `mock.onDraggable.mockClear()` and re-invoke the callback manually.
 */
export function makeBubbleBridgeMock(): BubbleBridgeMock {
	return {
		onLevel: vi.fn(() => vi.fn()),
		onShow: vi.fn(() => vi.fn()),
		onHide: vi.fn(() => vi.fn()),
		onSetState: vi.fn(() => vi.fn()),
		onDraggable: vi.fn((cb: (draggable: boolean) => void) => {
			cb(true);
			return vi.fn();
		}),
		signalReady: vi.fn(),
		hideComplete: vi.fn(),
		resizeTo: vi.fn(),
		moveBy: vi.fn(),
	};
}

/** Install a `window.bubble` mock and return it for assertions. */
export function installBubbleBridgeMock(): BubbleBridgeMock {
	const mock = makeBubbleBridgeMock();
	(window as unknown as { bubble: BubbleBridgeMock }).bubble = mock;
	return mock;
}

/** Remove the `window.bubble` mock installed by `installBubbleBridgeMock`. */
export function removeBubbleBridgeMock(): void {
	delete (window as unknown as { bubble?: unknown }).bubble;
}

// ── Sonner toast spy ───────────────────────────────────────────────────

/**
 * Build a `sonner` toast spy object suitable for `vi.mock("sonner", ...)`.
 * Each method is a `vi.fn()` so tests can assert on call counts/args.
 */
export function makeToastMock() {
	return {
		success: vi.fn(),
		error: vi.fn(),
		warning: vi.fn(),
		info: vi.fn(),
		dismiss: vi.fn(),
		Toaster: () => null,
	};
}

// ── Icon stub factories ────────────────────────────────────────────────

/**
 * Render a `<span data-testid="hugeicon" data-name={icon?.name}>` for
 * the `@hugeicons/react` `HugeiconsIcon` component. Lets tests assert
 * which icon was rendered via `data-name` without pulling in the real
 * icon runtime.
 */
export function makeHugeiconsReactMock() {
	return {
		HugeiconsIcon: ({
			children,
			icon,
		}: {
			children?: ReactNode;
			icon?: { name?: string };
		}) => (
			<span data-testid="hugeicon" data-name={icon?.name}>
				{children}
			</span>
		),
	};
}

/**
 * Build a `@hugeicons/core-free-icons` mock where every named export is
 * a `{ name }` tagged object. Pass the list of icon names a test needs,
 * or omit to get an empty stub that tests can extend via
 * `vi.mocked(...)` later.
 */
export function makeHugeiconsIconsMock(names: ReadonlyArray<string> = []) {
	const make = (name: string) => ({ name });
	const out: Record<string, { name: string }> = {};
	for (const n of names) out[n] = make(n);
	return out;
}
