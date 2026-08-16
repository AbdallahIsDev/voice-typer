/**
 * Unit tests for the client-side "Test corrections" engine
 * (lib/testCorrection.ts) — a mirror of the SERVER's vocabulary
 * application rules (voice_typer/server/vocabulary.py
 * `apply_to_text`), so the Vocabulary page can preview corrections
 * without a backend round-trip.
 *
 * Covered semantics (mirrored from the server):
 *   - phrase-level (phrase_corrections / extra_word_patterns):
 *     case-insensitive, literal (non-regex) replacement, longer
 *     phrases applied FIRST (the server sorts by length desc)
 *   - word-level (misspellings / technical_terms / names / products):
 *     tokenized on spaces, leading/trailing punctuation stripped for
 *     the lookup key and re-wrapped around the correction
 */
import { describe, expect, it } from "vitest";

import type { VocabularyEntry } from "@/types/ipc";

import { applyCorrections } from "../lib/testCorrection";

const entry = (
	category: VocabularyEntry["category"],
	original: string,
	correction: string,
): VocabularyEntry => ({ category, original, correction });

describe("applyCorrections — word-level (misspellings)", () => {
	it("replaces a whole token and re-wraps its punctuation", () => {
		const entries = [entry("misspellings", "recieve", "receive")];
		expect(applyCorrections("He said recieve.", entries)).toEqual({
			output: "He said receive.",
			applied: true,
		});
	});

	it("matches case-insensitively", () => {
		const entries = [entry("misspellings", "recieve", "receive")];
		expect(applyCorrections("RECIEVE and recieve", entries).output).toBe(
			"receive and receive",
		);
	});

	it("does NOT fire mid-token (only whole words are replaced)", () => {
		const entries = [entry("misspellings", "teh", "the")];
		expect(applyCorrections("the xtehx", entries)).toEqual({
			output: "the xtehx",
			applied: false,
		});
	});
});

describe("applyCorrections — phrase-level (phrase_corrections)", () => {
	it("replaces a multi-word phrase case-insensitively, verbatim correction", () => {
		const entries = [
			entry("phrase_corrections", "i am going to", "I'm going to"),
		];
		expect(applyCorrections("I AM GOING TO the store", entries)).toEqual({
			output: "I'm going to the store",
			applied: true,
		});
	});

	it("applies longer phrases before shorter ones (server sorts by length desc)", () => {
		const entries = [
			entry("phrase_corrections", "i am going to", "I'm going to"),
			entry("phrase_corrections", "going to", "gonna"),
		];
		// Longer phrase first: "i am going to" → "I'm going to", THEN the
		// shorter "going to" still matches inside the replacement.
		expect(applyCorrections("I am going to work", entries).output).toBe(
			"I'm gonna work",
		);
	});
});

describe("applyCorrections — extra word patterns (removal)", () => {
	it("removes filler words (empty correction)", () => {
		const entries = [entry("extra_word_patterns", "um", "")];
		expect(applyCorrections("um, I think so", entries).output).toBe(
			", I think so",
		);
		expect(applyCorrections("um, I think so", entries).applied).toBe(true);
	});
});

describe("applyCorrections — no-op paths", () => {
	it("returns the input unchanged when nothing matches", () => {
		const entries = [entry("misspellings", "recieve", "receive")];
		expect(applyCorrections("hello world", entries)).toEqual({
			output: "hello world",
			applied: false,
		});
	});

	it("handles empty text and empty entries", () => {
		expect(applyCorrections("", [entry("misspellings", "a", "b")])).toEqual({
			output: "",
			applied: false,
		});
		expect(applyCorrections("anything", [])).toEqual({
			output: "anything",
			applied: false,
		});
	});
});
