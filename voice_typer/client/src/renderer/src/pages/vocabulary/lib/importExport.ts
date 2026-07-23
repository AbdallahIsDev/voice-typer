// Import parser for vocabulary files.
//
// Extracted from the former monolithic ``pages/Vocabulary.tsx`` so the
// import hook can call it without dragging in the React state layer.
//
// Accepts:
//  - A bare JSON array of ``{original, correction, category?}`` objects
//    (the new export shape — see ``useVocabularyImportExport``).
//  - A backend-shape ``VocabularyData`` object (the legacy / sync
//    export shape) — flattened via ``flattenEntries``.
//
// Throws on malformed JSON or unknown shape so the caller can surface
// a toast.error with the parse failure reason.

import type { VocabularyData, VocabularyEntry } from "@/types/ipc";

import { CATEGORIES, detectCategory } from "./categories";
import { flattenEntries } from "./transform";

export function parseImportedVocabulary(text: string): VocabularyEntry[] {
	const parsed = JSON.parse(text) as unknown;
	if (Array.isArray(parsed)) {
		return parsed
			.filter(
				(
					e: unknown,
				): e is {
					original: unknown;
					correction: unknown;
					category?: unknown;
				} => typeof e === "object" && e !== null,
			)
			.map((e) => ({
				original: typeof e.original === "string" ? e.original : "",
				correction: typeof e.correction === "string" ? e.correction : "",
				category:
					typeof e.category === "string" &&
					CATEGORIES.includes(e.category as (typeof CATEGORIES)[number])
						? e.category
						: detectCategory(typeof e.original === "string" ? e.original : ""),
			}));
	}
	if (parsed && typeof parsed === "object") {
		// Backend-shape VocabularyData — flatten it.
		return flattenEntries(parsed as VocabularyData);
	}
	throw new Error("File does not contain a vocabulary array or data object");
}
