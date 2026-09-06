/**
 * Tests for useDownloadProgressEvent (extracted from Home.tsx).
 *
 * Contract: subscribe to `download_progress` pushes, keep only valid
 * 0-100 percentages, reset to null whenever the recording state leaves
 * "loading" so a stale progress bar can never linger.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useDownloadProgressEvent } from "@/pages/home/hooks/useDownloadProgressEvent";
import type { RecordingState } from "@/types/ipc";

// Capture the handler usePythonEvent registers so we can fire it.
const registered = new Map<string, (data?: unknown) => unknown>();

vi.mock("@/hooks/usePython", () => ({
	usePythonEvent: (type: string, handler: (data?: unknown) => unknown) => {
		registered.set(type, handler);
	},
}));

function fireDownloadProgress(data?: unknown) {
	const handler = registered.get("download_progress");
	if (!handler) throw new Error("download_progress handler not registered");
	act(() => {
		handler(data);
	});
}

beforeEach(() => {
	registered.clear();
	vi.clearAllMocks();
});

afterEach(() => {
	// noop — keeps the afterEach shape uniform with sibling hook tests.
});

describe("useDownloadProgressEvent", () => {
	it("registers the download_progress subscription", () => {
		renderHook(() => useDownloadProgressEvent("idle"));
		expect(registered.has("download_progress")).toBe(true);
	});

	it("starts with no percentage", () => {
		const { result } = renderHook(() => useDownloadProgressEvent("loading"));
		expect(result.current).toBeNull();
	});

	it("accepts in-range percentages while loading", () => {
		const { result } = renderHook(() => useDownloadProgressEvent("loading"));
		fireDownloadProgress({ percent: 42.5 });
		expect(result.current).toBe(42.5);
		fireDownloadProgress({ percent: 0 });
		expect(result.current).toBe(0);
		fireDownloadProgress({ percent: 100 });
		expect(result.current).toBe(100);
	});

	it("ignores out-of-range and non-numeric percentages", () => {
		const { result } = renderHook(() => useDownloadProgressEvent("loading"));
		fireDownloadProgress({ percent: 42 });
		fireDownloadProgress({ percent: -1 });
		expect(result.current).toBe(42);
		fireDownloadProgress({ percent: 100.5 });
		expect(result.current).toBe(42);
		fireDownloadProgress({ percent: "80" });
		expect(result.current).toBe(42);
		fireDownloadProgress(undefined);
		expect(result.current).toBe(42);
	});

	it("resets the percentage when the recording state leaves loading", () => {
		const { result, rerender } = renderHook(
			({ state }) => useDownloadProgressEvent(state),
			{ initialProps: { state: "loading" as RecordingState } },
		);
		fireDownloadProgress({ percent: 77 });
		expect(result.current).toBe(77);
		rerender({ state: "idle" });
		expect(result.current).toBeNull();
	});

	it("keeps the percentage while still loading", () => {
		const { result, rerender } = renderHook(
			({ state }) => useDownloadProgressEvent(state),
			{ initialProps: { state: "loading" as RecordingState } },
		);
		fireDownloadProgress({ percent: 12 });
		rerender({ state: "loading" });
		expect(result.current).toBe(12);
	});
});
