// Templates state + lifecycle hook.
//
// Owns:
//   - ``templates`` / ``loading`` / ``loadError`` React state
//   - ``templatesRef`` (ref mirror so delete-undo callbacks can read the
//latest list at undo time — see  / D2-FIX comments)
//   - ``loadRows`` (backend → React state, with localStorage migration)
//   - mount-time effect that calls ``loadRows`` once
//   - ``searchQuery`` / ``sortOrder`` state + ``filteredSortedTemplates``
//     memo (client-side search+sort — mirrors the History/Vocabulary pattern)
//``instantDeleteTemplate`` ( / R7-F10 instant delete +
//6-second Undo toast — see  comment for the ref-based pattern)
//
// Extracted from the former monolithic ``pages/Templates.tsx`` render
// function. The dialog + import/export state has been split into
// ``useTemplateDialog`` and ``useTemplateImportExport`` so each hook
// owns one concern.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { showUndoableToast } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";

import {
	loadTemplatesFromBackend,
	loadTemplatesFromLocalStorage,
	MIGRATION_FLAG_KEY,
	saveTemplates,
} from "../lib/storage";
import { rowsToTemplates, sortTemplateRows, toRows } from "../lib/transform";
import type { Template, TemplateRow, TemplateSortOrder } from "../lib/types";

type CallFn = <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>;

interface UseTemplatesArgs {
	call: CallFn;
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
	) => void;
}

interface UseTemplatesResult {
	templates: TemplateRow[];
	loading: boolean;
	loadError: string | null;
	templatesRef: React.RefObject<TemplateRow[]>;
	loadRows: () => Promise<void>;
	instantDeleteTemplate: (tmpl: TemplateRow) => Promise<void>;
	// Search + sort (client-side, applied via useMemo).
	searchQuery: string;
	setSearchQuery: (q: string) => void;
	sortOrder: TemplateSortOrder;
	setSortOrder: (o: TemplateSortOrder) => void;
	filteredSortedTemplates: TemplateRow[];
}

