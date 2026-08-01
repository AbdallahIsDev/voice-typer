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

/** Auto-detect category: phrases (spaces) go to phrase_corrections, single words to misspellings. */
export function detectCategory(
	trigger: string,
): "misspellings" | "phrase_corrections" {
	return trigger.includes(" ") ? "phrase_corrections" : "misspellings";
}
