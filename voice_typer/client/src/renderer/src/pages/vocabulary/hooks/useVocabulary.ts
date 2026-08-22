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
// - ``searchQuery`` / ``sortOrder`` state + ``filteredSorted`` memo
// (client-side search+sort — mirrors the History/Templates pattern)
//``instantDeleteEntry`` ( instant delete + 6-second
// Undo toast — see D2-FIX comment for the ref-based pattern)
//
// Extracted from the former monolithic ``pages/Vocabulary.tsx`` render
// function. The dialog + import/export state has been split into
// ``useVocabularyDialog`` and ``useVocabularyImportExport`` so each
// hook owns one concern.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { useFilterState } from "@/hooks/useFilterState";
import { showUndoableToast } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import type { VocabularyData, VocabularyEntry } from "@/types/ipc";
import { sortEntries, type VocabSortOrder } from "../lib/sort";
import {
	dedupeEntries,
	flattenEntries,
	rebuildData,
	type VocabRow,
	withEntryIds,
} from "../lib/transform";

/** Per-entry usage from the server's `get_correction_usage` snapshot. */
export interface EntryUsage {
	count: number;
	last_ts: number;
}

/**
 * Map key ``${category}::${original}`` → usage stats. Keyed by the
 * pair (not just the original) because the same wrong phrase can exist
 * in multiple categories.
 */
export type UsageByKey = Map<string, EntryUsage>;

export function usageKey(category: string, original: string): string {
	return `${category}::${original}`;
}

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
	/** Per-entry usage map ("used N×"), refreshed on load + after saves. */
	usageByKey: UsageByKey;
	// Search + filter + sort (client-side, applied via useMemo).
	searchQuery: string;
	setSearchQuery: (q: string) => void;
	sortOrder: VocabSortOrder;
	setSortOrder: (o: VocabSortOrder) => void;
	filteredSorted: VocabRow[];
}

