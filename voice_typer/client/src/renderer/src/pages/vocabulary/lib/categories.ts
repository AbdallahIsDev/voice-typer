// Backend vocabulary categories.
//
// Categories are part of the persisted data layer ONLY — the UI no
// longer surfaces them (the Vocabulary page is a flat two-column
// correction list). This module keeps the canonical category list the
// flatten/rebuild transforms and the import parser need, plus the
// auto-detect heuristic that assigns new entries to a sensible bucket
// so saved data stays well-formed for the backend.
//
// ``getCategoryLabels`` / ``CATEGORY_META`` were removed when the
// category UI (badges, group headers, filter, picker, bulk move) was
// deleted — see archive/deleted_files.txt.

import { normalizeWrongPhrase, type VocabRow } from "./transform";

// ── Backend categories (kept internally for save-back, hidden from UI) ──
export const CATEGORIES = [
	"misspellings",
	"phrase_corrections",
	"extra_word_patterns",
	"technical_terms",
	"names",
	"products",
] as const;

export type VocabCategory = (typeof CATEGORIES)[number];

/**
 * Frontend pre-check for the quick-add row: find an existing entry
 * whose wrong phrase collides with *original* (case-insensitive,
 * whitespace-collapsed — the same rule the backend enforces
 * authoritatively in ``save_vocabulary_with_diff``).
 *
 * This is a CONVENIENCE layer only: the authoritative check that
 * blocks the write lives in the backend write path, so every entry
 * point (quick-add, edit dialog, import) is covered even if this
 * pre-check is bypassed. Returns the first colliding entry, or
 * undefined when the phrase is new.
 */
export function findDuplicate(
	entries: ReadonlyArray<
		Pick<VocabRow, "original" | "correction" | "category">
	>,
	original: string,
) {
	const key = normalizeWrongPhrase(original);
	return entries.find((it) => normalizeWrongPhrase(it.original) === key);
}

// Auto-detect heuristics (conservative — when in doubt, fall through to
// misspellings so existing entries don't silently shift category):
//   1. Multi-word phrases → phrase_corrections
//   1b. Script-detection fallback for non-Latin scripts:
//        - CJK (Han) → phrase_corrections (case-based rules don't apply)
//        - Arabic → names (proper-noun heavy in user vocabularies)
//        - Cyrillic / Latin → fall through to case-based rules
//   2. Mixed-case single tokens → products (e.g. WiFi, macOS, iPhone)
//   3. All-uppercase single tokens (≥2 chars) → names (e.g. NASA)
//   4. First-letter-capitalised single tokens → names (e.g. John)
//   5. Lowercase single tokens in the tech-word list → technical_terms
//   6. Default → misspellings
const TECH_WORDS: ReadonlySet<string> = new Set([
	"kubernetes",
	"docker",
	"react",
	"postgresql",
]);

// A few CamelCase trademarks read as first-letter-capitalised names by
// rule 4 but are product names. Kept small + explicit so rule 4 stays
// the default for genuinely-unknown CamelCase words.
const PRODUCT_EXAMPLES: ReadonlySet<string> = new Set(["ipad", "iphone"]);

// Unicode script ranges used by the locale-aware fallback (rule 1b).
// CJK Unified Ideographs cover the vast majority of Chinese / Japanese
// Kanji; Arabic covers the core block; Cyrillic is intentionally NOT
// special-cased here (it has upper/lower case, so the case-based rules
// below apply directly — but those rules currently strip non-A-Z-a-z,
// so Cyrillic falls through to the misspellings default, which is the
// conservative behaviour we want for an unknown Cyrillic trigger).
const CJK_RANGE = /[\u4e00-\u9fff]/;
const ARABIC_RANGE = /[\u0600-\u06ff]/;

export type DetectCategoryResult =
	| "misspellings"
	| "phrase_corrections"
	| "technical_terms"
	| "names"
	| "products";

/** Auto-detect a vocabulary category for a trigger string. */
export function detectCategory(trigger: string): DetectCategoryResult {
	const trimmed = trigger.trim();

	// Rule 1: multi-word phrases.
	if (trimmed.includes(" ")) return "phrase_corrections";

	// Rule 1b: script-detection fallback for non-Latin scripts.
	// The case-based rules below only meaningfully classify Latin
	// and Cyrillic inputs (they have case). CJK and Arabic don't —
	// without this fallback, every CJK / Arabic trigger was being
	// bucketed as ``misspellings`` (the empty-letters default),
	// which is almost never correct for those scripts.
	if (CJK_RANGE.test(trimmed)) return "phrase_corrections";
	if (ARABIC_RANGE.test(trimmed)) return "names";

	const letters = trimmed.replace(/[^A-Za-z]/g, "");
	if (!letters) return "misspellings"; // e.g. "123"

	const hasUpper = /[A-Z]/.test(letters);
	const hasLower = /[a-z]/.test(letters);
	const upperCount = (letters.match(/[A-Z]/g) ?? []).length;

	if (hasUpper && hasLower) {
		// Rule 3: all-caps names handled below (no lowercase there).
		// Mixed case:
		//   - an uppercase letter at index ≥1 (internal caps) → product
		//     (WiFi, macOS, iPhone)
		//   - exactly one uppercase at index 0 → rule 4 (names) unless a
		//     known product example (iPad / iPhone)
		const firstCharUpper = /^[A-Z]/.test(letters);
		const internalCaps = /[A-Z]/.test(letters.slice(1));
		if (internalCaps || upperCount > 1) return "products";
		if (firstCharUpper) {
			if (PRODUCT_EXAMPLES.has(letters.toLowerCase())) return "products";
			return "names"; // John / Jon / Mary
		}
		// e.g. "macOS" already caught by internalCaps; anything else
		// unusual with mixed case defaults to products.
		return "products";
	}

	if (hasUpper) {
		// All-uppercase (no lowercase).
		return letters.length >= 2 ? "names" : "misspellings"; // NASA vs "A"
	}

	// All lowercase.
	if (TECH_WORDS.has(letters)) return "technical_terms";
	return "misspellings";
}
