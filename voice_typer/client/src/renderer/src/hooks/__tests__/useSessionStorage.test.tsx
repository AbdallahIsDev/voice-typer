/**
 * Unit tests for `useSessionStorage`.
 *
 * Contract under test:
 *   - returns the initial value when nothing is stored under the key
 *   - `setValue` (plain value) updates state AND persists JSON under the key
 *   - `setValue` (updater function) derives from the previous value
 *   - a previously stored JSON blob hydrates as the state on first mount
 *   - a corrupt stored blob falls back to the initial value (non-fatal)
 *   - NO `storage` event listener is registered: the window `storage`
 *     event does not fire cross-tab for `sessionStorage` writes (MDN),
 *     so the previous cross-tab sync listener was dead code, removed.
 *     These tests pin its absence so it cannot quietly return.
 *
 * Storage event semantics note: dispatching a synthetic `storage`
 * event at the window must NOT change the hook state, there is no
 * listener, and even a real same-document `sessionStorage` write never
 * dispatches one to the writing document.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useSessionStorage } from "@/hooks/useSessionStorage";

describe("useSessionStorage, state + persistence", () => {
	beforeEach(() => {
		sessionStorage.clear();
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("returns the initial value when nothing is stored", () => {
		const { result } = renderHook(() =>
			useSessionStorage("k:init", "fallback"),
		);
		expect(result.current[0]).toBe("fallback");
	});

	it("setValue (plain value) updates state and persists JSON under the key", () => {
		const { result } = renderHook(() => useSessionStorage("k:plain", "a"));
		act(() => {
			result.current[1]("b");
		});
		expect(result.current[0]).toBe("b");
		expect(sessionStorage.getItem("k:plain")).toBe(JSON.stringify("b"));
	});

	it("setValue (updater function) derives from the previous value", () => {
		const { result } = renderHook(() => useSessionStorage("k:updater", 1));
		act(() => {
			result.current[1]((prev) => prev + 41);
		});
		expect(result.current[0]).toBe(42);
		expect(sessionStorage.getItem("k:updater")).toBe("42");
	});

	it("hydrates the stored JSON blob as the initial state", () => {
		sessionStorage.setItem(
			"k:hydrate",
			JSON.stringify({ filter: "favorites" }),
		);
		const { result } = renderHook(() =>
			useSessionStorage("k:hydrate", { filter: "all" }),
		);
		expect(result.current[0]).toEqual({ filter: "favorites" });
	});

	it("falls back to the initial value on a corrupt stored blob (non-fatal)", () => {
		sessionStorage.setItem("k:corrupt", "{not valid json");
		const { result } = renderHook(() => useSessionStorage("k:corrupt", "safe"));
		expect(result.current[0]).toBe("safe");
	});

	it("persisted write failure (storage unavailable) is non-fatal, state still updates", () => {
		// jsdom's Storage defines setItem on the prototype; the in-memory
		// fallback from test-setup.ts is a plain object, spy on whichever
		// target actually provides the methods (same dual-target pattern
		// as lib/__tests__/theme-draft-storage.test.ts).
		const storageTarget: Storage =
			sessionStorage instanceof Storage ? Storage.prototype : sessionStorage;
		const setItemSpy = vi
			.spyOn(storageTarget, "setItem")
			.mockImplementation(() => {
				throw new Error("QuotaExceededError");
			});
		const { result } = renderHook(() => useSessionStorage("k:quota", "before"));
		act(() => {
			result.current[1]("after");
		});
		expect(result.current[0]).toBe("after");
		setItemSpy.mockRestore();
	});
});

describe("useSessionStorage, no storage-event listener (cross-tab sync was dead code)", () => {
	beforeEach(() => {
		sessionStorage.clear();
	});

	it("registers no `storage` event listener on the window", () => {
		const addSpy = vi.spyOn(window, "addEventListener");
		renderHook(() => useSessionStorage("k:nolisten", "v"));
		const storageCalls = addSpy.mock.calls.filter(
			([type]) => type === "storage",
		);
		expect(
			storageCalls,
			"sessionStorage storage events never fire cross-tab (MDN), the hook must not register a listener",
		).toEqual([]);
		addSpy.mockRestore();
	});

	it("dispatching a `storage` event does not change the hook state", () => {
		const { result } = renderHook(() =>
			useSessionStorage("k:ignore", "original"),
		);
		act(() => {
			window.dispatchEvent(
				new StorageEvent("storage", {
					key: "k:ignore",
					newValue: JSON.stringify("sneaky-cross-tab-write"),
				}),
			);
		});
		expect(result.current[0]).toBe("original");
	});
});
