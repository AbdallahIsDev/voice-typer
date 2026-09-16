/**
 * Tests for `useRendererHeartbeat` (MO-113).
 *
 * The hook feeds the host's webview watchdog: beats must flow while the
 * document is VISIBLE, must stop while hidden (engine-level background
 * throttling would otherwise read as a false stall), and must resume on
 * the next visibility change.
 */
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
	_heartbeatForTests,
	HEARTBEAT_INTERVAL_MS,
	useRendererHeartbeat,
} from "@/hooks/useRendererHeartbeat";

function setVisibility(state: DocumentVisibilityState): void {
	Object.defineProperty(document, "visibilityState", {
		configurable: true,
		get: () => state,
	});
}

let sendSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
	vi.useFakeTimers();
	setVisibility("visible");
	sendSpy = vi
		.spyOn(_heartbeatForTests, "send")
		.mockReturnValue(Promise.resolve());
});

afterEach(() => {
	vi.useRealTimers();
	sendSpy.mockRestore();
});

describe("useRendererHeartbeat", () => {
	it("beats immediately on mount and then on the interval", () => {
		renderHook(() => useRendererHeartbeat());

		expect(sendSpy).toHaveBeenCalledTimes(1);
		vi.advanceTimersByTime(HEARTBEAT_INTERVAL_MS);
		expect(sendSpy).toHaveBeenCalledTimes(2);
		vi.advanceTimersByTime(HEARTBEAT_INTERVAL_MS * 2);
		expect(sendSpy).toHaveBeenCalledTimes(4);
	});

	it("does not beat while the document is hidden", () => {
		setVisibility("hidden");
		renderHook(() => useRendererHeartbeat());

		expect(sendSpy).not.toHaveBeenCalled();
		vi.advanceTimersByTime(HEARTBEAT_INTERVAL_MS * 3);
		expect(sendSpy).not.toHaveBeenCalled();
	});

	it("stops when the window is hidden and resumes when visible again", () => {
		renderHook(() => useRendererHeartbeat());
		expect(sendSpy).toHaveBeenCalledTimes(1);

		setVisibility("hidden");
		document.dispatchEvent(new Event("visibilitychange"));
		vi.advanceTimersByTime(HEARTBEAT_INTERVAL_MS * 2);
		expect(sendSpy).toHaveBeenCalledTimes(1);

		setVisibility("visible");
		document.dispatchEvent(new Event("visibilitychange"));
		// Resume beats immediately (no waiting for the next tick).
		expect(sendSpy).toHaveBeenCalledTimes(2);
	});

	it("stops beating on unmount", () => {
		const { unmount } = renderHook(() => useRendererHeartbeat());
		unmount();
		vi.advanceTimersByTime(HEARTBEAT_INTERVAL_MS * 3);
		expect(sendSpy).toHaveBeenCalledTimes(1);
	});

	it("never throws when the bridge is absent", () => {
		sendSpy.mockReturnValue(undefined);
		expect(() => {
			renderHook(() => useRendererHeartbeat());
			vi.advanceTimersByTime(HEARTBEAT_INTERVAL_MS);
		}).not.toThrow();
	});
});
