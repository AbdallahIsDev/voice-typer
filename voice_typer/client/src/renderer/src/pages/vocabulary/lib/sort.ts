// Client-side sort for vocabulary entries.
//
// Extracted from the former monolithic ``pages/Vocabulary.tsx``.  Mirrors
// the History/Templates sort pattern — the backend returns entries in
// category-bucket order, so "newest"/"oldest" are identity / reverse of
// the loaded array (the backend doesn't expose per-entry timestamps, so
// we approximate "newest" as "last in the flattened array" = most
// recently added under the existing add-to-end semantics).
//
// Generic over ``T`` so callers passing ``VocabRow`` (VocabularyEntry
// + ``_id``) get back ``VocabRow[]`` — preserving the stable UUID
// through the sort so it can be used as the React key.
//
// Uses ``getLocale()`` for the A→Z / Z→A collation so accented
// characters sort correctly in non-English locales.

import { getLocale } from "@/i18n/i18n";
import type { VocabularyEntry } from "@/types/ipc";

export type VocabSortOrder = "newest" | "oldest" | "az" | "za";

export function sortEntries<T extends VocabularyEntry>(
	items: T[],
	order: VocabSortOrder,
): T[] {
	const locale = getLocale();
	const collator = new Intl.Collator(locale, {
		sensitivity: "base",
		numeric: true,
	});
	const sorted = [...items];
	switch (order) {
		case "oldest":
			// Flattened backend order = oldest first; identity.
			break;
		case "az":
			sorted.sort((a, b) =>
				collator.compare(a.original ?? "", b.original ?? ""),
			);
			break;
		case "za":
			sorted.sort((a, b) =>
				collator.compare(b.original ?? "", a.original ?? ""),
			);
			break;
		default:
			// Reverse so the most-recently-added entry appears at the top.
			sorted.reverse();
			break;
	}
	return sorted;
}
