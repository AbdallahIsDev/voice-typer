// Selection state + bulk operations for the Templates page — mirrors
// the Vocabulary page's useVocabularySelection exactly (the Templates
// UI is a 1:1 copy of the Vocabulary UI; only the row data shape
// differs).
//
// Owns:
//   - `selectedIds` (Set of row `id`s) + toggle / select-all / clear
//   - `bulkDeleteSelected` — instant removal + 6s Undo toast that
//     restores every deleted row at its original position (mirrors the
//     single-row `instantDeleteTemplate` pattern in useTemplates)
//
// Kept in its own hook (rather than inside useTemplates) so the
// selection Set churn doesn't re-render unrelated consumers.

import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";
import { showUndoableToast } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import type { TemplateRow } from "../lib/types";

interface UseTemplateSelectionArgs {
	templates: TemplateRow[];
	setTemplates: (templates: TemplateRow[]) => void;
	templatesRef: React.RefObject<TemplateRow[]>;
	saveTemplatesList: (updated: TemplateRow[]) => Promise<void>;
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
	) => void;
}

interface UseTemplateSelectionResult {
	selectedIds: ReadonlySet<string>;
	selectedCount: number;
	selectedRows: TemplateRow[];
	toggleSelect: (id: string) => void;
	/** Select (or clear) a specific set of ids — used by select-all. */
	setSelectMany: (ids: string[], selected: boolean) => void;
	clearSelection: () => void;
	bulkDeleteSelected: () => Promise<void>;
}
export function useTemplateSelection({
	templates,
	setTemplates,
	templatesRef,
	saveTemplatesList,
	showSnack,
}: UseTemplateSelectionArgs): UseTemplateSelectionResult {
	const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(
		new Set(),
	);

	const selectedCount = selectedIds.size;
	const selectedRows = useMemo(
		() => templates.filter((e) => selectedIds.has(e.id)),
		[templates, selectedIds],
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
		const rows = templatesRef.current.filter((e) => selectedIds.has(e.id));
		if (rows.length === 0) return;
		const byId = new Map(rows.map((e) => [e.id, e]));
		// Capture each deleted row's original index so Undo restores the
		// list exactly (mirrors instantDeleteTemplate's index capture).
		const originalIndexes = new Map(
			rows.map((e) => [e.id, templatesRef.current.indexOf(e)]),
		);
		try {
			const updated = templatesRef.current.filter(
				(e) => !selectedIds.has(e.id),
			);
			setTemplates(updated);
			// Keep the selection consistent — the deleted ids are gone.
			clearSelection();
			await saveTemplatesList(updated);
			showUndoableToast(
				t("templates.bulkDeleteToast", { count: String(rows.length) }),
				async () => {
					try {
						const latest = templatesRef.current.filter((e) => !byId.has(e.id));
						const restored = [...latest];
						for (const row of rows) {
							const insertAt = Math.min(
								originalIndexes.get(row.id) ?? restored.length,
								restored.length,
							);
							restored.splice(insertAt, 0, row);
						}
						await saveTemplatesList(restored);
						setTemplates(restored);
						toast.success(t("templates.restoredTemplate"));
					} catch {
						toast.error(t("templates.restoreFailed"));
					}
				},
				{ undoLabel: t("common.undo"), type: "warning", timeoutMs: 6000 },
			);
		} catch {
			// Restore the pre-delete list on failure (templatesRef still
			// holds the original list — saveTemplatesList threw first).
			setTemplates(templatesRef.current);
			showSnack(t("templates.deleteFailed"), "error");
		}
	}, [
		templatesRef,
		selectedIds,
		setTemplates,
		saveTemplatesList,
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
