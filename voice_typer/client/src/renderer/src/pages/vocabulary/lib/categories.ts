// Backend vocabulary categories + locale-aware label resolution.
//
// Extracted from the former monolithic ``pages/Vocabulary.tsx`` so the
// search/filter bar, list-row badge, and dialog picker can share one
// canonical category list (without re-declaring the 6 backend category
// keys — which previously drifted between the filter dropdown, the
// dialog picker, and the flatten/rebuild transforms).
//
//``getCategoryLabels`` is a FUNCTION (not a const) so the
// labels are re-resolved via ``t()`` on every render. If it were a
// module-level const, a locale switch at runtime would leave the UI
// showing the OLD locale's labels until the next page mount.

import { t } from "@/i18n/i18n";

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

//getCategoryLabels() — called at render time so locale
// switches re-resolve the t() keys against the new locale.
export function getCategoryLabels(): Record<
	string,
	{ label: string; description: string; example: string }
> {
	return {
		misspellings: {
			label: t("vocabulary.category.misspellings"),
			description: t("vocabulary.category.misspellingsDesc"),
			example: "recieve \u2192 receive",
		},
		phrase_corrections: {
			label: t("vocabulary.category.phraseCorrections"),
			description: t("vocabulary.category.phraseCorrectionsDesc"),
			example: "i am going to \u2192 I'm going to",
		},
		extra_word_patterns: {
			label: t("vocabulary.category.extraWordPatterns"),
			description: t("vocabulary.category.extraWordPatternsDesc"),
			example: "um, uh, like \u2192 (removed)",
		},
		technical_terms: {
			label: t("vocabulary.category.technicalTerms"),
			description: t("vocabulary.category.technicalTermsDesc"),
			example: "kubernetes \u2192 Kubernetes",
		},
		names: {
			label: t("vocabulary.category.names"),
			description: t("vocabulary.category.namesDesc"),
			example: "jon \u2192 John",
		},
		products: {
			label: t("vocabulary.category.products"),
			description: t("vocabulary.category.productsDesc"),
			example: "ipad \u2192 iPad",
		},
	};
}

// Auto-detect heuristics (conservative — when in doubt, fall through to
// misspellings so existing entries don't silently shift category):
//   1. Multi-word phrases → phrase_corrections
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
