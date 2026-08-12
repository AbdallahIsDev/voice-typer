// Pure transforms between persisted + React-side vocabulary shapes.
//
// Extracted from the former ``pages/Vocabulary.tsx`` module-level
// helpers (flattenEntries, rebuildData, withEntryIds, makeEntryId).
// Kept side-effect-free so the storage / hook / component layers can
// share one definition of "how to map between the backend shape
// (category-bucketed VocabularyData) and the flat entry view-model"
// without re-implementing it (and drifting).

import type { VocabularyData, VocabularyEntry } from "@/types/ipc";

import { CATEGORIES } from "./categories";

/**
 * React-side view of a vocabulary entry.  Extends ``VocabularyEntry``
 * with a stable client-side UUID (``_id``) used as the React key.  The
 * UUID is not persisted — it's regenerated on every load — so list
 * re-orders (sort, filter, undo-restore) don't reuse DOM nodes across
 * different entries.
 */
export type VocabRow = VocabularyEntry & { _id: string };

/** Flatten category-shaped VocabularyData into a flat array. */
export function flattenEntries(data: VocabularyData): VocabularyEntry[] {
	const items: VocabularyEntry[] = [];
	for (const cat of CATEGORIES) {
		const catData = (data as Record<string, unknown>)[cat];
		if (
			cat === "misspellings" ||
			cat === "technical_terms" ||
			cat === "names" ||
			cat === "products"
		) {
			if (typeof catData === "object" && catData !== null) {
				for (const [key, val] of Object.entries(
					catData as Record<string, string>,
				)) {
					items.push({ category: cat, original: key, correction: String(val) });
				}
			}
		} else if (cat === "phrase_corrections" || cat === "extra_word_patterns") {
			if (Array.isArray(catData)) {
				for (const entry of catData) {
					if (Array.isArray(entry) && entry.length >= 2) {
						items.push({
							category: cat,
							original: entry[0] as string,
							correction: entry[1] as string,
						});
					}
				}
			}
		}
	}
	return items;
}

/** Rebuild category-shaped VocabularyData from a flat array for server save. */
export function rebuildData(entries: VocabularyEntry[]): VocabularyData {
	const data: VocabularyData = {};
	for (const cat of CATEGORIES) {
		const filtered = entries.filter((e) => e.category === cat);
		if (
			cat === "misspellings" ||
			cat === "technical_terms" ||
			cat === "names" ||
			cat === "products"
		) {
			const dict: Record<string, string> = {};
			for (const e of filtered) {
				dict[e.original] = e.correction;
			}
			data[cat] = dict;
		} else {
			data[cat] = filtered.map(
				(e) => [e.original, e.correction] as [string, string],
			);
		}
	}
	return data;
}

/**
 * Generate a stable UUID for a vocabulary row.  Used as the React key
 * instead of ``${original}-${category}`` (the previous key) which
 * collided when the user added the same trigger word to two different
 * categories, or broke (re-rendered the wrong row) when an edit
 * changed the original text.  Falls back to a ``Math.random``-based
 * pseudo-ID when ``crypto.randomUUID`` is unavailable (sandboxed
 * tests).
 */
export function makeEntryId(): string {
	try {
		if (
			typeof crypto !== "undefined" &&
			typeof crypto.randomUUID === "function"
		) {
			return crypto.randomUUID();
		}
	} catch (e) {
		// crypto may be undefined in some test environments.
		// Fall through to the Math.random-based pseudo-ID below.
		console.warn(
			"[renderer:transform] crypto.randomUUID unavailable, falling back:",
			e,
		);
	}
	return `entry-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}

/**
 * Attach a stable client-side UUID to each entry.  The UUID is not
 * persisted — it's regenerated on every load and used only as a React
 * key so list re-orders (sort, filter, undo-restore) don't reuse DOM
 * nodes across different entries.
 */
export function withEntryIds(entries: VocabularyEntry[]): VocabRow[] {
	return entries.map((e) => ({ ...e, _id: makeEntryId() }));
}
