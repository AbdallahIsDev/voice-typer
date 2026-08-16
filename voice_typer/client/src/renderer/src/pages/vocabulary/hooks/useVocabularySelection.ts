// Selection state + bulk operations for the Vocabulary page.
//
// Owns:
//   - `selectedIds` (Set of row `_id`s) + toggle / select-all / clear
//   - `bulkDeleteSelected` — instant removal + 6s Undo toast that
//     restores every deleted row at its original position (mirrors the
//     single-row `instantDeleteEntry` pattern in useVocabulary)
//
// Kept in its own hook (rather than inside useVocabulary) so the
// selection Set churn doesn't re-render unrelated consumers.

import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";
import { showUndoableToast } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
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
	const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(
		new Set(),
	);

	const selectedCount = selectedIds.size;
	const selectedRows = useMemo(
		() => entries.filter((e) => selectedIds.has(e._id)),
		[entries, selectedIds],
	);

	const toggleSelect = useCallback((id: string) => {
		setSelectedIds((prev) => {
			const next = new Set(prev);
			if (next.has(id)) {
				next.delete(id);
			} else {
				next.add(id);
			}
			return next;
		});
	}, []);

	const setSelectMany = useCallback((ids: string[], selected: boolean) => {
		setSelectedIds((prev) => {
			const next = new Set(prev);
			for (const id of ids) {
				if (selected) {
					next.add(id);
				} else {
					next.delete(id);
				}
			}
			return next;
		});
	}, []);

	const clearSelection = useCallback(() => {
		setSelectedIds(new Set());
	}, []);

	const bulkDeleteSelected = useCallback(async () => {
		const rows = entriesRef.current.filter((e) => selectedIds.has(e._id));
		if (rows.length === 0) return;
		const byId = new Map(rows.map((e) => [e._id, e]));
		// Capture each deleted row's original index so Undo restores the
		// list exactly (mirrors instantDeleteEntry's index capture).
		const originalIndexes = new Map(
			rows.map((e) => [e._id, entriesRef.current.indexOf(e)]),
		);
		try {
			const updated = entriesRef.current.filter((e) => !selectedIds.has(e._id));
			setEntries(updated);
			// Keep the selection consistent — the deleted ids are gone.
			clearSelection();
			await persistVocabulary(updated);
			showUndoableToast(
				t("vocabulary.bulkDeleteToast", { count: String(rows.length) }),
				async () => {
					try {
						const latest = entriesRef.current.filter((e) => !byId.has(e._id));
						const restored = [...latest];
						for (const row of rows) {
							const insertAt = Math.min(
								originalIndexes.get(row._id) ?? restored.length,
								restored.length,
							);
							restored.splice(insertAt, 0, row);
						}
						await persistVocabulary(restored);
						setEntries(restored);
						toast.success(t("vocabulary.entryRestored"));
					} catch {
						toast.error(t("vocabulary.restoreFailed"));
					}
				},
				{ undoLabel: t("common.undo"), type: "warning", timeoutMs: 6000 },
			);
		} catch {
			// Restore the pre-delete list on failure (entriesRef still
			// holds the original list — persistVocabulary threw first).
			setEntries(entriesRef.current);
			showSnack(t("vocabulary.deleteFailed"), "error");
		}
	}, [
		entriesRef,
		selectedIds,
		setEntries,
		persistVocabulary,
		showSnack,
		clearSelection,
	]);

	return {
		selectedIds,
		selectedCount,
		selectedRows,
		toggleSelect,
		setSelectMany,
		clearSelection,
		bulkDeleteSelected,
	};
}
