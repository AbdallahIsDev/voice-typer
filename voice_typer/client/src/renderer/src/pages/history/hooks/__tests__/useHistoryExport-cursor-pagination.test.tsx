/**
 * Focused tests for useHistoryExport's cursor (keyset) pagination.
 *
 * The export loop previously paged with OFFSET only — every page forced
 * the backend to skip past all previously fetched rows (O(pages ×
 * offset)). It now threads before_timestamp + before_id (derived from
 * the last accumulated row) through every page after the first — the
 * same strategy useHistoryCache's loadMore uses — while keeping
 * limit + offset in the payload as the defensive fallback (rows
 * without a usable timestamp/id cursor-anchor via OFFSET instead).
 *
 * These tests pin:
 *   - the FIRST page carries no cursor params,
 *   - every subsequent page carries the (timestamp, id) of the LAST
 *     row of the accumulated export,
 *   - rows missing timestamp or id fall back to the OFFSET-only path,
 *   - data outcomes are unchanged (full export, empty export, sort).
 */
import { renderHook } from "@testing-library/react";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useHistoryExport } from "@/pages/history/hooks/useHistoryExport";
import type { HistoryRecord, WindowBridge } from "@/types/ipc";

vi.mock("sonner", () => ({
	toast: {
		info: vi.fn(),
		warning: vi.fn(),
		error: vi.fn(),
		success: vi.fn(),
	},
}));

function makeRow(id: number, timestamp: string): HistoryRecord {
	// HistoryRecord's full shape isn't relevant to the paging contract —
	// the loop only reads id/timestamp for the cursor and forwards the
	// rows verbatim; a minimal partial satisfies the sort (which reads
	// timestamp) and the bridge mock.
	return {
		id,
		timestamp,
		text: `row-${id}`,
	} as unknown as HistoryRecord;
}

const mockCall = vi.fn();

beforeEach(() => {
	vi.clearAllMocks();
	mockCall.mockReset();
	mockCall.mockImplementation(() => Promise.resolve([]));
	(window as unknown as { window_: Partial<WindowBridge> }).window_ = {
		exportHistory: vi
			.fn()
			.mockResolvedValue({ success: true, path: "/tmp/out.json" }),
	};
});

afterEach(() => {
	delete (window as unknown as { window_?: Partial<WindowBridge> }).window_;
});

function mountExport(searchQuery = "", favoritesOnly = false) {
	return renderHook(() =>
		useHistoryExport({
			call: mockCall,
			records: [],
			sortOrder: "newest",
			searchQuery,
			favoritesOnly,
		}),
	);
}

