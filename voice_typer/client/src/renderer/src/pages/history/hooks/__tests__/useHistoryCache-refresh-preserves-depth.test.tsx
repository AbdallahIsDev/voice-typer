/**
 *  regression test: background ``refreshFromEvent`` must preserve
 * the user's accumulated paged-in depth.
 *
 * First regression: ``useHistoryCache.refreshFromEvent`` called
 * ``fetchPage(query, favoritesOnly, HISTORY_PAGE_SIZE, 0)`` and overwrote
 * ``records`` with only the first ``HISTORY_PAGE_SIZE`` (50) rows. After a
 * user clicked "Load More" three times to reach 200 visible rows, the next
 * dictation triggered a debounced ``transcription_final`` event that
 * silently shrank the list back to 50 rows — the user lost 150 rows of
 * scroll context plus their scroll position. The fix uses
 * ``Math.max(HISTORY_PAGE_SIZE, offsetRef.current)`` as the refresh limit so
 * the refresh is never shallower than the existing visible depth.
 *
 * Second regression: the refresh limit is only a REQUEST — the server
 * clamps any single history fetch to its IPC row cap
 * (``_HISTORY_LIMIT_MAX = 500`` in ``server/ipc/history_bounds.py``), so a
 * deep-browsed list (paged-in depth > 500) receives only the newest 500
 * rows back. Replacing ``records`` with that response truncated the list
 * AND set ``hasMore = 500 >= refreshLimit(800) = false`` — older rows
 * vanished and Load-More stayed dead until remount. The fix MERGES the
 * returned head with the existing tail keyed by ``id`` (keyset ordering —
 * ``timestamp DESC, id DESC`` — guarantees the retained tail is strictly
 * older than the fresh head), and derives ``hasMore`` from the merged
 * length.
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

const stable = vi.hoisted(() => ({
	markUpdated: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
}));

vi.mock("@/hooks/useLastUpdated", () => ({
	useLastUpdated: () => ({
		agoLabel: "",
		markUpdated: stable.markUpdated,
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

/**
 * Mirror of the server's per-request history row cap
 * (``_HISTORY_LIMIT_MAX`` in ``server/ipc/history_bounds.py`` — the SEC-010
 * materialization guard). The renderer never hardcodes this value in
 * production code (it is a server-side contract), but the mock IPC layer
 * here MUST honor it so the refresh path is exercised against the real
 * backend behavior: ask for 800, receive 500.
 */
const SERVER_ROW_CAP = 500;

beforeEach(() => {
	vi.clearAllMocks();
	mockCall.mockReset();
	localStorage.clear();
	vi.resetModules();
});

afterEach(() => {
	vi.restoreAllMocks();
});

