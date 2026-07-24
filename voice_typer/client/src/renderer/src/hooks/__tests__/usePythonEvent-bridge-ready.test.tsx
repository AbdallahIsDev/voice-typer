/**
 * Regression test for CR-6: `usePythonEvent` must (re-)subscribe to the
 * Python event stream when `window.python` becomes available AFTER the
 * hook has already mounted.
 *
 * Previously the effect's only dependency was `[type]`, and the effect
 * body early-returned when `window.python` was undefined. So if the
 * bridge was installed late (e.g. by the Tauri `installTauriBridge()`
 * auto-install on first import, or by the Electron preload under slow
 * HMR), the subscription was never re-attempted and events were
 * silently dropped for the entire session.
 *
 * The fix adds a `useBridgeReady()` hook (backed by
 * `useSyncExternalStore`) that polls `window.python` presence every
 * 100ms. Including `bridgeReady` in the effect's dependency array
 * causes the effect to re-run when the bridge comes online, so the
 * subscription is created lazily on first bridge availability.
 */
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useBridgeReady, usePythonEvent } from "@/hooks/usePython";

// Shape of the `window.python` namespace the hook reads. We install a
// minimal mock on `window.python` per test and tear it down after.
interface PythonBridgeMock {
	call: ReturnType<typeof vi.fn>;
	onEvent: ReturnType<typeof vi.fn>;
}

describe("CR-6: useBridgeReady + usePythonEvent lazy subscription", () => {
	let original: PythonBridgeMock | undefined;

	beforeEach(() => {
		original = (window as unknown as { python?: PythonBridgeMock }).python;
		// Start each test with the bridge NOT installed — simulates
		// the renderer mounting before the preload / Tauri installer
		// has had a chance to set window.python.
		delete (window as unknown as { python?: PythonBridgeMock }).python;
	});

	afterEach(() => {
		const w = window as unknown as { python?: PythonBridgeMock };
		if (original === undefined) {
			delete w.python;
		} else {
			w.python = original;
		}
		cleanup();
		vi.restoreAllMocks();
	});

	it("useBridgeReady returns false when window.python is undefined at mount", () => {
		const { result } = renderHook(() => useBridgeReady());
		expect(result.current).toBe(false);
	});

	it("useBridgeReady returns true when window.python is already set at mount", () => {
		(window as unknown as { python: PythonBridgeMock }).python = {
			call: vi.fn(),
			onEvent: vi.fn(() => () => {}),
		};
		const { result } = renderHook(() => useBridgeReady());
		expect(result.current).toBe(true);
	});

	it("usePythonEvent creates NO subscription when window.python is undefined at mount", () => {
		const onEvent = vi.fn(() => () => {});
		// GT-78: `usePythonEvent` is now generic over
		// `PythonPushEvent["type"]`. Use the real `"state_changed"`
		// variant (was previously the non-existent `"status_changed"`
		// string — a typo the old `type: string` signature silently
		// accepted, causing the subscription to never fire in
		// production). The handler takes no args; `state_changed`'s
		// data is `Record<string, unknown>` (ignored here).
		renderHook(() => usePythonEvent("state_changed", () => undefined));

		// The effect should have early-returned without calling onEvent.
		expect(onEvent).not.toHaveBeenCalled();
	});

	it("CR-6 core: usePythonEvent subscribes AFTER window.python becomes available post-mount", async () => {
		const onEvent = vi.fn(() => () => {});
		// Track the unsubscribe so we can assert it's called on cleanup.
		const unsubscribe = vi.fn();
		onEvent.mockReturnValue(unsubscribe);

		// Mount with window.python undefined — effect early-returns,
		// useBridgeReady starts polling.
		renderHook(() => usePythonEvent("state_changed", () => {}));

		// No subscription yet — bridge not installed.
		expect(onEvent).not.toHaveBeenCalled();

		// Simulate the bridge becoming available after ~50ms (e.g. the
		// Tauri `installTauriBridge()` finishing its async setup, or
		// the Electron preload completing under slow HMR).
		setTimeout(() => {
			(window as unknown as { python: PythonBridgeMock }).python = {
				call: vi.fn(),
				onEvent,
			};
		}, 50);

		// The useBridgeReady poll (100ms interval) should detect the
		// bridge within ~150ms of mount, fire its callback, trigger a
		// re-render (bridgeReady false → true), and the effect should
		// re-run and call api.onEvent(...).
		await waitFor(
			() => {
				expect(onEvent).toHaveBeenCalledTimes(1);
			},
			{ timeout: 2_000 },
		);

		// The onEvent callback was registered with a function that
		// filters by event.type === "state_changed".
		expect(onEvent).toHaveBeenCalledWith(expect.any(Function));
	});

	it("CR-6: usePythonEvent does NOT subscribe if window.python never becomes available", async () => {
		const onEvent = vi.fn(() => () => {});

		renderHook(() => usePythonEvent("state_changed", () => {}));

		// Wait long enough that the poll would have fired multiple
		// times (3x the 100ms interval).
		await new Promise((r) => setTimeout(r, 350));

		// Still no subscription — bridge never came up.
		expect(onEvent).not.toHaveBeenCalled();
	});

	it("CR-6: handler receives events dispatched after the lazy subscription", async () => {
		// Captures the onEvent callback so the test can dispatch a
		// synthetic event to it. Wrapped in an object so TS doesn't
		// narrow it to `null` (assignments inside the vi.fn closure
		// aren't tracked by control-flow analysis).
		type Cb = (event: { type: string; data?: Record<string, unknown> }) => void;
		const captured: { cb: Cb | null } = { cb: null };
		const onEvent = vi.fn((cb: Cb) => {
			captured.cb = cb;
			return () => {};
		});

		const handler = vi.fn((_data?: Record<string, unknown>) => undefined);
		renderHook(() => usePythonEvent("state_changed", handler));

		// Install the bridge after 50ms.
		setTimeout(() => {
			(window as unknown as { python: PythonBridgeMock }).python = {
				call: vi.fn(),
				onEvent,
			};
		}, 50);

		// Wait for the lazy subscription to be created.
		await waitFor(
			() => {
				expect(onEvent).toHaveBeenCalledTimes(1);
			},
			{ timeout: 2_000 },
		);

		// Dispatch a synthetic event of the matching type. The runtime
		// shape (`{type, data}`) matches `StateChangedEvent` even
		// though the Cb type is the structural `{type: string; data?:
		// Record<string, unknown>}` widening (the `as` cast on the
		// hook side mirrors what the real bridge does).
		expect(captured.cb).not.toBeNull();
		captured.cb?.({ type: "state_changed", data: { status: "recording" } });

		// The handler should have been called with the event data.
		await waitFor(() => {
			expect(handler).toHaveBeenCalledWith({ status: "recording" });
		});
	});
});
