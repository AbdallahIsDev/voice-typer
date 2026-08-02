/**
 * Tests for ``parseImportedVocabulary`` in
 * ``vocabulary/lib/importExport.ts``.
 *
 * Covers:
 *   - Bare-array import shape (the new export format).
 *   - Backend-shape VocabularyData import (the legacy / sync format).
 *   - CSV import (with / without header, with / without category column,
 *     with quoted fields — RFC 4180).
 *   - Invalid shape → throws an Error whose message is the localised
 *     ``vocabulary.importInvalidShape`` string (not a hardcoded
 *     English fallback).
 */
import { describe, expect, it } from "vitest";

import { parseImportedVocabulary } from "../lib/importExport";

describe("parseImportedVocabulary", () => {
	it("parses a bare array of {original, correction, category?} objects", () => {
		const text = JSON.stringify([
			{ original: "recieve", correction: "receive" },
			{ original: "teh", correction: "the", category: "misspellings" },
			{ original: "i am going to", correction: "I'm going to" },
		]);
		const result = parseImportedVocabulary(text);
		expect(result).toHaveLength(3);
		expect(result[0]).toEqual({
			original: "recieve",
			correction: "receive",
			category: "misspellings",
		});
		expect(result[1]).toEqual({
			original: "teh",
			correction: "the",
			category: "misspellings",
		});
		// Multi-word → phrase_corrections (detectCategory heuristic).
		expect(result[2]?.category).toBe("phrase_corrections");
	});

	it("parses a backend-shape VocabularyData object", () => {
		const text = JSON.stringify({
			misspellings: { recieve: "receive" },
			phrase_corrections: [["i am going to", "I'm going to"]],
		});
		const result = parseImportedVocabulary(text);
		expect(result).toHaveLength(2);
		expect(result.some((e) => e.original === "recieve")).toBe(true);
		expect(result.some((e) => e.original === "i am going to")).toBe(true);
	});

	it("throws a localised error for an invalid shape (not a hardcoded English string)", () => {
		// A bare string is neither an array nor a VocabularyData object.
		// Note: it starts with `"` so it goes through the JSON branch.
		expect(() => parseImportedVocabulary(JSON.stringify("hello"))).toThrow(
			/File does not contain a vocabulary array or data object/,
		);
	});

	it("throws a localised error for a number", () => {
		expect(() => parseImportedVocabulary(JSON.stringify(42))).toThrow(
			/File does not contain a vocabulary array or data object/,
		);
	});

	it("throws on malformed JSON that does not look like CSV either", () => {
		// `not valid json` doesn't start with `[` or `{` so it falls
		// into the CSV branch, where it yields zero valid rows (no
		// comma → fewer than 2 fields per line). The CSV branch
		// throws the same localised shape-error message.
		expect(() => parseImportedVocabulary("not valid json")).toThrow(
			/File does not contain a vocabulary array or data object/,
		);
	});

	it("parses a CSV without a header (2-column original,correction)", () => {
		const csv = "recieve,receive\nteh,the\n";
		const result = parseImportedVocabulary(csv);
		expect(result).toHaveLength(2);
		expect(result[0]).toEqual({
			original: "recieve",
			correction: "receive",
			category: "misspellings",
		});
		expect(result[1]).toEqual({
			original: "teh",
			correction: "the",
			category: "misspellings",
		});
	});

	it("parses a CSV with a header row (original,correction)", () => {
		const csv = "original,correction\nrecieve,receive\nNASA,John\n";
		const result = parseImportedVocabulary(csv);
		expect(result).toHaveLength(2);
		expect(result[0]?.original).toBe("recieve");
		// NASA → names (all-uppercase ≥2 chars).
		expect(result[1]?.category).toBe("names");
	});

	it("parses a CSV with a 3rd category column", () => {
		const csv =
			"original,correction,category\nrecieve,receive,misspellings\nNASA,John,names\n";
		const result = parseImportedVocabulary(csv);
		expect(result).toHaveLength(2);
		expect(result[0]?.category).toBe("misspellings");
		expect(result[1]?.category).toBe("names");
	});

	it("auto-detects the category when the 3rd column is unknown", () => {
		const csv =
			"original,correction,category\nrecieve,receive,not_a_real_category\n";
		const result = parseImportedVocabulary(csv);
		expect(result).toHaveLength(1);
		// Unknown category → fall back to detectCategory("recieve")
		// → misspellings.
		expect(result[0]?.category).toBe("misspellings");
	});

	it("handles RFC 4180 quoted fields (commas, escaped quotes)", () => {
		// A row whose original contains a comma, one whose correction
		// contains a double-quote (escaped as `""`).
		const csv =
			'original,correction\n"hello, world","hi there"\n"quote ""inside""","out"\n';
		const result = parseImportedVocabulary(csv);
		expect(result).toHaveLength(2);
		expect(result[0]?.original).toBe("hello, world");
		expect(result[0]?.correction).toBe("hi there");
		expect(result[1]?.original).toBe('quote "inside"');
		expect(result[1]?.correction).toBe("out");
	});

	it("skips blank lines in CSV input", () => {
		const csv = "recieve,receive\n\nteh,the\n";
		const result = parseImportedVocabulary(csv);
		expect(result).toHaveLength(2);
	});

	it("throws on a CSV that yields zero valid rows", () => {
		// Each line has only one field — fewer than 2 → skipped.
		expect(() => parseImportedVocabulary("justoneword\nanotherword\n")).toThrow(
			/File does not contain a vocabulary array or data object/,
		);
	});

	it("handles CRLF line endings in CSV", () => {
		const csv = "original,correction\r\nrecieve,receive\r\nteh,the\r\n";
		const result = parseImportedVocabulary(csv);
		expect(result).toHaveLength(2);
		expect(result[0]?.original).toBe("recieve");
		expect(result[1]?.original).toBe("teh");
	});
});
