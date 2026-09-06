// History per-row record actions hook.
//
// Extracted from `pages/History.tsx` (page-root slimming): the three
// per-row event flows — delete-with-undo, favorite toggle, and the
// lazy full-text fetch for expandable rows — are cohesive IPC-backed
// event logic that belongs in a named, testable hook instead of the
// page root. The list rendering (ActivityList wiring) stays in the
// page; this hook owns the handlers it wires up.
//
// All state mutations go through the passed-in callbacks (`setRecords`
// / `load` from useHistoryCache), so the hook stays a pure handler
// factory — the page keeps owning the data.

import { useCallback } from "react";
import { toast } from "sonner";
import type { usePython } from "@/hooks/usePython";
import { showUndoableToast } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import type { HistoryRecord } from "@/types/ipc";

export interface UseHistoryRecordActionsOptions {
	/** The Python bridge call (from usePython). */
	call: ReturnType<typeof usePython>["call"];
	/** The currently loaded records (delete needs the pre-delete row). */
	records: HistoryRecord[];
	/** The cache hook's load (re-fetches after an undo restore). */
	load: (query?: string, favoritesOnly?: boolean) => Promise<void>;
	/** The cache hook's records-state setter. */
	setRecords: React.Dispatch<React.SetStateAction<HistoryRecord[]>>;
}

export interface UseHistoryRecordActionsReturn {
	/** Delete a row, offering an undo toast that restores it. */
	handleDelete: (id: number) => Promise<void>;
	/** Toggle a row's favorite flag in the backend + local cache. */
	handleToggleFavorite: (id: number) => Promise<void>;
	/** Lazy full-text fetch for an expandable row (null on failure). */
	handleFetchFullText: (id: number) => Promise<string | null>;
}

/**
 * Per-row record actions for the History page. See the file header for
 * the extraction rationale.
 */
export function useHistoryRecordActions({
	call,
	records,
	load,
	setRecords,
}: UseHistoryRecordActionsOptions): UseHistoryRecordActionsReturn {
	const handleDelete = useCallback(
		async (id: number) => {
			//capture the record before delete for Undo.
			const deleted = records.find((r) => r.id === id);
			try {
				await call("delete_history", { id });
				setRecords((prev) => prev.filter((r) => r.id !== id));
				if (deleted) {
					showUndoableToast(
						t("history.entryDeleted"),
						async () => {
							try {
								await call("restore_history", {
									record: deleted,
								});
								load();
								toast.success(t("history.entryRestored"));
							} catch {
								toast.error(t("history.restoreFailed"));
							}
						},
						{
							undoLabel: t("history.undo"),
							type: "warning",
							timeoutMs: 6000,
						},
					);
				}
			} catch {
				toast.error(t("history.deleteFailed"));
			}
		},
		[call, records, load, setRecords],
	);

	const handleToggleFavorite = useCallback(
		async (id: number) => {
			try {
				const res = await call<{ favorite: number }>("toggle_favorite", {
					id,
				});
				setRecords((prev) =>
					prev.map((r) => (r.id === id ? { ...r, favorite: res.favorite } : r)),
				);
			} catch {
				toast.error(t("history.favoriteFailed"));
			}
		},
		[call, setRecords],
	);

	// Full-text fetch for expandable rows. The list payload carries a
	// 500-char preview; the row calls this lazily on first expansion.
	// Resolves with the full text, or ``null`` when the row is gone /
	// the backend failed (the row surfaces a toast instead of expanding
	// to a clipped preview).
	const handleFetchFullText = useCallback(
		async (id: number): Promise<string | null> => {
			try {
				const data = await call<{ id: number; text: string }>(
					"get_transcription_text",
					{ id },
				);
				return data?.text ?? null;
			} catch {
				return null;
			}
		},
		[call],
	);

	return { handleDelete, handleToggleFavorite, handleFetchFullText };
}
