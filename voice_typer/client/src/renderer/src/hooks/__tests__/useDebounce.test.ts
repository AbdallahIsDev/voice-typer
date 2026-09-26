import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
	createDebouncedCallback,
	useDebouncedCallback,
	useDebouncedValue,
} from "@/hooks/useDebounce";

beforeEach(() => {
	vi.useFakeTimers();
});

afterEach(() => {
	vi.useRealTimers();
});

describe("createDebouncedCallback", () => {
	it("coalesces rapid calls into one fire with the latest args", async () => {
		const fn = vi.fn();
		const { debounced } = createDebouncedCallback(fn, 300);
		debounced("a");
		debounced("b");
		debounced("c");
		expect(fn).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(300);
		expect(fn).toHaveBeenCalledOnce();
		expect(fn).toHaveBeenCalledWith("c");
	});

	it("cancel drops the pending fire", async () => {
		const fn = vi.fn();
		const { debounced, cancel } = createDebouncedCallback(fn, 300);
		debounced("a");
		cancel();
		await vi.advanceTimersByTimeAsync(500);
		expect(fn).not.toHaveBeenCalled();
	});

	it("flush fires immediately with no double on timer advance", async () => {
		const fn = vi.fn();
		const { debounced, flush } = createDebouncedCallback(fn, 300);
		debounced("a");
		flush();
		expect(fn).toHaveBeenCalledOnce();
		expect(fn).toHaveBeenCalledWith("a");
		await vi.advanceTimersByTimeAsync(500);
		expect(fn).toHaveBeenCalledOnce();
	});
});

describe("useDebouncedCallback", () => {
	it("fires the latest fn after the delay", async () => {
		const fn = vi.fn();
		const { result } = renderHook(() => useDebouncedCallback(fn, 200));
		act(() => {
			result.current.debounced();
		});
		expect(fn).not.toHaveBeenCalled();
		await act(async () => {
			await vi.advanceTimersByTimeAsync(200);
		});
		expect(fn).toHaveBeenCalledOnce();
	});

	it("cancels the pending fire on unmount", async () => {
		const fn = vi.fn();
		const { result, unmount } = renderHook(() => useDebouncedCallback(fn, 200));
		act(() => {
			result.current.debounced();
		});
		unmount();
		await vi.advanceTimersByTimeAsync(500);
		expect(fn).not.toHaveBeenCalled();
	});
});

describe("useDebouncedValue", () => {
	it("publishes the latest value after the delay", async () => {
		const { result, rerender } = renderHook(
			({ value }: { value: string }) => useDebouncedValue(value, 200),
			{ initialProps: { value: "a" } },
		);
		expect(result.current).toBe("a");
		rerender({ value: "b" });
		rerender({ value: "c" });
		expect(result.current).toBe("a");
		await act(async () => {
			await vi.advanceTimersByTimeAsync(200);
		});
		expect(result.current).toBe("c");
	});
});
