/**
 *  regression tests: `useMicrophonePermission` cleanup clears
 * `status.onchange` + removes the `change` event listener.
 *
 * Background
 * ----------
 * Pre-: the cleanup only set `cancelled = true` — it did NOT
 * clear `status.onchange`. The `PermissionStatus` object is owned by
 * the `navigator.permissions` cache and lives for the document
 * lifetime, so the `onchange` closure (which captures
 * `setMicPermission` and `cancelled`) was held until the next mount
 * overwrote it — a bounded single-closure leak per unmount.
 *
 * Post-: the cleanup calls `status.removeEventListener("change",
 * handler)` AND sets `status.onchange = null`. The `cancelled` flag
 * pattern is preserved (guards setState after unmount).
 *
 * These tests verify:
 *   1. `status.onchange` is `null` after unmount.
 *   2. `status.removeEventListener` is called with the same handler
 *      that was registered via `addEventListener`.
 *   3. The `cancelled` flag pattern is preserved (no setState after
 *      unmount).
 */
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useMicrophonePermission } from "../useMicrophonePermission";

// ── Mock PermissionStatus ───────────────────────────────────────────
// jsdom does not implement `navigator.permissions.query`. We provide a
// minimal mock that captures the `addEventListener` / `onchange`
// interactions so the cleanup assertions can verify the listener was
// removed + onchange was cleared.

interface MockPermissionStatus {
	state: "granted" | "denied" | "prompt";
	onchange: (() => void) | null;
	addEventListener: (type: string, cb: () => void) => void;
	removeEventListener: (type: string, cb: () => void) => void;
	// Track registered handlers so the test can assert removeEventListener
	// was called with the same handler.
	_handlers: Map<string, Set<() => void>>;
}

function makeMockPermissionStatus(
	state: "granted" | "denied" | "prompt" = "granted",
): MockPermissionStatus {
	const _handlers = new Map<string, Set<() => void>>();
	return {
		state,
		onchange: null,
		addEventListener: vi.fn((type: string, cb: () => void) => {
			if (!_handlers.has(type)) _handlers.set(type, new Set());
			_handlers.get(type)?.add(cb);
		}),
		removeEventListener: vi.fn((type: string, cb: () => void) => {
			_handlers.get(type)?.delete(cb);
		}),
		_handlers,
	};
}

describe("AB-41: useMicrophonePermission cleanup clears onchange", () => {
	let mockStatus: MockPermissionStatus;
	let queryMock: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		mockStatus = makeMockPermissionStatus("granted");
		queryMock = vi.fn().mockResolvedValue(mockStatus);
		// `navigator.permissions` may not exist in jsdom — define it.
		Object.defineProperty(navigator, "permissions", {
			value: { query: queryMock },
			configurable: true,
			writable: true,
		});
	});

	afterEach(() => {
		cleanup();
		vi.restoreAllMocks();
	});

	it("sets status.onchange = null on unmount", async () => {
		const { unmount } = renderHook(() => useMicrophonePermission());

		// Wait for the async probe to resolve + register the listener.
		await act(async () => {
			await new Promise<void>((resolve) => setTimeout(resolve, 0));
		});

		//defensive: onchange may or may not be set (we use
		// addEventListener as the primary mechanism), but after unmount
		// it MUST be null.
		unmount();

		expect(mockStatus.onchange).toBeNull();
	});

	it("calls removeEventListener with the registered handler on unmount", async () => {
		const { unmount } = renderHook(() => useMicrophonePermission());

		await act(async () => {
			await new Promise<void>((resolve) => setTimeout(resolve, 0));
		});

		// The handler was registered via addEventListener.
		expect(mockStatus._handlers.get("change")?.size ?? 0).toBe(1);
		const registeredHandler = Array.from(
			mockStatus._handlers.get("change") ?? [],
		)[0];

		unmount();

		//removeEventListener must have been called with the
		// same handler that was registered.
		expect(mockStatus.removeEventListener).toHaveBeenCalledWith(
			"change",
			registeredHandler,
		);
		// The handler must have been removed from the internal set.
		expect(mockStatus._handlers.get("change")?.size ?? 0).toBe(0);
	});

	it("preserves the cancelled flag pattern — no setState after unmount", async () => {
		const { result, unmount } = renderHook(() => useMicrophonePermission());

		await act(async () => {
			await new Promise<void>((resolve) => setTimeout(resolve, 0));
		});

		// Initial state should be "granted" (the mock returns granted).
		expect(result.current.micPermission).toBe("granted");

		unmount();

		// Simulate a change event firing AFTER unmount. The handler
		// should have been removed, so dispatching should NOT call
		// any React setState (which would warn about a memory leak).
		mockStatus.state = "denied";
		const spy = vi.spyOn(console, "error").mockImplementation(() => {});
		// Dispatch via the registered handler (if any remain).
		//After  cleanup, no handlers should remain.
		const handlers = mockStatus._handlers.get("change") ?? new Set();
		for (const h of handlers) {
			try {
				h();
			} catch {
				/* ignore */
			}
		}
		// Also fire onchange (should be null after cleanup).
		if (mockStatus.onchange) {
			try {
				mockStatus.onchange();
			} catch {
				/* ignore */
			}
		}

		// No React "Can't perform a React state update on an unmounted
		// component" warning should have fired.
		const reactWarnings = spy.mock.calls.filter(
			(args) =>
				typeof args[0] === "string" && args[0].includes("unmounted component"),
		);
		expect(reactWarnings.length).toBe(0);
		spy.mockRestore();
	});

	it("still updates micPermission when the change event fires while mounted", async () => {
		const { result } = renderHook(() => useMicrophonePermission());

		await act(async () => {
			await new Promise<void>((resolve) => setTimeout(resolve, 0));
		});

		expect(result.current.micPermission).toBe("granted");

		// Simulate a permission change (granted → denied).
		mockStatus.state = "denied";
		act(() => {
			const handlers = mockStatus._handlers.get("change") ?? new Set();
			for (const h of handlers) h();
		});

		//no regression: the addEventListener path still works.
		expect(result.current.micPermission).toBe("denied");
	});
});
