/**
 * Tests for ``parseImportedVocabulary`` in
 * ``vocabulary/lib/importExport.ts``.
 *
 * Covers:
 *   - Bare-array import shape (the new export format).
 *   - Backend-shape VocabularyData import (the legacy / sync format).
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
		expect(result[2].category).toBe("phrase_corrections");
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
		expect(() => parseImportedVocabulary(JSON.stringify("hello"))).toThrow(
			/File does not contain a vocabulary array or data object/,
		);
	});

	it("throws a localised error for a number", () => {
		expect(() => parseImportedVocabulary(JSON.stringify(42))).toThrow(
			/File does not contain a vocabulary array or data object/,
		);
	});

	it("throws on malformed JSON", () => {
		expect(() => parseImportedVocabulary("not valid json")).toThrow(
			SyntaxError,
		);
	});
});