export function useVocabulary({
	call,
	showSnack,
}: UseVocabularyArgs): UseVocabularyResult {
	const [entries, setEntries] = useState<VocabRow[]>([]);
	const [loading, setLoading] = useState(true);
	//fix #8: surface backend-load failures to the user
	// instead of silently masking them as "no entries exist".  Matches
	// the History/Templates retry pattern.
	const [loadError, setLoadError] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);
	// (XA-5-4): persist search + sort across page navigation via
	// sessionStorage so a user who switched tabs doesn't lose their
	// filter context when they come back. Wraps useSessionStorage
	// under the hood with a per-page namespaced key.
	const [searchQuery, setSearchQuery] = useFilterState<string>(
		"vocabulary",
		"searchQuery",
		"",
	);
	const [sortOrder, setSortOrder] = useFilterState<VocabSortOrder>(
		"vocabulary",
		"sortOrder",
		"newest",
	);

	// Per-correction usage snapshot (``get_correction_usage``) — powers
	// the per-row "Used N×" indicator. Fetched alongside the vocabulary
	// and re-fetched after every save (the server prunes usage records
	// for deleted corrections, so the map must track the live entries).
	const [usageByKey, setUsageByKey] = useState<UsageByKey>(new Map());

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

	// Ref mirror of `showSnack` so `loadVocabulary` keeps a STABLE
	// identity ([] deps). `showSnack` from useSnackbar is stable in
	// the real app, but tests mock it with a fresh function per render —
	// a dependency on it would re-create loadVocabulary every render and
	// re-trigger the mount-time load effect in an endless loop (spinner
	// forever). Same pattern as the page's categoryLabelsRef.
	const showSnackRef = useRef(showSnack);
	useEffect(() => {
		showSnackRef.current = showSnack;
	}, [showSnack]);

	// Ref mirror of `call` for the same reason as showSnack: some test
	// mocks (e.g. axe-core.test.tsx) return a FRESH `call` from
	// usePython on every render. Depending the mount-load effect on it
	// made loadVocabulary change identity per render → the effect
	// re-fired forever, re-fetching + re-rendering until the worker
	// OOM'd (FATAL ERROR: heap limit, killed the whole axe-core suite).
	const callRef = useRef(call);
	useEffect(() => {
		callRef.current = call;
	}, [call]);

	// Per-correction usage snapshot (``get_correction_usage``) — powers
	// the per-row "Used N×" indicator. Fetched alongside the vocabulary
	// and re-fetched after every save (the server prunes usage records
	// for deleted corrections, so the map must track the live entries).
	const loadUsage = useCallback(async () => {
		try {
			const snapshot = await callRef.current<{
				entries?: Record<
					string,
					Record<string, { count: number; last_ts: number }>
				>;
			}>("get_correction_usage");
			const map = new Map<string, EntryUsage>();
			for (const [cat, catEntries] of Object.entries(snapshot?.entries ?? {})) {
				for (const [original, usage] of Object.entries(catEntries ?? {})) {
					if (usage && typeof usage.count === "number" && usage.count > 0) {
						map.set(usageKey(cat, original), {
							count: usage.count,
							last_ts: usage.last_ts ?? 0,
						});
					}
				}
			}
			setUsageByKey(map);
		} catch (err) {
			// Usage is a progressive enhancement — a failure to load it
			// must not break the vocabulary list (entries still render
			// without the "used N×" line).
			console.error(
				"[renderer:useVocabulary] Failed to load correction usage:",
				err,
			);
			setUsageByKey(new Map());
		}
	}, []);

	const loadVocabulary = useCallback(async () => {
		setLoading(true);
		// Clear any prior load error before retrying so the EmptyState
		// swaps back to the spinner during the retry attempt (matches
		// the History/Templates retry pattern).
		setLoadError(null);
		try {
			const data = await callRef.current<VocabularyData>("get_vocabulary");
			const flat = flattenEntries(data ?? {});
			// Merge exact duplicates (same original+correction+category)
			// on load — the add dialog blocks new ones and import
			// de-dupes, but legacy files / hand-edited JSON can still
			// contain exact repeats. Keep the first occurrence and tell
			// the user; the next save persists the merged list.
			const { entries: unique, mergedCount } = dedupeEntries(flat);
			setEntries(withEntryIds(unique));
			if (mergedCount > 0) {
				showSnackRef.current(
					t("vocabulary.mergedDuplicates", {
						count: String(mergedCount),
					}),
					"info",
				);
			}
		} catch (err) {
			console.error("[renderer:useVocabulary] Failed to load vocabulary:", err);
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
	}, []);

	// Mount-only load: `loadVocabulary` / `loadUsage` have stable
	// identities (read `call` via a ref), so this effect runs exactly once.
	useEffect(() => {
		loadVocabulary();
		loadUsage();
	}, [loadVocabulary, loadUsage]);

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
				await callRef.current(
					"save_vocabulary",
					data as unknown as Record<string, unknown>,
				);
				// The save path prunes usage records for deleted corrections
				// — refresh the map so removed entries stop showing counts.
				await loadUsage();
			} catch (err) {
				console.error(
					"[renderer:useVocabulary] Failed to save vocabulary:",
					err,
				);
				throw err;
			} finally {
				setSaving(false);
			}
		},
		[loadUsage],
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
			// Capture the PRE-DELETE snapshot up front. This is the list
			// the failure path must restore — NOT `entriesRef.current`
			// at catch time: the ref-sync effect (declared near the
			// state) advances entriesRef to `updated` on the very next
			// render after setEntries(updated), so by the time a slow
			// or failed save settles, entriesRef no longer holds the
			// pre-delete list and restoring from it silently keeps the
			// entry deleted (a false-success state).
			const currentEntries = entriesRef.current;
			const originalIndex = currentEntries.indexOf(entry);
			const updated = currentEntries.filter((e) => e !== entry);
			try {
				// make the delete ACTUALLY instant.
				// Previously the entry stayed visible during the entire
				// persistVocabulary IPC round-trip (100-500ms+) because
				// setEntries(updated) ran AFTER the await. Felt sluggish
				// and could trigger duplicate-delete clicks. Now we update
				// the UI first, then persist; on failure we restore the
				// captured pre-delete snapshot.
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
				// Restore the pre-delete list on failure — from the
				// captured snapshot, not the (already-advanced) ref.
				setEntries(currentEntries);
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
		return sortEntries(bySearch, sortOrder);
	}, [entries, searchQuery, sortOrder]);

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
		usageByKey,
		searchQuery,
		setSearchQuery,
		sortOrder,
		setSortOrder,
		filteredSorted,
	};
}
