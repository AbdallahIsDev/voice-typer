// Selection state + bulk operations for the Templates page.
//
// Thin feature wrapper over the shared :func:`useRowSelection` hook —
// supplies the Templates row shape (`id` id field), the persist
// callback, and the templates message keys. All selection/undo logic
// lives in the shared hook (formerly a 1:1 copy of this page's logic,
// which itself mirrored the Vocabulary page).

import { useCallback } from "react";
import { useRowSelection } from "@/hooks/useRowSelection";
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
	const getRowId = useCallback((row: TemplateRow) => row.id, []);
	const messages = {
		bulkDeleteToast: "templates.bulkDeleteToast",
		rowRestored: "templates.restoredTemplate",
		restoreFailed: "templates.restoreFailed",
		deleteFailed: "templates.deleteFailed",
	};

	return useRowSelection<TemplateRow>({
		rows: templates,
		setRows: setTemplates,
		rowsRef: templatesRef,
		persist: saveTemplatesList,
		showSnack,
		getRowId,
		messages,
	});
}