describe("refreshFromEvent preserves paged-in depth", () => {
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

describe("refreshFromEvent merges the capped head with the existing tail", () => {
	/**
	 * Mock IPC layer that behaves like the real backend: a global
	 * newest-first keyset list (``timestamp DESC, id DESC`` — ascending
	 * ``id`` = ascending time), sliced by the requested offset/limit,
	 * with the per-request row cap applied (ask 800 → get 500). The
	 * underlying total is mutable so a test can simulate new rows
	 * arriving (the ``transcription_final`` path).
	 */
	function installCappedServerHistoryMock(totalRef: { total: number }) {
		mockCall.mockImplementation((type: string, args?: unknown) => {
			const a = (args ?? {}) as {
				limit?: number;
				offset?: number;
			};
			if (type === "get_history") {
				const limit = Math.min(a.limit ?? PAGE_SIZE, SERVER_ROW_CAP);
				const offset = a.offset ?? 0;
				// Global keyset order: newest first.
				const newestFirst = makeRecords(0, totalRef.total).reverse();
				return Promise.resolve(newestFirst.slice(offset, offset + limit));
			}
			if (type === "get_today_stats") {
				return Promise.resolve(zeroStats);
			}
			return Promise.resolve({});
		});
	}

	it("keeps the deep-browsed rows and Load-More alive when the server caps the refresh at 500", async () => {
		const totalRef = { total: 1000 };
		installCappedServerHistoryMock(totalRef);

		const { useHistoryCache } = await import(
			"@/pages/history/hooks/useHistoryCache"
		);
		const { result } = renderHook(() => useHistoryCache());

		// Initial load: 50 rows (ids 999..950, newest first).
		await act(async () => {
			await result.current.load();
		});
		await waitFor(() => {
			expect(result.current.records.length).toBe(PAGE_SIZE);
		});
		expect(result.current.hasMore).toBe(true);

		// Deep browse: 15 more pages → 800 rows held (ids 999..200).
		for (let i = 0; i < 15; i++) {
			await act(async () => {
				await result.current.loadMore();
			});
		}
		await waitFor(() => {
			expect(result.current.records.length).toBe(800);
		});
		expect(result.current.hasMore).toBe(true);

		// Background refresh: the hook asks for limit=800, the
		// capped server returns only the newest 500 rows.
		await act(async () => {
			await result.current.refreshFromEvent();
		});

		// The refresh still REQUESTED the full depth...
		const getHistoryCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "get_history",
		);
		const lastCall = getHistoryCalls[getHistoryCalls.length - 1];
		const lastCallArgs = (lastCall?.[1] ?? {}) as {
			limit?: number;
			offset?: number;
		};
		expect(lastCallArgs.limit).toBe(800);
		expect(lastCallArgs.offset).toBe(0);
		// ...but only 500 rows came back (server cap). The refresh
		// fires get_history + get_today_stats in parallel, so the
		// last mock result may be the stats call — resolve the
		// last get_history RESULT by its call index.
		const lastHistoryCallIdx = mockCall.mock.calls.reduce(
			(last: number, args: unknown[], i: number) =>
				args[0] === "get_history" ? i : last,
			-1,
		);
		const lastHistoryRows = (await mockCall.mock.results[lastHistoryCallIdx]
			?.value) as HistoryRecord[];
		expect(lastHistoryRows.length).toBe(SERVER_ROW_CAP);

		// THE regression: the merged list must still hold the full
		// deep-browsed depth (fresh head 500 + retained tail 300),
		// not collapse to the 500-row head.
		expect(result.current.records.length).toBe(800);
		// Load-More stays alive (hasMore from the merged length).
		expect(result.current.hasMore).toBe(true);
		// No duplicate rows slipped in via the merge.
		const ids = result.current.records.map((r) => r.id);
		expect(new Set(ids).size).toBe(ids.length);
		// List order is still keyset (newest first).
		expect(result.current.records[0]?.id).toBe(999);
		expect(result.current.records[799]?.id).toBe(200);
	});

	it("a refresh that lands new rows keeps them at the head AND preserves the tail", async () => {
		const totalRef = { total: 1000 };
		installCappedServerHistoryMock(totalRef);

		const { useHistoryCache } = await import(
			"@/pages/history/hooks/useHistoryCache"
		);
		const { result } = renderHook(() => useHistoryCache());

		await act(async () => {
			await result.current.load();
		});
		for (let i = 0; i < 15; i++) {
			await act(async () => {
				await result.current.loadMore();
			});
		}
		await waitFor(() => {
			expect(result.current.records.length).toBe(800);
		});

		// Five new dictations land (ids 1000..1004 — newest).
		totalRef.total = 1005;
		await act(async () => {
			await result.current.refreshFromEvent();
		});

		// Newest row is at the head...
		expect(result.current.records[0]?.id).toBe(1004);
		// ...the deep-browsed tail survives (800 + 5 new = 805)...
		expect(result.current.records.length).toBe(805);
		// ...and Load-More is still alive.
		expect(result.current.hasMore).toBe(true);
		const ids = result.current.records.map((r) => r.id);
		expect(new Set(ids).size).toBe(ids.length);
		// Tail end is the oldest deep-browsed row.
		expect(result.current.records[804]?.id).toBe(200);
	});

	it("an emptied history clears the list instead of retaining stale rows", async () => {
		const totalRef = { total: 300 };
		installCappedServerHistoryMock(totalRef);

		const { useHistoryCache } = await import(
			"@/pages/history/hooks/useHistoryCache"
		);
		const { result } = renderHook(() => useHistoryCache());

		await act(async () => {
			await result.current.load();
		});
		for (let i = 0; i < 5; i++) {
			await act(async () => {
				await result.current.loadMore();
			});
		}
		await waitFor(() => {
			expect(result.current.records.length).toBe(300);
		});

		// Every row is deleted behind the renderer's back (e.g.
		// clear-all from another surface). The refresh returns [].
		totalRef.total = 0;
		await act(async () => {
			await result.current.refreshFromEvent();
		});

		expect(result.current.records.length).toBe(0);
		expect(result.current.hasMore).toBe(false);
	});
});

describe("refreshFromEvent follow-ups", () => {
	it("drops a legacy id-less tail row duplicated by its fresh identified copy", async () => {
		// Legacy rows written before the ``id`` column existed carry no
		// numeric ``id`` at runtime, so id-keyed dedup can never match
		// them. When the fresh window contains the same entry (now WITH
		// its id), the merge must drop the id-less copy by
		// ``(timestamp, text)`` instead of rendering the entry twice.
		mockCall.mockImplementation((type: string, args?: unknown) => {
			const a = (args ?? {}) as { limit?: number; offset?: number };
			if (type === "get_history") {
				return Promise.resolve(
					makeRecords(a.offset ?? 0, a.limit ?? PAGE_SIZE),
				);
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

		// Inject the legacy twin: same timestamp+text as the last fresh
		// row, but with no numeric id (cast: the static type requires
		// ``id: number`` — the gap exists only in pre-id-column data).
		const twinSource = result.current.records[PAGE_SIZE - 1];
		expect(twinSource).toBeDefined();
		const legacyTwin = {
			...twinSource,
			id: undefined,
		} as unknown as HistoryRecord;
		await act(async () => {
			result.current.setRecords((prev) => [...prev, legacyTwin]);
		});
		expect(result.current.records.length).toBe(PAGE_SIZE + 1);

		await act(async () => {
			await result.current.refreshFromEvent();
		});

		// The twin is gone (matched by content), the identified row stays.
		expect(result.current.records.length).toBe(PAGE_SIZE);
		const twins = result.current.records.filter(
			(r) =>
				r.timestamp === twinSource?.timestamp && r.text === twinSource?.text,
		);
		expect(twins.length).toBe(1);
		expect(typeof twins[0]?.id).toBe("number");
	});

	it("derives hasMore from the committed merge when Load-More lands mid-refresh", async () => {
		// P2 pin: ``hasMore``/offset must come from the state the
		// functional updater commits — not the pre-await snapshot — so
		// a page appended between IPC completion and commit is counted.
		let resolveRefresh: ((rows: HistoryRecord[]) => void) | undefined;
		mockCall.mockImplementation((type: string, args?: unknown) => {
			const a = (args ?? {}) as {
				limit?: number;
				offset?: number;
			};
			if (type === "get_history") {
				// The refresh (offset 0, limit >= depth) hangs until the
				// test releases it; paging calls resolve immediately.
				if (a.offset === 0 && (a.limit ?? 0) > PAGE_SIZE) {
					return new Promise<HistoryRecord[]>((resolve) => {
						resolveRefresh = resolve;
					});
				}
				return Promise.resolve(
					makeRecords(a.offset ?? 0, a.limit ?? PAGE_SIZE),
				);
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
		// Page once so the refresh asks for limit=100 (the deferred
		// branch below only hangs refresh-depth fetches, limit > 50).
		await act(async () => {
			await result.current.loadMore();
		});
		await waitFor(() => {
			expect(result.current.records.length).toBe(PAGE_SIZE * 2);
		});

		// Start the refresh (its IPC stays pending), then land a
		// second Load-More page before the refresh resolves.
		let refreshPromise: Promise<void> | undefined;
		act(() => {
			refreshPromise = result.current.refreshFromEvent();
		});
		await act(async () => {
			await result.current.loadMore();
		});
		await waitFor(() => {
			expect(result.current.records.length).toBe(PAGE_SIZE * 3);
		});
		await act(async () => {
			resolveRefresh?.(makeRecords(0, PAGE_SIZE * 3));
			await refreshPromise;
		});

		// All three pages survive and Load-More stays alive: the
		// committed merge holds 150 rows against refreshLimit (100).
		expect(result.current.records.length).toBe(PAGE_SIZE * 3);
		expect(result.current.hasMore).toBe(true);
	});
});
