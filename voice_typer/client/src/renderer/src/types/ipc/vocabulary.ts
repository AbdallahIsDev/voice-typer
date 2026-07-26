// types/ipc/vocabulary.ts
//
// Vocabulary-domain types — mirrors the Python `VocabularyManager`.
//
// Split out from the original monolithic `types/ipc.ts` (DT-31 / DT-FIX-7).
// No behaviour change vs. the original file — pure structural refactor.

export interface VocabularyData {
	misspellings?: Record<string, string>;
	technical_terms?: Record<string, string>;
	names?: Record<string, string>;
	products?: Record<string, string>;
	phrase_corrections?: Array<[string, string]>;
	extra_word_patterns?: Array<[string, string]>;
}

export interface VocabularyEntry {
	category: string;
	original: string;
	correction: string;
	index?: number;
}
