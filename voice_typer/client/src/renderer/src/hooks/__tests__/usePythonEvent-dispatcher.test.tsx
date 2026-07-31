/**
 * DJ-89: regression tests for the shared event dispatcher in
 * `hooks/usePython.ts`.
 *
 * Previously each `usePythonEvent` call subscribed to `api.onEvent`
 * directly, creating N subscriptions for N callers. On Tauri, each
 * subscription registers 4 Tauri event listeners (the main
 * `python-event` channel + 3 supervisor relay channels in
 * `python-namespace.ts`), so N callers created 4N Tauri listeners —
 * and every event triggered all 4N callbacks only to be filtered
 * down to the (typically 1) matching caller by the
 * `if (event.type === type)` check.
 *
 * The dispatcher subscribes to `api.onEvent` exactly ONCE (per
 * `window.python` instance) and fan-outs to per-type subscribers
 * stored in a `Map<type, Set<entry>>`. These tests pin:
 *
 *   1. N callers → 1 `onEvent` subscription (the multiplication is
 *      eliminated).
 *   2. The dispatcher fan-outs only to matching-type subscribers.
 *   3. The dispatcher tears down the `onEvent` subscription when the
 *      last subscriber unsubscribes (no dangling listener).
 *   4. The dispatcher re-subscribes if `window.python` is replaced
 *      (e.g. test `afterEach` deletes and re-sets it).
 *   5. The PVT-G5-019 cleanup contract is preserved: the cleanup
 *      returned by the previous handler invocation is run before the
 *      next matching event's handler.
 */
import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePythonEvent } from "@/hooks/usePython";

interface PythonBridgeMock {
	call: ReturnType<typeof vi.fn>;
	onEvent: ReturnType<typeof vi.fn>;
}

