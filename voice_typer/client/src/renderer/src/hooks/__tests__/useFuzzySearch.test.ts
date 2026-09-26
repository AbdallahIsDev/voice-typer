import { describe, expect, it } from "vitest";

import {
	filterFuzzy,
	FUZZY_RESULT_LIMIT,
	FUZZY_THRESHOLD,
	fuzzyContains,
} from "@/hooks/useFuzzySearch";

describe("fuzzyContains, exact-first ordering", () => {
	it("matches substrings case-insensitively", () => {
		expect(fuzzyContains("LLM Polishing", "llm pol")).toBe(true);
		expect(fuzzyContains("Prewarm Status", "PREWARM")).toBe(true);
	});

	it("treats an empty query as a match", () => {
		expect(fuzzyContains("Theme", "")).toBe(true);
		expect(fuzzyContains("Theme", "   ")).toBe(true);
	});

	it("rejects the superstring direction and true non-matches", () => {
		expect(fuzzyContains("Theme", "the theme settings rows")).toBe(false);
		expect(fuzzyContains("Overlay", "hotkey")).toBe(false);
	});

	it("tolerates a single-character typo inside the threshold", () => {
		expect(fuzzyContains("Dictation Key", "dictatoin key")).toBe(true);
	});
});

describe("filterFuzzy", () => {
	const items = [
		{ trigger: "hello", expansion: "hi there" },
		{ trigger: "goodbye", expansion: "see you" },
		{ trigger: "helium", expansion: "gas" },
	];

	it("returns exact substring matches first in input order", () => {
		const out = filterFuzzy(items, "he", (r) => [r.trigger, r.expansion]);
		expect(out[0]).toEqual(items[0]);
		expect(out.map((r) => r.trigger)).toContain("helium");
	});

	it("returns the full list untouched for an empty query", () => {
		expect(filterFuzzy(items, "", (r) => [r.trigger])).toEqual(items);
		expect(filterFuzzy(items, "   ", (r) => [r.trigger])).toEqual(items);
	});

	it("finds typo-tolerant extras beyond the substring set", () => {
		const out = filterFuzzy(items, "helo", (r) => [r.trigger, r.expansion]);
		expect(out.map((r) => r.trigger)).toContain("hello");
	});

	it("caps non-empty results at the shared limit with stable order", () => {
		const many = Array.from({ length: 80 }, (_, i) => ({
			trigger: `item-${i}`,
			expansion: `expansion ${i}`,
		}));
		const first = filterFuzzy(many, "item", (r) => [r.trigger]);
		const second = filterFuzzy(many, "item", (r) => [r.trigger]);
		expect(first).toHaveLength(FUZZY_RESULT_LIMIT);
		expect(second.map((r) => r.trigger)).toEqual(
			first.map((r) => r.trigger),
		);
	});

	it("pins the shared tuning contract", () => {
		expect(FUZZY_THRESHOLD).toBeGreaterThanOrEqual(0.3);
		expect(FUZZY_THRESHOLD).toBeLessThanOrEqual(0.4);
		expect(FUZZY_RESULT_LIMIT).toBe(50);
	});
});
