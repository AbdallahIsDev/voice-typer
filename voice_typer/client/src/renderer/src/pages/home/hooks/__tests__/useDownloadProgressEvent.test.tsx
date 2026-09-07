/**
 * Tests for useDownloadProgressEvent (extracted from Home.tsx).
 *
 * Contract: subscribe to `download_progress` pushes, keep only valid
 * 0-100 percentages from the wire field `progress` (NOT `percent` — the
 * server payload is `{model, progress, status, ...}`), and hide the bar
 * when no download is genuinely in flight:
 *   - reset to null whenever the recording state leaves "loading"
 *     (the state the bar was originally built for) so a stale bar can
 *     never linger;
 *   - terminal events (progress 100 / terminal status markers) remove
 *     the model from the in-flight set and clear the bar once ALL
 *     downloads finish;
 *   - queued events (`queue_position`) keep the bar up across a
 *     download chain without regressing the live percentage;
 *   - a terminal-armed drain window (TERMINAL_DRAIN_MS) clears entries
 *     the event stream never announces (a queued model cancelled from
 *     the Models page pushes no event).
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
	TERMINAL_DRAIN_MS,
	useDownloadProgressEvent,
} from "@/pages/home/hooks/useDownloadProgressEvent";
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
	// Fake timers for the whole file: every terminal event arms a
	// TERMINAL_DRAIN_MS timer, and tests must decide deterministically
	// whether it fires (no cross-test real-time leakage).
	vi.useFakeTimers();
});

afterEach(() => {
	vi.useRealTimers();
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
		fireDownloadProgress({
			model: "tiny.en",
			progress: 42.5,
			status: "Downloading tiny.en: 5 MB",
		});
		expect(result.current).toBe(42.5);
		// 0 is a legit non-terminal value (the "Starting download…" push);
		// 100 is NOT (the wire caps intermediate events at 95 — see the
		// terminal-event describe below).
		fireDownloadProgress({
			model: "tiny.en",
			progress: 0,
			status: "Starting download for tiny.en...",
		});
		expect(result.current).toBe(0);
		fireDownloadProgress({
			model: "tiny.en",
			progress: 95,
			status: "Downloading tiny.en: 80 MB",
		});
		expect(result.current).toBe(95);
	});

	it("ignores out-of-range and non-numeric percentages", () => {
		const { result } = renderHook(() => useDownloadProgressEvent("loading"));
		fireDownloadProgress({
			model: "tiny.en",
			progress: 42,
			status: "Downloading",
		});
		fireDownloadProgress({
			model: "tiny.en",
			progress: -1,
			status: "Downloading",
		});
		expect(result.current).toBe(42);
		fireDownloadProgress({
			model: "tiny.en",
			progress: 100.5,
			status: "Downloading",
		});
		expect(result.current).toBe(42);
		fireDownloadProgress({
			model: "tiny.en",
			progress: "80",
			status: "Downloading",
		});
		expect(result.current).toBe(42);
		fireDownloadProgress(undefined);
		expect(result.current).toBe(42);
	});

	it("ignores events without a model (defensive — the backend always sends one)", () => {
		const { result } = renderHook(() => useDownloadProgressEvent("loading"));
		fireDownloadProgress({ progress: 42, status: "Downloading" });
		expect(result.current).toBeNull();
	});

	it("reads the wire field `progress`, not `percent`", () => {
		const { result } = renderHook(() => useDownloadProgressEvent("loading"));
		// A `percent` payload never exists on the wire — it must be ignored
		// (pre-fix the hook read ONLY `percent`, so the bar could never fill).
		fireDownloadProgress({ model: "tiny.en", percent: 42 });
		expect(result.current).toBeNull();
		fireDownloadProgress({
			model: "tiny.en",
			progress: 42,
			status: "Downloading",
		});
		expect(result.current).toBe(42);
	});

	it("resets the percentage when the recording state leaves loading", () => {
		const { result, rerender } = renderHook(
			({ state }) => useDownloadProgressEvent(state),
			{ initialProps: { state: "loading" as RecordingState } },
		);
		fireDownloadProgress({
			model: "tiny.en",
			progress: 77,
			status: "Downloading",
		});
		expect(result.current).toBe(77);
		rerender({ state: "idle" });
		expect(result.current).toBeNull();
	});

	it("keeps the percentage while still loading", () => {
		const { result, rerender } = renderHook(
			({ state }) => useDownloadProgressEvent(state),
			{ initialProps: { state: "loading" as RecordingState } },
		);
		fireDownloadProgress({
			model: "tiny.en",
			progress: 12,
			status: "Downloading",
		});
		rerender({ state: "loading" });
		expect(result.current).toBe(12);
	});
});

describe("useDownloadProgressEvent — terminal events clear the bar", () => {
	it("clears on the terminal success event (progress 100 + complete status)", () => {
		const { result } = renderHook(() => useDownloadProgressEvent("idle"));
		fireDownloadProgress({
			model: "tiny.en",
			progress: 55,
			status: "Downloading tiny.en: 10 MB",
		});
		expect(result.current).toBe(55);
		fireDownloadProgress({
			model: "tiny.en",
			progress: 100,
			status: "Download of tiny.en complete",
		});
		expect(result.current).toBeNull();
	});

	it("treats progress 100 alone as terminal (the only 100% value on the wire)", () => {
		const { result } = renderHook(() => useDownloadProgressEvent("idle"));
		fireDownloadProgress({
			model: "tiny.en",
			progress: 60,
			status: "Downloading",
		});
		fireDownloadProgress({ model: "tiny.en", progress: 100 });
		expect(result.current).toBeNull();
	});

	it("clears on the terminal cancel status (progress 0)", () => {
		const { result } = renderHook(() => useDownloadProgressEvent("idle"));
		fireDownloadProgress({
			model: "tiny.en",
			progress: 40,
			status: "Downloading",
		});
		fireDownloadProgress({
			model: "tiny.en",
			progress: 0,
			status: "Download cancelled",
		});
		expect(result.current).toBeNull();
	});

	it("clears on the terminal failure status (progress 0)", () => {
		const { result } = renderHook(() => useDownloadProgressEvent("idle"));
		fireDownloadProgress({
			model: "tiny.en",
			progress: 5,
			status: "Checking cache for tiny.en...",
		});
		fireDownloadProgress({
			model: "tiny.en",
			progress: 0,
			status: "Download failed: HTTP 503",
		});
		expect(result.current).toBeNull();
	});

	it("clears on the cache-hit terminal status (progress 5, already cached)", () => {
		const { result } = renderHook(() => useDownloadProgressEvent("idle"));
		fireDownloadProgress({
			model: "tiny.en",
			progress: 5,
			status: "tiny.en already cached",
		});
		expect(result.current).toBeNull();
	});

	it("keeps the percentage on non-terminal pause/resume transition events", () => {
		const { result } = renderHook(() => useDownloadProgressEvent("idle"));
		fireDownloadProgress({
			model: "tiny.en",
			progress: 40,
			status: "Downloading",
		});
		fireDownloadProgress({
			model: "tiny.en",
			progress: 42,
			status: "Download of tiny.en paused",
			paused: true,
		});
		expect(result.current).toBe(42);
	});
});

describe("useDownloadProgressEvent — download queue chain", () => {
	it("a queued event keeps the bar up without regressing the live percentage", () => {
		const { result } = renderHook(() => useDownloadProgressEvent("idle"));
		fireDownloadProgress({
			model: "tiny.en",
			progress: 60,
			status: "Downloading tiny.en: 30 MB",
		});
		// A second download is queued behind the live one — its event
		// carries progress 0 and queue_position 1.
		fireDownloadProgress({
			model: "large-v3",
			progress: 0,
			status: "Download of large-v3 queued",
			queue_position: 1,
		});
		expect(result.current).toBe(60);
	});

	it("keeps the bar across a queued-download chain and clears after the final completion", () => {
		const { result } = renderHook(() => useDownloadProgressEvent("idle"));
		// tiny.en transfers while large-v3 queues behind it.
		fireDownloadProgress({
			model: "tiny.en",
			progress: 40,
			status: "Downloading tiny.en: 8 MB",
		});
		fireDownloadProgress({
			model: "large-v3",
			progress: 0,
			status: "Download of large-v3 queued",
			queue_position: 1,
		});
		// tiny.en finishes — the bar must PERSIST (large-v3 is still
		// pending), holding its last percentage until the next transfer.
		fireDownloadProgress({
			model: "tiny.en",
			progress: 100,
			status: "Download of tiny.en complete",
		});
		expect(result.current).toBe(40);
		// The queue drains: large-v3 starts transferring.
		fireDownloadProgress({
			model: "large-v3",
			progress: 0,
			status: "Starting download for large-v3...",
		});
		expect(result.current).toBe(0);
		fireDownloadProgress({
			model: "large-v3",
			progress: 90,
			status: "Downloading large-v3: 2 GB",
		});
		expect(result.current).toBe(90);
		// Final completion — nothing is queued or transferring any more.
		fireDownloadProgress({
			model: "large-v3",
			progress: 100,
			status: "Download of large-v3 complete",
		});
		expect(result.current).toBeNull();
	});
});

describe("useDownloadProgressEvent — terminal drain window", () => {
	it("clears a silently-cancelled queued model once the drain window passes", () => {
		const { result } = renderHook(() => useDownloadProgressEvent("idle"));
		fireDownloadProgress({
			model: "tiny.en",
			progress: 55,
			status: "Downloading tiny.en: 10 MB",
		});
		fireDownloadProgress({
			model: "large-v3",
			progress: 0,
			status: "Download of large-v3 queued",
			queue_position: 1,
		});
		// tiny.en completes. large-v3 was cancelled from the Models page
		// while queued — the backend pushes NO event for that, so the
		// entry is indistinguishable from a pending chain start until the
		// stream stays quiet past the drain window.
		fireDownloadProgress({
			model: "tiny.en",
			progress: 100,
			status: "Download of tiny.en complete",
		});
		expect(result.current).toBe(55);
		act(() => {
			vi.advanceTimersByTime(TERMINAL_DRAIN_MS);
		});
		expect(result.current).toBeNull();
	});

	it("a follow-up event inside the drain window cancels the clear", () => {
		const { result } = renderHook(() => useDownloadProgressEvent("idle"));
		fireDownloadProgress({
			model: "tiny.en",
			progress: 100,
			status: "Download of tiny.en complete",
		});
		expect(result.current).toBeNull();
		// The queue drains a moment later — well inside the window.
		act(() => {
			vi.advanceTimersByTime(TERMINAL_DRAIN_MS - 10);
		});
		fireDownloadProgress({
			model: "large-v3",
			progress: 10,
			status: "Downloading large-v3: 300 MB",
		});
		expect(result.current).toBe(10);
		// The old terminal's drain timer was cancelled by the event above —
		// advancing past its original deadline must NOT clear the live bar.
		act(() => {
			vi.advanceTimersByTime(TERMINAL_DRAIN_MS);
		});
		expect(result.current).toBe(10);
	});
});
