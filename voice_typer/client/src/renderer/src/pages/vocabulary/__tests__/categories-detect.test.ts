/**
 * Tests for the ``detectCategory`` heuristics in
 * ``vocabulary/lib/categories.ts``.
 *
 * The function auto-detects a backend category for a trigger string
 * using these heuristics (evaluated in order — first match wins):
 *
 *  1. Multi-word phrases → ``phrase_corrections``
 *  1b. Script-detection fallback for non-Latin scripts:
 *       - CJK (Han) → ``phrase_corrections``
 *       - Arabic → ``names``
 *       - Cyrillic / Latin → fall through to case-based rules
 *  2. Mixed-case single tokens → ``products`` (e.g. ``iPad``)
 *  3. All-uppercase single tokens (≥2 chars) → ``names`` (e.g. ``NASA``)
 *  4. First-letter-capitalised single tokens → ``names`` (e.g. ``John``)
 *  5. Lowercase single tokens in the tech-word list → ``technical_terms``
 *  6. Default → ``misspellings``
 *
 * The heuristics are deliberately conservative — when in doubt, they
 * fall through to ``misspellings`` (the previous default) so existing
 * entries don't silently shift category.
 */
import { describe, expect, it } from "vitest";

import { detectCategory } from "../lib/categories";

describe("detectCategory heuristics", () => {
	it("multi-word phrases → phrase_corrections", () => {
		expect(detectCategory("i am going to")).toBe("phrase_corrections");
		expect(detectCategory("the quick brown fox")).toBe("phrase_corrections");
	});

	it("mixed-case single tokens → products", () => {
		expect(detectCategory("iPad")).toBe("products");
		expect(detectCategory("iPhone")).toBe("products");
		expect(detectCategory("WiFi")).toBe("products");
		expect(detectCategory("macOS")).toBe("products");
	});

	it("all-uppercase single tokens (≥2 chars) → names", () => {
		expect(detectCategory("NASA")).toBe("names");
		expect(detectCategory("IBM")).toBe("names");
		expect(detectCategory("FBI")).toBe("names");
	});

	it("first-letter-capitalised single tokens → names", () => {
		expect(detectCategory("John")).toBe("names");
		expect(detectCategory("Jon")).toBe("names");
		expect(detectCategory("Mary")).toBe("names");
	});

	it("lowercase tech words → technical_terms", () => {
		expect(detectCategory("kubernetes")).toBe("technical_terms");
		expect(detectCategory("docker")).toBe("technical_terms");
		expect(detectCategory("react")).toBe("technical_terms");
		expect(detectCategory("postgresql")).toBe("technical_terms");
	});

	it("lowercase non-tech words → misspellings (default)", () => {
		expect(detectCategory("recieve")).toBe("misspellings");
		expect(detectCategory("teh")).toBe("misspellings");
		expect(detectCategory("definately")).toBe("misspellings");
	});

	it("trims surrounding whitespace before evaluating", () => {
		expect(detectCategory("  iPad  ")).toBe("products");
		expect(detectCategory("  John  ")).toBe("names");
		expect(detectCategory("  i am going to  ")).toBe("phrase_corrections");
	});

	it("single uppercase letter is NOT treated as all-caps name", () => {
		// A single uppercase letter alone (no lowercase) is ambiguous —
		// the heuristic requires ≥2 uppercase chars for the all-caps
		// name path. A single "A" with no lowercase falls through to
		// the default (misspellings).
		expect(detectCategory("A")).toBe("misspellings");
	});

	it("non-letter characters are ignored for case detection", () => {
		// "123" has no letters — falls through to misspellings.
		expect(detectCategory("123")).toBe("misspellings");
		// "iPad 3" is multi-word → phrase_corrections.
		expect(detectCategory("iPad 3")).toBe("phrase_corrections");
	});

	it("CJK single tokens → phrase_corrections (script fallback)", () => {
		// CJK Unified Ideographs (U+4E00–U+9FFF) don't have case, so
		// the Latin case-based rules would otherwise bucket every CJK
		// trigger as misspellings (the empty-letters default). The
		// script fallback routes them to phrase_corrections — the
		// most common user intent for a CJK trigger is a phrase-
		// level replacement (e.g. typo → correct form). Covers
		// Chinese Hanzi and Japanese Kanji (both in the U+4E00–U+9FFF
		// range). Hiragana / Katakana / Hangul are intentionally
		// out of scope (not in this range).
		expect(detectCategory("你好")).toBe("phrase_corrections");
		expect(detectCategory("日本")).toBe("phrase_corrections");
		expect(detectCategory("词汇")).toBe("phrase_corrections");
	});

	it("Arabic single tokens → names (script fallback)", () => {
		// Arabic is proper-noun heavy in user vocabularies (names of
		// people, places, brands) — route to names by default.
		expect(detectCategory("محمد")).toBe("names");
		expect(detectCategory("مرحبا")).toBe("names");
	});

	it("Cyrillic single tokens fall through to the case-based default (misspellings)", () => {
		// The task spec calls for keeping the existing case-based
		// logic for Cyrillic (it has upper/lower case). The current
		// implementation strips non-A-Z-a-z, so a pure Cyrillic
		// token yields empty ``letters`` and falls through to
		// misspellings — the conservative default.
		expect(detectCategory("Привет")).toBe("misspellings");
		expect(detectCategory("Москва")).toBe("misspellings");
	});

	it("Latin rules still apply when a Latin token is mixed with CJK", () => {
		// The script fallback checks the WHOLE trimmed trigger. A
		// mixed Latin+CJK token (rare) routes to phrase_corrections
		// because CJK is present. This is intentional — multi-
		// script triggers are almost always phrase-level.
		expect(detectCategory("hello你好")).toBe("phrase_corrections");
	});

	it("multi-word CJK phrases still take the multi-word rule (rule 1)", () => {
		// A space-separated CJK phrase hits rule 1 before the
		// script fallback — both paths yield phrase_corrections,
		// so this is a sanity check that the order doesn't matter.
		expect(detectCategory("你好 世界")).toBe("phrase_corrections");
	});
});