describe("DJ-89: usePythonEvent shared dispatcher", () => {
	let original: PythonBridgeMock | undefined;

	beforeEach(() => {
		original = (window as unknown as { python?: PythonBridgeMock }).python;
		// Start each test with the bridge NOT installed so the
		// dispatcher is in a clean state.
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

	function installPythonMock(
		onEventImpl?: (
			cb: (event: { type: string; data?: Record<string, unknown> }) => void,
		) => () => void,
	): {
		mock: PythonBridgeMock;
		captured: {
			cb:
				| ((event: { type: string; data?: Record<string, unknown> }) => void)
				| null;
		};
	} {
		const captured: {
			cb:
				| ((event: { type: string; data?: Record<string, unknown> }) => void)
				| null;
		} = { cb: null };
		const mock: PythonBridgeMock = {
			call: vi.fn(),
			onEvent: vi.fn((cb) => {
				captured.cb = cb;
				return onEventImpl ? onEventImpl(cb) : () => {};
			}),
		};
		(window as unknown as { python: PythonBridgeMock }).python = mock;
		return { mock, captured };
	}

	it("N usePythonEvent callers share 1 onEvent subscription (no N-listener multiplication)", () => {
		const { mock } = installPythonMock();

		// Mount 5 hooks with different event types.
		renderHook(() => usePythonEvent("status_change", () => undefined));
		renderHook(() => usePythonEvent("transcription_final", () => undefined));
		renderHook(() => usePythonEvent("recording_started", () => undefined));
		renderHook(() => usePythonEvent("state_changed", () => undefined));
		renderHook(() => usePythonEvent("mic_level", () => undefined));

		// Despite 5 usePythonEvent callers, `api.onEvent` is
		// called exactly ONCE — the dispatcher holds a single
		// shared subscription.
		expect(mock.onEvent).toHaveBeenCalledTimes(1);
	});

	it("N callers with the SAME event type also share 1 onEvent subscription", () => {
		const { mock } = installPythonMock();

		renderHook(() => usePythonEvent("status_change", () => undefined));
		renderHook(() => usePythonEvent("status_change", () => undefined));
		renderHook(() => usePythonEvent("status_change", () => undefined));

		expect(mock.onEvent).toHaveBeenCalledTimes(1);
	});

	it("dispatcher fan-outs only to matching-type subscribers", () => {
		const { captured } = installPythonMock();

		const handlerStatus = vi.fn(() => undefined);
		const handlerTranscription = vi.fn(() => undefined);
		renderHook(() => usePythonEvent("status_change", handlerStatus));
		renderHook(() =>
			usePythonEvent("transcription_final", handlerTranscription),
		);

		// Dispatch a status_change event.
		captured.cb?.({ type: "status_change", data: { status: "idle" } });
		expect(handlerStatus).toHaveBeenCalledWith({ status: "idle" });
		expect(handlerTranscription).not.toHaveBeenCalled();

		// Dispatch a transcription_final event.
		captured.cb?.({ type: "transcription_final", data: { text: "hello" } });
		expect(handlerTranscription).toHaveBeenCalledWith({ text: "hello" });
		// handlerStatus still called only once.
		expect(handlerStatus).toHaveBeenCalledTimes(1);
	});

	it("dispatcher does not invoke handlers for unsubscribed event types", () => {
		const { captured } = installPythonMock();

		const handler = vi.fn(() => undefined);
		renderHook(() => usePythonEvent("status_change", handler));

		// Dispatch an event with a type no one subscribed to.
		captured.cb?.({ type: "recording_started" });
		expect(handler).not.toHaveBeenCalled();
	});

	it("tears down the onEvent subscription when the last subscriber unmounts", () => {
		const unsubscribe = vi.fn();
		installPythonMock(() => unsubscribe);

		const { unmount } = renderHook(() =>
			usePythonEvent("status_change", () => undefined),
		);

		// The dispatcher subscribed on mount.
		expect(unsubscribe).not.toHaveBeenCalled();

		// Unmount the only subscriber → dispatcher tears down.
		unmount();
		expect(unsubscribe).toHaveBeenCalledTimes(1);
	});

	it("does NOT tear down the subscription while other subscribers remain", () => {
		const unsubscribe = vi.fn();
		installPythonMock(() => unsubscribe);

		const { unmount: unmount1 } = renderHook(() =>
			usePythonEvent("status_change", () => undefined),
		);
		renderHook(() => usePythonEvent("transcription_final", () => undefined));

		// Unmount one of two subscribers → dispatcher stays up.
		unmount1();
		expect(unsubscribe).not.toHaveBeenCalled();
	});

	it("re-subscribes when window.python is replaced (stale subscription torn down)", () => {
		const unsubscribe1 = vi.fn();
		installPythonMock(() => unsubscribe1);

		renderHook(() => usePythonEvent("status_change", () => undefined));
		expect(unsubscribe1).not.toHaveBeenCalled();

		// Simulate a test `afterEach` deleting and re-setting
		// `window.python` with a fresh mock.
		const unsubscribe2 = vi.fn();
		const newMock: PythonBridgeMock = {
			call: vi.fn(),
			onEvent: vi.fn(() => unsubscribe2),
		};
		(window as unknown as { python: PythonBridgeMock }).python = newMock;

		// Mount a new subscriber — the dispatcher detects the
		// instance change, tears down the old subscription, and
		// re-subscribes to the new mock.
		renderHook(() => usePythonEvent("transcription_final", () => undefined));

		expect(unsubscribe1).toHaveBeenCalledTimes(1);
		expect(newMock.onEvent).toHaveBeenCalledTimes(1);
	});

	it("PVT-G5-019: runs the previous handler-returned cleanup before the next matching event", () => {
		const { captured } = installPythonMock();

		const cleanupFn = vi.fn();
		// The handler returns a cleanup function on the first
		// invocation; the dispatcher must run it before the
		// NEXT matching event's handler.
		let callCount = 0;
		const handler = vi.fn(() => {
			callCount += 1;
			if (callCount === 1) return cleanupFn;
			return undefined;
		});

		renderHook(() => usePythonEvent("status_change", handler));

		// First event — handler runs, returns cleanup.
		captured.cb?.({ type: "status_change", data: { status: "idle" } });
		expect(handler).toHaveBeenCalledTimes(1);
		expect(cleanupFn).not.toHaveBeenCalled();

		// Second event — cleanup from the first invocation is
		// run BEFORE the handler.
		captured.cb?.({ type: "status_change", data: { status: "recording" } });
		expect(cleanupFn).toHaveBeenCalledTimes(1);
		expect(handler).toHaveBeenCalledTimes(2);
	});

	it("PVT-G5-019: runs the most recent cleanup on unmount", () => {
		const { captured } = installPythonMock();

		const cleanupFn = vi.fn();
		const handler = vi.fn(() => cleanupFn);

		const { unmount } = renderHook(() =>
			usePythonEvent("status_change", handler),
		);

		// Dispatch one event so a cleanup is registered.
		captured.cb?.({ type: "status_change", data: { status: "idle" } });
		expect(cleanupFn).not.toHaveBeenCalled();

		// Unmount — the most recent cleanup is invoked.
		unmount();
		expect(cleanupFn).toHaveBeenCalledTimes(1);
	});

	it("XZ-R16-05: a throwing handler is caught and does not break subsequent dispatch", () => {
		const { captured } = installPythonMock();

		const err = new Error("handler boom");
		const throwingHandler = vi.fn(() => {
			throw err;
		});
		const consoleError = vi
			.spyOn(console, "error")
			.mockImplementation(() => {});

		renderHook(() => usePythonEvent("status_change", throwingHandler));

		// First event — handler throws.
		expect(() =>
			captured.cb?.({ type: "status_change", data: { status: "idle" } }),
		).not.toThrow();
		expect(throwingHandler).toHaveBeenCalledTimes(1);
		expect(consoleError).toHaveBeenCalledWith(
			"usePythonEvent handler threw:",
			err,
		);

		// Second event — dispatcher still dispatches (the
		// throwing handler didn't kill the subscription).
		captured.cb?.({ type: "status_change", data: { status: "recording" } });
		expect(throwingHandler).toHaveBeenCalledTimes(2);

		consoleError.mockRestore();
	});

	it("preserves the latest handler identity without re-subscribing on every render", () => {
		const { mock, captured } = installPythonMock();

		const handler1 = vi.fn(() => undefined);
		const handler2 = vi.fn(() => undefined);

		const { rerender } = renderHook(
			({ h }) => usePythonEvent("status_change", h),
			{ initialProps: { h: handler1 } },
		);

		expect(mock.onEvent).toHaveBeenCalledTimes(1);

		// Re-render with a new handler identity — the
		// dispatcher must NOT re-subscribe (handlerRef indirection).
		rerender({ h: handler2 });
		expect(mock.onEvent).toHaveBeenCalledTimes(1);

		// The dispatcher invokes the LATEST handler.
		captured.cb?.({ type: "status_change", data: { status: "idle" } });
		expect(handler1).not.toHaveBeenCalled();
		expect(handler2).toHaveBeenCalledWith({ status: "idle" });
	});
});
