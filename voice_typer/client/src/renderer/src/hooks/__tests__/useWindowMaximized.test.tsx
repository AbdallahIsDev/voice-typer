/**
 * Tests for useWindowMaximized (extracted from App.tsx, EO-28).
 *
 * Contract: query the native bridge on mount, subscribe to
 * onMaximizedChanged, mirror the value onto <html class="is-maximized">,
 * and return the boolean.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useWindowMaximized } from "@/hooks/useWindowMaximized";
import type { WindowBridge } from "@/types/ipc";

function makeBridge() {
	const isMaximized = vi.fn().mockResolvedValue(false);
	const onMaximizedChanged = vi.fn().mockReturnValue(() => {});
	const bridge = {
		isMaximized,
		onMaximizedChanged,
	} as unknown as WindowBridge;
	return { bridge, isMaximized, onMaximizedChanged };
}

beforeEach(() => {
	document.documentElement.classList.remove("is-maximized");
});

afterEach(() => {
	document.documentElement.classList.remove("is-maximized");
	vi.restoreAllMocks();
});

describe("useWindowMaximized", () => {
	it("returns false and does nothing when bridge is undefined", () => {
		const { result } = renderHook(() => useWindowMaximized(undefined));
		expect(result.current).toBe(false);
		expect(
			document.documentElement.classList.contains("is-maximized"),
		).toBe(false);
	});

	it("queries isMaximized on mount and mirrors the result", async () => {
		const { bridge, isMaximized } = makeBridge();
		isMaximized.mockResolvedValue(true);
		renderHook(() => useWindowMaximized(bridge));
		await act(async () => {});
		expect(isMaximized).toHaveBeenCalledTimes(1);
		expect(
			document.documentElement.classList.contains("is-maximized"),
		).toBe(true);
	});

	it("subscribes to onMaximizedChanged and updates state + class", async () => {
		const { bridge, onMaximizedChanged } = makeBridge();
		let listener: ((v: boolean) => void) | undefined;
		onMaximizedChanged.mockImplementation((cb: (v: boolean) => void) => {
			listener = cb;
			return () => {};
		});
		const { result } = renderHook(() => useWindowMaximized(bridge));
		await act(async () => {});
		expect(result.current).toBe(false);

		act(() => {
			listener!(true);
		});
		expect(result.current).toBe(true);
		expect(
			document.documentElement.classList.contains("is-maximized"),
		).toBe(true);

		act(() => {
			listener!(false);
		});
		expect(result.current).toBe(false);
		expect(
			document.documentElement.classList.contains("is-maximized"),
		).toBe(false);
	});

	it("unsubscribes on unmount", async () => {
		const { bridge, onMaximizedChanged } = makeBridge();
		const unsub = vi.fn();
		onMaximizedChanged.mockReturnValue(unsub);
		const { unmount } = renderHook(() => useWindowMaximized(bridge));
		await act(async () => {});
		unmount();
		expect(unsub).toHaveBeenCalledTimes(1);
	});
});