export function useTemplates({
	call,
	showSnack,
}: UseTemplatesArgs): UseTemplatesResult {
	const [templates, setTemplates] = useState<TemplateRow[]>([]);
	const [loading, setLoading] = useState(true);
	//surface backend-load failures (IPC error or malformed
	// payload) to the user instead of silently falling back to an
	// empty list. Distinguishes "no templates exist" (valid empty
	// array from backend) from "load failed" (backend unreachable or
	// returned garbage).
	const [loadError, setLoadError] = useState<string | null>(null);
	const [searchQuery, setSearchQuery] = useState("");
	const [sortOrder, setSortOrder] = useState<TemplateSortOrder>("newest");

	//ref mirror of `templates` (the React-state TemplateRow[])
	// so `saveTemplate` and the `instantDeleteTemplate` undo callback
	// can read the LATEST list at undo time (potentially seconds after
	// the delete, during which the user may have added/edited/deleted
	// OTHER templates).  Previously both call sites re-read from
	// `loadTemplatesFromLocalStorage()`, which:
	//   1. Could disagree with React state if a `saveTemplates()` call
	//      was still in flight (the old `saveTemplates` was fire-and-
	//      forget on the IPC leg).
	//   2. Used the `tmpl.index` captured at delete time against the
	//      fresh localStorage list — if other operations had shifted
	//      indices in the interim, the splice landed at the WRONG
	//      position (data loss / silent reordering).
	// The ref is kept in sync by the effect below; reads inside
	// callbacks always see the latest committed state.  Mirrors the
	// D2-FIX pattern from Vocabulary.tsx (entriesRef).
	const templatesRef = useRef<TemplateRow[]>(templates);
	useEffect(() => {
		templatesRef.current = templates;
	}, [templates]);

	//load from the Python backend (the new source of truth).
	// On first run after upgrade, if the backend has no templates but
	// localStorage does, push the localStorage data to the backend so the
	// user doesn't lose their pre-existing templates.
	//
	//distinguish "no templates exist" (valid empty array
	// from backend) from "load failed" (backend unreachable or
	// returned malformed data). If the backend IPC fails AND the
	// localStorage fallback is also empty, surface a load error so the
	// user can retry instead of being presented with the
	// "create your first template" empty state.
	const loadRows = useCallback(async () => {
		setLoading(true);
		// Clear any prior load error before retrying so the EmptyState
		// swaps back to the spinner during the retry attempt.
		setLoadError(null);
		try {
			let backendTemplates: Template[] = [];
			let backendFailed = false;
			try {
				backendTemplates = await loadTemplatesFromBackend(call);
			} catch (err) {
				// Backend not yet ready (e.g. Python still booting).  Fall
				// back to localStorage so the page is still usable; the next
				// save will resync the backend.
				console.warn(
					"get_templates IPC failed, falling back to localStorage",
					err,
				);
				backendFailed = true;
				backendTemplates = loadTemplatesFromLocalStorage();
			}

			// One-time migration: if backend is empty AND localStorage has
			// data AND we haven't migrated yet, push localStorage → backend.
			const migrated = localStorage.getItem(MIGRATION_FLAG_KEY) === "1";
			if (backendTemplates.length === 0 && !migrated && call) {
				const localItems = loadTemplatesFromLocalStorage();
				if (localItems.length > 0) {
					try {
						await call("save_templates", { templates: localItems });
						backendTemplates = localItems;
						console.warn(
							"[Templates] Migrated %d templates from localStorage to backend",
							localItems.length,
						);
					} catch (err) {
						console.error(
							"Failed to migrate localStorage templates to backend",
							err,
						);
					}
				}
				// Mark migration as complete regardless of whether there was
				// anything to migrate — we don't want to retry on every load.
				try {
					localStorage.setItem(MIGRATION_FLAG_KEY, "1");
				} catch (e) {
					// localStorage unavailable — non-fatal; we'll retry next session.
					console.warn("[useTemplates] migration flag setItem failed:", e);
				}
			}

			setTemplates(toRows(backendTemplates));
			//if the backend failed AND we couldn't recover
			// from localStorage (or migration), surface a load error
			// so the user knows to retry. Otherwise the empty list
			// would be indistinguishable from "no templates exist".
			//
			//previously this was a hardcoded English string.
			// Use the i18n key so the message localises with the UI
			// locale.
			if (backendFailed && backendTemplates.length === 0) {
				setLoadError(t("templates.loadFailedDescription"));
			}
		} catch (err) {
			console.error("Failed to load templates", err);
			setTemplates([]);
			//replace hardcoded English fallback with the
			// localised i18n key. If the caught error is a real
			// Error instance we still surface its .message (which
			// may come from the backend and carry useful detail);
			// otherwise we fall back to the localised description.
			setLoadError(
				err instanceof Error
					? err.message
					: t("templates.loadFailedDescription"),
			);
		} finally {
			setLoading(false);
		}
	}, [call]);

	useEffect(() => {
		loadRows();
	}, [loadRows]);

	//R7-F10: instant-delete path (no confirm dialog).
	// Triggered by the trash icon.  The legacy ConfirmDialog flow
	// was unreachable dead code and has been removed; all deletes
	// now go through this instant-delete + Undo toast path, which is
	// faster and recoverable (6-second undo window).
	//
	//the delete + undo now read from `templatesRef.current`
	// (the latest committed React state) instead of from
	// `loadTemplatesFromLocalStorage()`.  We capture `originalIndex`
	// BEFORE the delete (when templatesRef still holds the pre-delete
	// array).  At undo time we re-read `templatesRef.current` (which
	// may reflect add/edit/delete operations performed in the 6s
	// undo window), defensively filter out any item matching the
	// removed one (in case it was re-added in the interim), and
	// splice it back at the captured index CLAMPED to the current
	// length.  This guarantees exactly ONE copy is restored,
	// regardless of concurrent edits — mirroring Vocabulary.tsx's
	// D2-FIX pattern.  Previously the undo re-read from localStorage
	// (which could disagree with React state if a save was in
	// flight) and used the un-clamped `tmpl.index`, so concurrent
	// operations could shift indices and land the restore at the
	// wrong position (silent reordering / data loss).
	const instantDeleteTemplate = useCallback(
		async (tmpl: TemplateRow) => {
			// Capture the pre-delete rows BEFORE the optimistic
			// setTemplates call.  The ref is kept in sync by a
			// useEffect that runs AFTER render commits, so once we
			// call setTemplates(toRows(items)) the next render's
			// effect will overwrite templatesRef.current with the
			// post-delete list.  Holding the pre-delete rows in a
			// local lets the catch branch restore the exact
			// pre-delete state regardless of when the IPC rejects
			// (mirrors the useVocabulary D2-FIX pattern).
			const preDeleteRows = templatesRef.current;
			try {
				const items = rowsToTemplates(preDeleteRows);
				const originalIndex = tmpl.index;
				const removed = items.splice(tmpl.index, 1)[0];
				// Optimistic UI update: remove the row from React
				// state BEFORE awaiting the IPC save so the list
				// updates instantly.  Previously the UI stayed
				// stale for the entire 100-500ms saveTemplates
				// round-trip (plus another round-trip from
				// loadRows below), which felt sluggish and could
				// trigger duplicate-delete clicks.  On failure we
				// restore from `preDeleteRows` before showing the
				// error toast.
				setTemplates(toRows(items));
				//await the IPC save so loadRows()
				// below sees the post-delete state.
				await saveTemplates(items, call);
				if (removed) {
					showUndoableToast(
						t("templates.deletedTemplate", { name: tmpl.trigger }),
						async () => {
							try {
								// Re-read the LATEST list (may include
								// concurrent edits made between the delete
								// and the Undo click).
								const latest = rowsToTemplates(templatesRef.current);
								// Defensively filter out any item matching
								// the removed one (in case it was re-added
								// in the interim) so we don't end up with
								// a duplicate after the splice.
								const filtered = latest.filter(
									(existing) =>
										!(
											existing.trigger === removed.trigger &&
											existing.output === removed.output &&
											existing.match_mode === removed.match_mode
										),
								);
								// Clamp the captured index to the current
								// length so a shrunken list doesn't get
								// an out-of-bounds insert.
								const insertAt =
									originalIndex >= 0
										? Math.min(originalIndex, filtered.length)
										: filtered.length;
								filtered.splice(insertAt, 0, removed);
								await saveTemplates(filtered, call);
								loadRows();
							} catch (err) {
								console.error("Failed to restore template", err);
								showSnack(t("templates.saveFailed"), "error");
							}
						},
						{ undoLabel: t("common.undo"), type: "warning", timeoutMs: 6000 },
					);
				} else {
					showSnack(
						t("templates.deletedTemplate", { name: tmpl.trigger }),
						"warning",
					);
				}
				loadRows();
			} catch (err) {
				// Restore the pre-delete list so the UI matches the
				// actual persisted state (the save failed so the
				// backend still has the original list).
				setTemplates(preDeleteRows);
				console.error("Failed to delete template", err);
				showSnack(t("templates.deleteFailed"), "error");
			}
		},
		[call, loadRows, showSnack],
	);

	// ── Search + Sort (client-side) ─────────────────────────────────
	//
	// Applied via useMemo so the sort/filter only re-runs when the
	// underlying list, search query, or sort order changes — not on
	// every keystroke that re-renders the page.
	const filteredSortedTemplates = useMemo(() => {
		const q = searchQuery.trim().toLowerCase();
		const filtered = q
			? templates.filter(
					(r) =>
						r.trigger.toLowerCase().includes(q) ||
						r.expansion.toLowerCase().includes(q),
				)
			: templates;
		return sortTemplateRows(filtered, sortOrder);
	}, [templates, searchQuery, sortOrder]);

	return {
		templates,
		loading,
		loadError,
		templatesRef,
		loadRows,
		instantDeleteTemplate,
		searchQuery,
		setSearchQuery,
		sortOrder,
		setSortOrder,
		filteredSortedTemplates,
	};
}