describe("useHistoryExport — cursor (keyset) pagination", () => {
	it("first page has no cursor; subsequent pages cursor-anchor on the last accumulated row", async () => {
		// Three full pages then an empty page. Pages are handed out
		// sequentially by the offset the loop passes.
		const pages: HistoryRecord[][] = [
			Array.from({ length: 100 }, (_, i) =>
				makeRow(300 - i, `2026-01-0${(i % 9) + 1}T00:00:0${i % 10}Z`),
			),
			Array.from({ length: 100 }, (_, i) =>
				makeRow(200 - i, `2026-01-0${(i % 9) + 1}T00:00:0${i % 10}Z`),
			),
			Array.from({ length: 100 }, (_, i) =>
				makeRow(100 - i, `2026-01-0${(i % 9) + 1}T00:00:0${i % 10}Z`),
			),
			[],
		];
		let pageIdx = 0;
		mockCall.mockImplementation((type: string) => {
			if (type === "get_history") {
				const page = pages[Math.min(pageIdx, pages.length - 1)];
				pageIdx += 1;
				return Promise.resolve(page);
			}
			return Promise.resolve({});
		});

		const { result } = mountExport();
		await result.current.doExport("json");

		const calls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "get_history",
		) as unknown as [string, Record<string, unknown>][];

		expect(calls.length).toBe(4);
		// First page: no cursor params (OFFSET path only).
		expect(calls[0]?.[1]).toEqual({ limit: 100, offset: 0 });
		expect(calls[0]?.[1]).not.toHaveProperty("before_timestamp");
		// Second page: cursor = last row of the first accumulated page.
		const lastOfPage1 = pages[0]?.[pages[0].length - 1];
		expect(calls[1]?.[1]).toEqual({
			limit: 100,
			offset: 100,
			before_timestamp: lastOfPage1?.timestamp,
			before_id: lastOfPage1?.id,
		});
		// Third page: cursor = last row of the accumulated 200 rows.
		const lastOfPage2 = pages[1]?.[pages[1].length - 1];
		expect(calls[2]?.[1]).toEqual({
			limit: 100,
			offset: 200,
			before_timestamp: lastOfPage2?.timestamp,
			before_id: lastOfPage2?.id,
		});
		// Fourth (empty) page still carries the moving cursor + offset.
		const lastOfPage3 = pages[2]?.[pages[2].length - 1];
		expect(calls[3]?.[1]).toEqual({
			limit: 100,
			offset: 300,
			before_timestamp: lastOfPage3?.timestamp,
			before_id: lastOfPage3?.id,
		});

		// Data outcome unchanged: all 300 rows were exported.
		const exportHistory = (
			window as unknown as {
				window_: { exportHistory: ReturnType<typeof vi.fn> };
			}
		).window_.exportHistory;
		expect(exportHistory).toHaveBeenCalledTimes(1);
		const exportedRows = exportHistory.mock.calls[0]?.[0] as HistoryRecord[];
		expect(exportedRows).toHaveLength(300);
	});

	it("falls back to OFFSET-only when the last row lacks a usable timestamp or id", async () => {
		const pages: HistoryRecord[][] = [
			// Full page whose last row has NO id (e.g. a legacy row written
			// before the id column existed).
			Array.from({ length: 100 }, (_, i) =>
				makeRow(i, `2026-01-01T00:00:0${i % 10}Z`),
			).map((row, i) =>
				i === 99
					? ({ ...row, id: undefined } as unknown as HistoryRecord)
					: row,
			),
			[],
		];
		let pageIdx = 0;
		mockCall.mockImplementation((type: string) => {
			if (type === "get_history") {
				const page = pages[Math.min(pageIdx, pages.length - 1)];
				pageIdx += 1;
				return Promise.resolve(page);
			}
			return Promise.resolve({});
		});

		const { result } = mountExport();
		await result.current.doExport("json");

		const calls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "get_history",
		) as unknown as [string, Record<string, unknown>][];
		expect(calls.length).toBe(2);
		// Second page: no cursor fields (defensive OFFSET fallback).
		expect(calls[1]?.[1]).toEqual({ limit: 100, offset: 100 });
		expect(calls[1]?.[1]).not.toHaveProperty("before_timestamp");
		expect(calls[1]?.[1]).not.toHaveProperty("before_id");
	});

	it("passes the cursor through the search_history payload under an active query", async () => {
		const page1 = Array.from({ length: 100 }, (_, i) =>
			makeRow(50 - i, `2026-02-0${(i % 9) + 1}T00:00:00Z`),
		);
		let pageIdx = 0;
		mockCall.mockImplementation((type: string) => {
			if (type === "search_history") {
				const page = pageIdx === 0 ? page1 : [];
				pageIdx += 1;
				return Promise.resolve(page);
			}
			return Promise.resolve({});
		});

		const { result } = mountExport("hello");
		await result.current.doExport("json");

		const calls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "search_history",
		) as unknown as [string, Record<string, unknown>][];
		expect(calls.length).toBe(2);
		expect(calls[0]?.[1]).toEqual({ query: "hello", limit: 100, offset: 0 });
		const last = page1[page1.length - 1];
		expect(calls[1]?.[1]).toEqual({
			query: "hello",
			limit: 100,
			offset: 100,
			before_timestamp: last?.timestamp,
			before_id: last?.id,
		});
		// Filtered-export toast still fires (unchanged behavior).
		expect(toast.info).toHaveBeenCalled();
	});

	it("keeps the empty-export and no-bridge outcomes unchanged", async () => {
		mockCall.mockImplementation(() => Promise.resolve([]));
		const { result } = mountExport();
		await result.current.doExport("json");
		expect(toast.warning).toHaveBeenCalled(); // exportEmpty warning
		const exportHistory = (
			window as unknown as {
				window_: { exportHistory: ReturnType<typeof vi.fn> };
			}
		).window_.exportHistory;
		expect(exportHistory).not.toHaveBeenCalled();
	});
});
