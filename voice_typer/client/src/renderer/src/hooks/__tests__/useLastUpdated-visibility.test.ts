/**
 * regression tests: `useLastUpdated` visibility-gated interval.
 *
 * Background
 * ----------
 * Pre-fix: `useLastUpdated` ran a 5s `setInterval` unconditionally —
 * even when the tab was hidden. Every mounted page that consumes the
 * hook (Home, History, Models, Microphone, Dashboard) was re-rendered
 * every 5s by the `setNow(Date.now())` tick, even though no one was
 * looking at the "Xs ago" label. Browsers throttle hidden-tab
 * intervals to ~1 Hz but don't pause them, so the ticks (and the
 * setState calls) kept firing.
 *
 * Post-fix: the hook registers a `visibilitychange` listener
 * that CLEARS the interval (`clearInterval`) when the tab becomes
 * hidden and RE-ARMS it when the tab becomes visible again. No ticks
 * fire while hidden — no setState, no re-render, no reconciliation.
 *
 * These tests verify:
 *   1. `clearInterval` IS called when the tab becomes hidden.
 *   2. A new `setInterval` IS armed when the tab becomes visible again.
 *   3. The `visibilitychange` listener IS removed on unmount (no leak).
 *   4. The interval is NOT armed at mount when the tab starts hidden.
 *
 * NOTE: these tests use REAL timers (not `vi.useFakeTimers()`) because
 * `vi.useFakeTimers()` replaces `window.setInterval` / `clearInterval`
 * with fakes AFTER `vi.spyOn` wraps them — the spy would be bypassed.
 * The 5s interval is long enough that it won't fire during the
 * sub-second test, so real timers are safe.
 */
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useLastUpdated } from "@/hooks/useLastUpdated";

// ── Helpers ─────────────────────────────────────────────────────────
//
// `document.visibilityState` is a read-only property in jsdom — we
// override it via `Object.defineProperty` so the test can flip it
// between "visible" and "hidden". The `visibilitychange` event is
// dispatched via `document.dispatchEvent` after flipping the property.

function setVisibility(state: "visible" | "hidden") {
	Object.defineProperty(document, "visibilityState", {
		configurable: true,
		get: () => state,
	});
}

function dispatchVisibilityChange() {
	act(() => {
		document.dispatchEvent(new Event("visibilitychange"));
	});
}

beforeEach(() => {
	// Default: tab is visible.
	setVisibility("visible");
});

afterEach(() => {
	cleanup();
	vi.restoreAllMocks();
	// Restore visibilityState to the jsdom default ("visible").
	setVisibility("visible");
});

describe("useLastUpdated visibility-gated interval", () => {
	it("calls clearInterval when the tab becomes hidden", () => {
		const clearSpy = vi.spyOn(window, "clearInterval");

		renderHook(() => useLastUpdated());

		// The interval was armed on mount (tab is visible). Reset the
		// spy so we count ONLY post-hide clear calls.
		clearSpy.mockClear();

		// Hide the tab → the `visibilitychange` listener should call
		// `clearInterval`.
		setVisibility("hidden");
		dispatchVisibilityChange();

		expect(clearSpy).toHaveBeenCalledTimes(1);

		clearSpy.mockRestore();
	});

	it("re-arms a new setInterval when the tab becomes visible again", () => {
		const setSpy = vi.spyOn(window, "setInterval");
		const clearSpy = vi.spyOn(window, "clearInterval");

		renderHook(() => useLastUpdated());

		// 1 setInterval call on mount (initial arm).
		expect(setSpy).toHaveBeenCalledTimes(1);
		setSpy.mockClear();

		// Hide → clear.
		setVisibility("hidden");
		dispatchVisibilityChange();
		expect(clearSpy).toHaveBeenCalledTimes(1);
		clearSpy.mockClear();

		// No new setInterval should have been armed on hide.
		expect(setSpy).toHaveBeenCalledTimes(0);

		// Show → re-arm. A NEW setInterval should be created.
		setVisibility("visible");
		dispatchVisibilityChange();

		expect(setSpy).toHaveBeenCalledTimes(1);

		setSpy.mockRestore();
		clearSpy.mockRestore();
	});

	it("removes the visibilitychange listener on unmount (no leak)", () => {
		const addSpy = vi.spyOn(document, "addEventListener");
		const removeSpy = vi.spyOn(document, "removeEventListener");

		const { unmount } = renderHook(() => useLastUpdated());

		// The hook registered a `visibilitychange` listener on mount.
		const visibilityAddCalls = addSpy.mock.calls.filter(
			([type]) => type === "visibilitychange",
		);
		expect(visibilityAddCalls.length).toBe(1);

		unmount();

		// The hook removed the same listener on unmount.
		const visibilityRemoveCalls = removeSpy.mock.calls.filter(
			([type]) => type === "visibilitychange",
		);
		expect(visibilityRemoveCalls.length).toBe(1);

		// The removed listener must be the same function that was
		// registered (no anonymous-arrow leak).
		const registered = visibilityAddCalls[0]?.[1];
		const removed = visibilityRemoveCalls[0]?.[1];
		expect(removed).toBe(registered);

		addSpy.mockRestore();
		removeSpy.mockRestore();
	});

	it("does NOT arm the interval at mount when the tab starts hidden", () => {
		const setSpy = vi.spyOn(window, "setInterval");

		// Tab is hidden at mount.
		setVisibility("hidden");

		renderHook(() => useLastUpdated());

		// No setInterval should have been armed (the initial `arm()`
		// call is gated on `document.visibilityState === "visible"`).
		expect(setSpy).toHaveBeenCalledTimes(0);

		setSpy.mockRestore();
	});
});
