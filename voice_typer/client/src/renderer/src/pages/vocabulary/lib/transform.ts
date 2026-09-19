// Pure transforms between persisted + React-side vocabulary shapes.
// helpers (flattenEntries, rebuildData, withEntryIds, makeEntryId).
// Kept side-effect-free so the storage / hook / component layers can
// share one definition of "how to map between the backend shape
// (category-bucketed VocabularyData) and the flat entry view-model"
// without re-implementing it (and drifting).

import type { VocabularyData, VocabularyEntry } from "@/types/ipc";

import { CATEGORIES } from "./categories";

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

export function dedupeEntries(entries: VocabularyEntry[]): {
	entries: VocabularyEntry[];
	mergedCount: number;
} {
	const seen = new Set<string>();
	const unique: VocabularyEntry[] = [];
	let mergedCount = 0;
	for (const e of entries) {
		const key = `${e.original}\u0000${e.correction}`;
		if (seen.has(key)) {
			mergedCount++;
			continue;
		}
		seen.add(key);
		unique.push(e);
	}
	return { entries: unique, mergedCount };
}

export function normalizeWrongPhrase(phrase: string): string {
	return phrase.trim().replace(/\s+/g, " ").toLowerCase();
}

export function findDuplicateGroups(
	entries: ReadonlyArray<Pick<VocabRow, "original" | "correction">>,
): Array<{ phrase: string; entries: Array<VocabRow> }> {
	const groups = new Map<string, VocabRow[]>();
	for (const e of entries) {
		const key = normalizeWrongPhrase(e.original);
		const list = groups.get(key);
		if (list) {
			list.push(e as VocabRow);
		} else {
			groups.set(key, [e as VocabRow]);
		}
	}
	return Array.from(groups.entries())
		.filter(([, list]) => list.length >= 2)
		.map(([phrase, list]) => ({ phrase, entries: list }));
}

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

export function withEntryIds(entries: VocabularyEntry[]): VocabRow[] {
	return entries.map((e) => ({ ...e, _id: makeEntryId() }));
}
