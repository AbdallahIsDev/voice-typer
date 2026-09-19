import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HistoryRecord, TodayStats } from "@/types/ipc";

import {
	loadCachedRecent,
	loadCachedStats,
	persistRecent,
	persistStats,
} from "../cache";
import { RECENT_CACHE_KEY, STATS_CACHE_KEY } from "../constants";

function makeRecord(overrides: Partial<HistoryRecord> = {}): HistoryRecord {
	return {
		id: 1,
		text: "hello world",
		timestamp: "2026-09-04T12:00:00.000Z",
		duration: 2,
		model: "tiny",
		device: "cpu",
		word_count: 2,
		char_count: 11,
		favorite: 0,
		language: "en",
		...overrides,
	};
}

const validStats: TodayStats = {
	count: 3,
	chars: 30,
	word_count: 6,
	duration: 9,
};

beforeEach(() => {
	localStorage.clear();
});

afterEach(() => {
	localStorage.clear();
	vi.restoreAllMocks();
});

describe("loadCachedRecent per-entry validation", () => {
	it("keeps valid entries and drops corrupted ones from a mixed payload", () => {
		const good1 = makeRecord({ id: 10, text: "first" });
		const good2 = makeRecord({ id: 11, text: "second" });
		localStorage.setItem(
			RECENT_CACHE_KEY,
			JSON.stringify([
				good1,
				{ id: "not-a-number", text: "bad id", timestamp: "x" },
				{ id: 12, text: null, timestamp: "x" },
				{ id: 13, text: "no timestamp" },
				null,
				"just a string",
				42,
				good2,
			]),
		);

		const loaded = loadCachedRecent({ current: [] });

		expect(loaded).toHaveLength(2);
		expect(loaded[0]?.id).toBe(10);
		expect(loaded[1]?.id).toBe(11);
	});

	it("degrades a fully-corrupted list to [] without throwing", () => {
		localStorage.setItem(
			RECENT_CACHE_KEY,
			JSON.stringify([{ nope: true }, 7, "str"]),
		);

		expect(() => loadCachedRecent({ current: [] })).not.toThrow();
		expect(loadCachedRecent({ current: [] })).toEqual([]);
	});

	it("ignores a non-array payload (older/foreign cache shape)", () => {
		localStorage.setItem(RECENT_CACHE_KEY, JSON.stringify({ count: 5 }));

		expect(loadCachedRecent({ current: [] })).toEqual([]);
	});

	it("survives malformed JSON (warn + empty list)", () => {
		localStorage.setItem(RECENT_CACHE_KEY, "{not json");

		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
		const loaded = loadCachedRecent({ current: [] });

		expect(loaded).toEqual([]);
		expect(warn).toHaveBeenCalledOnce();
	});

	it("keeps entries carrying the optional truncation fields", () => {
		const record = makeRecord({
			id: 77,
			text_truncated: true,
			text_full_length: 900,
		});
		localStorage.setItem(RECENT_CACHE_KEY, JSON.stringify([record]));

		const loaded = loadCachedRecent({ current: [] });

		expect(loaded).toHaveLength(1);
		expect(loaded[0]?.id).toBe(77);
		expect(loaded[0]?.text_truncated).toBe(true);
	});

	it("prefers the in-memory ref over localStorage (hit-avoidance)", () => {
		const cached = makeRecord({ id: 1 });
		localStorage.setItem(RECENT_CACHE_KEY, JSON.stringify([cached]));

		const seeded = [makeRecord({ id: 99 })];
		expect(loadCachedRecent({ current: seeded })).toBe(seeded);
	});
});

describe("cache round-trips", () => {
	it("persistRecent then a fresh ref reloads the persisted list", () => {
		const records = [
			makeRecord({ id: 1, text: "a" }),
			makeRecord({ id: 2, text: "b" }),
		];
		persistRecent({ current: [] }, records);

		const reloaded = loadCachedRecent({ current: [] });
		expect(reloaded).toHaveLength(2);
		expect(reloaded[1]?.id).toBe(2);
	});

	it("persistRecent survives a quota-exceeded localStorage (ref keeps the value)", () => {
		const records = [makeRecord()];
		const ref = { current: [] as HistoryRecord[] };
		// Spy the concrete instance: in this suite `localStorage` may be
		// either jsdom's real Storage (setItem on the prototype) or the
		// in-memory fallback from test-setup (own-property methods) —
		// spying the instance covers both.
		vi.spyOn(localStorage, "setItem").mockImplementation(() => {
			throw new DOMException("QuotaExceeded", "QuotaExceededError");
		});
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

		persistRecent(ref, records);

		expect(ref.current).toBe(records);
		expect(warn).toHaveBeenCalledOnce();
	});

	it("loadCachedStats keeps its shape sanity-check (count must be a number)", () => {
		localStorage.setItem(STATS_CACHE_KEY, JSON.stringify(validStats));
		expect(loadCachedStats({ current: null })).toEqual(validStats);

		localStorage.clear();
		localStorage.setItem(STATS_CACHE_KEY, JSON.stringify({ count: "five" }));
		expect(loadCachedStats({ current: null })).toBeNull();
	});

	it("persistStats round-trips through localStorage", () => {
		const ref = { current: null as TodayStats | null };
		persistStats(ref, validStats);

		expect(ref.current).toEqual(validStats);
		expect(loadCachedStats({ current: null })).toEqual(validStats);
	});
});
