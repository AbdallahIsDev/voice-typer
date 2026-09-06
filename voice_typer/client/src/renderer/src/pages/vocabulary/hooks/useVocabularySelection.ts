// Selection state + bulk operations for the Vocabulary page.
//
// Thin feature wrapper over the shared :func:`useRowSelection` hook —
// supplies the Vocabulary row shape (`_id` id field), the persist
// callback, and the vocabulary message keys. All selection/undo logic
// lives in the shared hook (formerly a 1:1 copy of this page's logic).

import { useCallback } from "react";
import { useRowSelection } from "@/hooks/useRowSelection";
import type { VocabRow } from "../lib/transform";

interface UseVocabularySelectionArgs {
	entries: VocabRow[];
	setEntries: (entries: VocabRow[]) => void;
	entriesRef: React.RefObject<VocabRow[]>;
	persistVocabulary: (updated: VocabRow[]) => Promise<void>;
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
	) => void;
}

interface UseVocabularySelectionResult {
	selectedIds: ReadonlySet<string>;
	selectedCount: number;
	selectedRows: VocabRow[];
	toggleSelect: (id: string) => void;
	/** Select (or clear) a specific set of ids — used by select-all. */
	setSelectMany: (ids: string[], selected: boolean) => void;
	clearSelection: () => void;
	bulkDeleteSelected: () => Promise<void>;
}

export function useVocabularySelection({
	entries,
	setEntries,
	entriesRef,
	persistVocabulary,
	showSnack,
}: UseVocabularySelectionArgs): UseVocabularySelectionResult {
	const getRowId = useCallback((row: VocabRow) => row._id, []);
	const messages = {
		bulkDeleteToast: "vocabulary.bulkDeleteToast",
		rowRestored: "vocabulary.entryRestored",
		restoreFailed: "vocabulary.restoreFailed",
		deleteFailed: "vocabulary.deleteFailed",
	};

	return useRowSelection<VocabRow>({
		rows: entries,
		setRows: setEntries,
		rowsRef: entriesRef,
		persist: persistVocabulary,
		showSnack,
		getRowId,
		messages,
	});
}
