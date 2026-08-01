// Vocabulary state + lifecycle hook.
//
// Owns:
// - ``entries`` / ``loading`` / ``loadError`` / ``saving`` React state
// - ``entriesRef`` (ref mirror so delete-undo callbacks can read the
// latest list at undo time — see D2-FIX comment for the bug history)
// - ``loadVocabulary`` (backend → React state)
// - ``persistVocabulary`` (strips client-side ``_id``, rebuilds the
// category-bucketed VocabularyData, calls ``save_vocabulary``)
// - mount-time effect that calls ``loadVocabulary`` once
// - ``searchQuery`` / ``sortOrder`` / ``categoryFilter`` state +
// ``filteredSorted`` memo (client-side search+filter+sort — mirrors
// the History/Templates pattern)
//``instantDeleteEntry`` ( instant delete + 6-second
// Undo toast — see D2-FIX comment for the ref-based pattern)
//
// Extracted from the former monolithic ``pages/Vocabulary.tsx`` render
// function. The dialog + import/export state has been split into
// ``useVocabularyDialog`` and ``useVocabularyImportExport`` so each
// hook owns one concern.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { showUndoableToast } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import type { VocabularyData, VocabularyEntry } from "@/types/ipc";
import { sortEntries, type VocabSortOrder } from "../lib/sort";
import {
	flattenEntries,
	rebuildData,
	type VocabRow,
	withEntryIds,
} from "../lib/transform";

type CallFn = <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>;

interface UseVocabularyArgs {
	call: CallFn;
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
	) => void;
}

interface UseVocabularyResult {
	entries: VocabRow[];
	loading: boolean;
	loadError: string | null;
	saving: boolean;
	entriesRef: React.RefObject<VocabRow[]>;
	loadVocabulary: () => Promise<void>;
	persistVocabulary: (updated: VocabRow[]) => Promise<void>;
	instantDeleteEntry: (entry: VocabRow) => Promise<void>;
	setEntries: (entries: VocabRow[]) => void;
	// Search + filter + sort (client-side, applied via useMemo).
	searchQuery: string;
	setSearchQuery: (q: string) => void;
	sortOrder: VocabSortOrder;
	setSortOrder: (o: VocabSortOrder) => void;
	categoryFilter: string;
	setCategoryFilter: (c: string) => void;
	filteredSorted: VocabRow[];
}

