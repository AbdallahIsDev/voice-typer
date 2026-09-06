// Generic selection state + bulk-delete-with-undo, shared by the
// Vocabulary and Templates pages (previously a 1:1 copy-paste fork —
// useVocabularySelection / useTemplateSelection differed only in the row
// id-field name, the persist callback, and the i18n message keys).
//
// Owns:
//   - `selectedIds` (Set of row ids) + toggle / select-many / clear
//   - `bulkDeleteSelected` — instant removal + 6s Undo toast that
//     restores every deleted row at its original position
//
// Kept generic over the row type (`getRowId` accessor) so each page
// keeps its own data shape (Vocabulary uses `_id`, Templates uses `id`)
// and its own message keys — the LOGIC is single-sourced here.
//
// Kept in a dedicated hook (rather than inside the page data hooks) so
// the selection Set churn doesn't re-render unrelated consumers.

import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";
import { showUndoableToast } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";

interface UseRowSelectionArgs<Row> {
	rows: Row[];
	setRows: (rows: Row[]) => void;
	rowsRef: React.RefObject<Row[]>;
	persist: (updated: Row[]) => Promise<void>;
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
	) => void;
	/** Extract the stable row id (Vocabulary `_id`, Templates `id`). */
	getRowId: (row: Row) => string;
	/** i18n message keys — the pages' copy differs per feature. */
	messages: {
		bulkDeleteToast: string;
		rowRestored: string;
		restoreFailed: string;
		deleteFailed: string;
	};
}

interface UseRowSelectionResult<Row> {
	selectedIds: ReadonlySet<string>;
	selectedCount: number;
	selectedRows: Row[];
	toggleSelect: (id: string) => void;
	/** Select (or clear) a specific set of ids — used by select-all. */
	setSelectMany: (ids: string[], selected: boolean) => void;
	clearSelection: () => void;
	bulkDeleteSelected: () => Promise<void>;
}

export function useRowSelection<Row>({
	rows,
	setRows,
	rowsRef,
	persist,
	showSnack,
	getRowId,
	messages,
}: UseRowSelectionArgs<Row>): UseRowSelectionResult<Row> {
	const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(
		new Set(),
	);

	const selectedCount = selectedIds.size;
	const selectedRows = useMemo(
		() => rows.filter((e) => selectedIds.has(getRowId(e))),
		[rows, selectedIds, getRowId],
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
		const deletedRows = rowsRef.current.filter((e) =>
			selectedIds.has(getRowId(e)),
		);
		if (deletedRows.length === 0) return;
		const deletedIds = new Set(deletedRows.map(getRowId));
		// Capture each deleted row's original index so Undo restores the
		// list exactly.
		const originalIndexes = new Map(
			deletedRows.map((e) => [getRowId(e), rowsRef.current.indexOf(e)]),
		);
		try {
			const updated = rowsRef.current.filter(
				(e) => !selectedIds.has(getRowId(e)),
			);
			setRows(updated);
			// Keep the selection consistent — the deleted ids are gone.
			clearSelection();
			await persist(updated);
			showUndoableToast(
				t(messages.bulkDeleteToast, { count: String(deletedRows.length) }),
				async () => {
					try {
						const latest = rowsRef.current.filter(
							(e) => !deletedIds.has(getRowId(e)),
						);
						const restored = [...latest];
						for (const row of deletedRows) {
							const insertAt = Math.min(
								originalIndexes.get(getRowId(row)) ?? restored.length,
								restored.length,
							);
							restored.splice(insertAt, 0, row);
						}
						await persist(restored);
						setRows(restored);
						toast.success(t(messages.rowRestored));
					} catch {
						toast.error(t(messages.restoreFailed));
					}
				},
				{ undoLabel: t("common.undo"), type: "warning", timeoutMs: 6000 },
			);
		} catch {
			// Restore the pre-delete list on failure (rowsRef still holds
			// the original list — persist threw first).
			setRows(rowsRef.current);
			showSnack(t(messages.deleteFailed), "error");
		}
	}, [
		rowsRef,
		selectedIds,
		getRowId,
		setRows,
		persist,
		showSnack,
		clearSelection,
		messages,
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
