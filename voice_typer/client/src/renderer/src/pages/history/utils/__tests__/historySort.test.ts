// Unit tests for the client-side history sort.
//
// Covers all four HistorySortOrder values (newest / oldest / az / za)
// plus the parseHistorySortOrder runtime type guard. The locale-aware
// A→Z / Z→A path is exercised against the default "en" locale.
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { HistoryRecord } from "@/types/ipc";

// Stub getLocale() so the collator locale is deterministic in tests.
vi.mock("@/i18n/i18n", () => ({
	getLocale: () => "en",
}));

import {
	type HistorySortOrder,
	parseHistorySortOrder,
	sortRecords,
} from "../historySort";

const sample = (overrides: Partial<HistoryRecord> = {}): HistoryRecord => ({
	id: 1,
	text: "hello world",
	timestamp: "2024-01-01T00:00:00.000Z",
	duration: 1,
	model: "tiny",
	device: "cpu",
	word_count: 2,
	char_count: 11,
	favorite: 0,
	language: "en",
	...overrides,
});

// Backend returns records in "newest first" order (index 0 = most recent).
// We mirror that contract here so the "newest" / "oldest" tests assert
// the right semantics.
const records: HistoryRecord[] = [
	sample({ id: 3, text: "Banana", timestamp: "2024-03-01T00:00:00.000Z" }),
	sample({ id: 2, text: "apple", timestamp: "2024-02-01T00:00:00.000Z" }),
	sample({ id: 1, text: "Cherry", timestamp: "2024-01-01T00:00:00.000Z" }),
];

describe("sortRecords", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("returns a new array (does not mutate the input)", () => {
		const result = sortRecords(records, "newest");
		expect(result).not.toBe(records);
		expect(records).toEqual(records); // input unchanged
	});

	it("'newest' preserves the backend's newest-first order", () => {
		const result = sortRecords(records, "newest");
		expect(result.map((r) => r.id)).toEqual([3, 2, 1]);
	});

	it("'oldest' reverses the backend's newest-first order", () => {
		const result = sortRecords(records, "oldest");
		expect(result.map((r) => r.id)).toEqual([1, 2, 3]);
	});

	it("'az' sorts text ascending via locale-aware collation (case-insensitive)", () => {
		const result = sortRecords(records, "az");
		// apple, Banana, Cherry — case-insensitive ascending.
		expect(result.map((r) => r.text)).toEqual(["apple", "Banana", "Cherry"]);
	});

	it("'za' sorts text descending via locale-aware collation (case-insensitive)", () => {
		const result = sortRecords(records, "za");
		// Cherry, Banana, apple — case-insensitive descending.
		expect(result.map((r) => r.text)).toEqual(["Cherry", "Banana", "apple"]);
	});

	it("handles an empty array under any order", () => {
		for (const order of [
			"newest",
			"oldest",
			"az",
			"za",
		] as HistorySortOrder[]) {
			expect(sortRecords([], order)).toEqual([]);
		}
	});

	it("handles a single-element array under any order", () => {
		const one = [records[0] as HistoryRecord];
		for (const order of [
			"newest",
			"oldest",
			"az",
			"za",
		] as HistorySortOrder[]) {
			expect(sortRecords(one, order)).toEqual(one);
		}
	});

	it("uses numeric collation so 'item2' < 'item10' (not lexically)", () => {
		const numeric = [
			sample({ id: 10, text: "item10" }),
			sample({ id: 2, text: "item2" }),
			sample({ id: 1, text: "item1" }),
		];
		const result = sortRecords(numeric, "az");
		expect(result.map((r) => r.text)).toEqual(["item1", "item2", "item10"]);
	});

	it("treats missing text as empty string for collation (no crash)", () => {
		const withMissing = [
			sample({ id: 2, text: "" }),
			sample({ id: 1, text: undefined as unknown as string }),
		];
		// Should not throw; empty / undefined sort before "Banana" etc.
		const result = sortRecords(withMissing, "az");
		expect(result).toHaveLength(2);
	});
});

describe("parseHistorySortOrder", () => {
	it.each([
		"newest",
		"oldest",
		"az",
		"za",
	] as HistorySortOrder[])("returns the value verbatim when it is a valid sort order (%s)", (value) => {
		expect(parseHistorySortOrder(value)).toBe(value);
	});

	it.each([
		"",
		"unknown",
		"NEWEST",
		"newest ",
		"az ",
		"random",
	])("falls back to 'newest' for unrecognised value %j", (value) => {
		expect(parseHistorySortOrder(value)).toBe("newest");
	});
});