export function useVocabulary({
	call,
	showSnack,
}: UseVocabularyArgs): UseVocabularyResult {
	const [entries, setEntries] = useState<VocabRow[]>([]);
	const [searchQuery, setSearchQuery] = useState("");
	const [loading, setLoading] = useState(true);
	//fix #8: surface backend-load failures to the user
	// instead of silently masking them as "no entries exist".  Matches
	// the History/Templates retry pattern.
	const [loadError, setLoadError] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);
	const [sortOrder, setSortOrder] = useState<VocabSortOrder>("newest");
	const [categoryFilter, setCategoryFilter] = useState<string>("all");

	// D2-FIX (b-review Finding 4): ref mirror of `entries` so the
	// `instantDeleteEntry` undo callback can read the LATEST list at
	// undo time (potentially seconds after the delete).  Previously the
	// undo callback closed over `entries` from the render that created
	// `instantDeleteEntry` — that snapshot STILL INCLUDED the deleted
	// entry (because `instantDeleteEntry` reads `entries` to compute
	// `updated` via `.filter`, but never replaces `entries` in the
	// closure).  When the user clicked Undo, `restored = [...entries]`
	// contained `entry` at its original index, `restored.indexOf(entry)`
	// returned that index, and `restored.splice(idx, 0, entry)`
	// (deleteCount=0) INSERTED A SECOND COPY at that index — the entry
	// reappeared TWICE after Undo.  The closure was also stale with
	// respect to any other vocabulary edits made between the delete and
	// the Undo click — those edits were silently lost.
	//
	// Mirrors the pattern in Templates.tsx:383, which re-reads via
	// `loadTemplatesFromLocalStorage()` inside the undo callback instead
	// of closing over a stale snapshot.  We use a ref instead of a
	// storage re-read because Vocabulary keeps its source of truth in
	// React state (not localStorage), so a ref is the equivalent.
	const entriesRef = useRef<VocabRow[]>(entries);
	useEffect(() => {
		entriesRef.current = entries;
	}, [entries]);

	const loadVocabulary = useCallback(async () => {
		setLoading(true);
		// Clear any prior load error before retrying so the EmptyState
		// swaps back to the spinner during the retry attempt (matches
		// the History/Templates retry pattern).
		setLoadError(null);
		try {
			const data = await call<VocabularyData>("get_vocabulary");
			setEntries(withEntryIds(flattenEntries(data ?? {})));
		} catch (err) {
			console.error("Failed to load vocabulary:", err);
			setEntries([]);
			//fix #8: capture the error message so the render
			// path can show a retry EmptyState instead of an ambiguous
			// empty list.
			//
			//previously this was a hardcoded English string.
			// Use the i18n key so the message localises with the UI
			// locale. If the caught error is a real Error instance we
			// still surface its .message (which may come from the
			// backend); otherwise we fall back to the localised
			// description.
			setLoadError(
				err instanceof Error
					? err.message
					: t("vocabulary.loadFailedDescription"),
			);
		} finally {
			setLoading(false);
		}
	}, [call]);

	useEffect(() => {
		loadVocabulary();
	}, [loadVocabulary]);

	const persistVocabulary = useCallback(
		async (updated: VocabRow[]) => {
			// Strip the client-side ``_id`` before sending to the backend
			// (the backend's save_vocabulary expects the raw
			// VocabularyEntry shape — extra fields would be ignored but
			// we keep the contract clean).
			const stripped: VocabularyEntry[] = updated.map(
				({ _id: _ignored, ...rest }) => {
					void _ignored;
					return rest;
				},
			);
			const data = rebuildData(stripped);
			setSaving(true);
			try {
				await call(
					"save_vocabulary",
					data as unknown as Record<string, unknown>,
				);
			} catch (err) {
				console.error("Failed to save vocabulary:", err);
				throw err;
			} finally {
				setSaving(false);
			}
		},
		[call],
	);

	//instant-delete + Undo toast.  Triggered by the trash
	// icon.  Removes the entry immediately and offers a 6-second Undo
	// window during which the user can restore it.
	//
	// D2-FIX (b-review Finding 4): the undo callback now reads the LATEST
	// `entries` via `entriesRef.current` (kept in sync by the effect
	// declared near the state) instead of closing over the render-time
	// `entries` snapshot.  This fixes two bugs:
	// 1. The stale-closure bug: `[...entries]` previously still
	// contained the deleted entry, so `indexOf(entry)` returned the
	// original index and `splice(idx, 0, entry)` (deleteCount=0)
	// INSERTED a second copy at that index — the entry reappeared
	// TWICE after Undo.
	// 2. The lost-edits bug: any add/edit of OTHER entries between the
	// delete and the Undo click were silently reverted because the
	// restore replaced the current list with the stale pre-delete
	// snapshot.
	//
	// We capture `originalIndex` BEFORE the delete (when entriesRef still
	// holds the pre-delete array).  At undo time we filter the latest
	// list defensively (in case the entry was somehow re-added in the
	// interim) and splice the entry back at the captured index, clamped
	// to the current length so a shrunken list doesn't get an out-of-
	// bounds insert.  The filter-then-splice combo guarantees exactly
	// ONE copy of the entry is restored, regardless of any concurrent
	// edits.
	//
	// Deps no longer include `entries` — the callback reads from the ref,
	// so its identity is now stable across renders (it only changes when
	// `persistVocabulary` or `showSnack` change, which themselves only
	// change when `call` changes).  This matches the Templates.tsx
	// `instantDeleteTemplate` pattern (deps: [call, loadRows, showSnack]).
	const instantDeleteEntry = useCallback(
		async (entry: VocabRow) => {
			try {
				const currentEntries = entriesRef.current;
				const originalIndex = currentEntries.indexOf(entry);
				const updated = currentEntries.filter((e) => e !== entry);
				// make the delete ACTUALLY instant.
				// Previously the entry stayed visible during the entire
				// persistVocabulary IPC round-trip (100-500ms+) because
				// setEntries(updated) ran AFTER the await. Felt sluggish
				// and could trigger duplicate-delete clicks. Now we update
				// the UI first, then persist; on failure we restore from
				// the ref (which still holds the pre-delete list).
				setEntries(updated);
				await persistVocabulary(updated);
				showUndoableToast(
					t("vocabulary.deletedEntry", { name: entry.original }),
					async () => {
						try {
							const latest = entriesRef.current.filter((e) => e !== entry);
							const restored = [...latest];
							const insertAt =
								originalIndex >= 0
									? Math.min(originalIndex, restored.length)
									: restored.length;
							restored.splice(insertAt, 0, entry);
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
				// Restore the pre-delete list on failure — entriesRef still
				// holds the original list because persistVocabulary threw
				// before any successful save.
				setEntries(entriesRef.current);
				showSnack(t("vocabulary.deleteFailed"), "error");
			}
		},
		[persistVocabulary, showSnack],
	);

	// ── Search + Filter + Sort (client-side) ──────────────────────────
	//
	// Applied via useMemo so the filter/sort only re-runs when the
	// underlying list, search query, category filter, or sort order
	// changes — not on every keystroke that re-renders the page.

	const filteredSorted = useMemo(() => {
		const q = searchQuery.trim().toLowerCase();
		const bySearch = q
			? entries.filter(
					(e) =>
						e.original.toLowerCase().includes(q) ||
						e.correction.toLowerCase().includes(q),
				)
			: entries;
		const byCategory =
			categoryFilter === "all"
				? bySearch
				: bySearch.filter((e) => e.category === categoryFilter);
		return sortEntries(byCategory, sortOrder);
	}, [entries, searchQuery, categoryFilter, sortOrder]);

	return {
		entries,
		loading,
		loadError,
		saving,
		entriesRef,
		loadVocabulary,
		persistVocabulary,
		instantDeleteEntry,
		setEntries,
		searchQuery,
		setSearchQuery,
		sortOrder,
		setSortOrder,
		categoryFilter,
		setCategoryFilter,
		filteredSorted,
	};
}
