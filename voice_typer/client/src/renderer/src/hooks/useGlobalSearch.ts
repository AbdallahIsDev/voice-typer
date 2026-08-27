import { create } from "zustand";

interface GlobalSearchState {
	query: string;
	setQuery: (q: string) => void;
	clearQuery: () => void;
	/** The current vocabulary entry count, synced by the Vocabulary page
	 *  so the title-bar placeholder can show "Search {count} corrections". */
	vocabEntryCount: number;
	setVocabEntryCount: (n: number) => void;
}

export const useGlobalSearch = create<GlobalSearchState>((set) => ({
	query: "",
	setQuery: (q: string) => set({ query: q }),
	clearQuery: () => set({ query: "" }),
	vocabEntryCount: 0,
	setVocabEntryCount: (n: number) => set({ vocabEntryCount: n }),
}));
