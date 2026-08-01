/**
 *  regression test: background ``refreshFromEvent`` must preserve
 * the user's accumulated paged-in depth.
 *
 * Before the fix, ``useHistoryCache.refreshFromEvent`` called
 * ``fetchPage(query, favoritesOnly, HISTORY_PAGE_SIZE, 0)`` and overwrote
 * ``records`` with only the first ``HISTORY_PAGE_SIZE`` (50) rows. After a
 * user clicked "Load More" three times to reach 200 visible rows, the next
 * dictation triggered a debounced ``transcription_final`` event that
 * silently shrank the list back to 50 rows — the user lost 150 rows of
 * scroll context plus their scroll position.
 *
 * The fix uses ``Math.max(HISTORY_PAGE_SIZE, offsetRef.current)`` as the
 * refresh limit so the refresh is never shallower than the existing
 * visible depth.
 *
 * The test renders the hook directly (not the full page) so it can drive
 * the load → loadMore → refreshFromEvent sequence deterministically
 * without depending on Radix portals or debounce timers.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockCall } = vi.hoisted(() => ({
	mockCall: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
}));

vi.mock("@/hooks/useLastUpdated", () => ({
	useLastUpdated: () => ({
		agoLabel: "",
		markUpdated: vi.fn(),
		refreshing: false,
		withRefresh: async <T,>(op: () => Promise<T>): Promise<T> => op(),
	}),
}));

import type { HistoryRecord, TodayStats } from "@/types/ipc";

const PAGE_SIZE = 50;

function makeRecords(start: number, count: number): HistoryRecord[] {
	const rows: HistoryRecord[] = [];
	for (let i = 0; i < count; i++) {
		rows.push({
			id: start + i,
			text: `row ${start + i}`,
			timestamp: new Date(start + i * 1000).toISOString(),
			duration: 1,
			model: "tiny",
			device: "cpu",
			word_count: 2,
			char_count: 10,
			favorite: 0,
			language: "en",
		});
	}
	return rows;
}

const zeroStats: TodayStats = {
	count: 0,
	chars: 0,
	word_count: 0,
	duration: 0,
};

beforeEach(() => {
	mockCall.mockReset();
	localStorage.clear();
	vi.resetModules();
});

afterEach(() => {
	vi.restoreAllMocks();
});

describe("ZU-7: refreshFromEvent preserves paged-in depth", () => {
	it("re-fetches at least the current paged-in depth on refresh", async () => {
		// Initial load: 1 page of 50 rows.
		// loadMore: 3 additional pages (50 each) → offsetRef = 200.
		// refreshFromEvent should call get_history with limit >= 200
		// (NOT the default 50).
		mockCall.mockImplementation((type: string, args?: unknown) => {
			const a = (args ?? {}) as { limit?: number; offset?: number };
			if (type === "get_history") {
				const limit = a.limit ?? PAGE_SIZE;
				const offset = a.offset ?? 0;
				// Return exactly `limit` rows so hasMore stays true.
				return Promise.resolve(makeRecords(offset, limit));
			}
			if (type === "get_today_stats") {
				return Promise.resolve(zeroStats);
			}
			return Promise.resolve({});
		});

		const { useHistoryCache } = await import(
			"@/pages/history/hooks/useHistoryCache"
		);
		const { result } = renderHook(() => useHistoryCache());

		// Initial load.
		await act(async () => {
			await result.current.load();
		});
		await waitFor(() => {
			expect(result.current.records.length).toBe(PAGE_SIZE);
		});
		expect(result.current.hasMore).toBe(true);

		// Page through 3 more loads → offsetRef should be 200.
		await act(async () => {
			await result.current.loadMore();
		});
		await act(async () => {
			await result.current.loadMore();
		});
		await act(async () => {
			await result.current.loadMore();
		});
		await waitFor(() => {
			expect(result.current.records.length).toBe(PAGE_SIZE * 4);
		});

		// Now trigger a background refresh (simulating a
		// transcription_final event). The fix should preserve the
		// paged-in depth of 200 rows by re-fetching with limit >= 200.
		await act(async () => {
			await result.current.refreshFromEvent();
		});

		// Find the LAST get_history call (the refreshFromEvent call).
		const getHistoryCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "get_history",
		);
		expect(getHistoryCalls.length).toBeGreaterThanOrEqual(1);
		const lastCall = getHistoryCalls[getHistoryCalls.length - 1];
		const lastCallArgs = (lastCall?.[1] ?? {}) as {
			limit?: number;
			offset?: number;
		};
		//the refresh limit must be >= 200 (the paged-in depth),
		// NOT the default 50.
		expect(lastCallArgs.limit).toBeGreaterThanOrEqual(200);
		expect(lastCallArgs.offset).toBe(0);

		// The records list must still have at least 200 rows after
		// the refresh (it can be capped by the backend's response).
		await waitFor(() => {
			expect(result.current.records.length).toBeGreaterThanOrEqual(200);
		});
	});

	it("uses HISTORY_PAGE_SIZE as the minimum refresh limit when no paging has occurred", async () => {
		// Initial load only — no loadMore calls. offsetRef = 50.
		// refreshFromEvent should use limit = max(50, 50) = 50.
		mockCall.mockImplementation((type: string, args?: unknown) => {
			const a = (args ?? {}) as { limit?: number; offset?: number };
			if (type === "get_history") {
				const limit = a.limit ?? PAGE_SIZE;
				const offset = a.offset ?? 0;
				return Promise.resolve(makeRecords(offset, limit));
			}
			if (type === "get_today_stats") {
				return Promise.resolve(zeroStats);
			}
			return Promise.resolve({});
		});

		const { useHistoryCache } = await import(
			"@/pages/history/hooks/useHistoryCache"
		);
		const { result } = renderHook(() => useHistoryCache());

		await act(async () => {
			await result.current.load();
		});
		await waitFor(() => {
			expect(result.current.records.length).toBe(PAGE_SIZE);
		});

		await act(async () => {
			await result.current.refreshFromEvent();
		});

		const getHistoryCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "get_history",
		);
		// The refresh call is the LAST get_history call.
		const lastCall = getHistoryCalls[getHistoryCalls.length - 1];
		const lastCallArgs = (lastCall?.[1] ?? {}) as {
			limit?: number;
			offset?: number;
		};
		// Without prior paging, the refresh limit is the default 50.
		expect(lastCallArgs.limit).toBe(PAGE_SIZE);
		expect(lastCallArgs.offset).toBe(0);
	});
});
