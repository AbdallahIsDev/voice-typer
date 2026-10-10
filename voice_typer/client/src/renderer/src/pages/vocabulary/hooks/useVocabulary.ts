// Vocabulary state + lifecycle hook.
// Owns:
// - ``entries`` / ``loading`` / ``loadError`` / ``saving`` React state
// - ``entriesRef`` (ref mirror so delete-undo callbacks can read the
// latest list at undo time, see D2-FIX comment for the bug history)
// - ``loadVocabulary`` (backend → React state)
// - ``persistVocabulary`` (strips client-side ``_id``, rebuilds the
// category-bucketed VocabularyData, calls ``save_vocabulary``)
// - mount-time effect that calls ``loadVocabulary`` once
// - ``searchQuery`` / ``sortOrder`` state + ``filteredSorted`` memo
// (client-side search+sort, mirrors the History/Templates pattern)
//``instantDeleteEntry`` ( instant delete + 6-second
// Undo toast, see D2-FIX comment for the ref-based pattern)
// function. The dialog + import/export state has been split into
// ``useVocabularyDialog`` and ``useVocabularyImportExport`` so each
// hook owns one concern.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { useFilterState } from "@/hooks/useFilterState";
import { useFuzzyFilter } from "@/hooks/useFuzzySearch";
import { useLatestRef } from "@/hooks/useLatestRef";
import type { PythonCall } from "@/hooks/usePython";
import { showUndoableToast } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { peekIpcCache, writeIpcCache } from "@/lib/ipcCache";
import {
	isSnapshotFresh,
	MOUNT_RERACE_FAST_WINDOW_MS,
	type PrefetchCall,
	peekPrefetchFlight,
	runPrefetchFlight,
	type TimestampedSnapshot,
} from "@/lib/snapshotCache";
import { useGlobalSearch } from "@/stores/useGlobalSearch";
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

export type UsageByKey = Map<string, EntryUsage>;

export function usageKey(category: string, original: string): string {
	return `${category}::${original}`;
}

// Module-cache key for the SWR seed (see lib/ipcCache.ts).
const VOCAB_CACHE_KEY = "vocabulary.entries";
// Timestamped snapshot (C-CACHE-1): entries + the raw usage response
// written atomically. Usage was previously refetched-but-never-seeded,
// so "Used N×" flashed empty on every revisit while entries showed.
const VOCAB_SNAPSHOT_KEY = "vocabulary.snapshot";
export const VOCAB_SNAPSHOT_TTL_MS = 30_000;

interface VocabSnapshot extends TimestampedSnapshot {
	entries: VocabRow[];
	usageRaw: unknown;
}

function readVocabSnapshot(): VocabSnapshot | null {
	const snap = peekIpcCache<VocabSnapshot>(VOCAB_SNAPSHOT_KEY);
	if (snap && Array.isArray(snap.entries)) return snap;
	const entries = peekIpcCache<VocabRow[]>(VOCAB_CACHE_KEY);
	if (entries) return { entries, usageRaw: null, fetchedAt: 0 };
	return null;
}

// Pure builder for the per-row usage map (shared by the load path,
// the seed, and hydration so all three derive identical maps).
function buildUsageMap(snapshot: {
	entries?: Record<string, Record<string, { count: number; last_ts: number }>>;
}): UsageByKey {
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
	return map;
}

/** True when a fresh snapshot makes a mount load redundant. */
export function isVocabCacheFresh(): boolean {
	return isSnapshotFresh(readVocabSnapshot(), VOCAB_SNAPSHOT_TTL_MS);
}

/** In-flight hover prefetch, if any (mount awaits it before deciding). */
export function peekVocabPrefetch(): Promise<void> | null {
	return peekPrefetchFlight(VOCAB_SNAPSHOT_KEY);
}

// Hover/focus data prefetch: warms entries + usage before navigation.
// Same four guards as the Analytics prefetch (C-CACHE-4).
export function prefetchVocabularyData(call: PrefetchCall): Promise<void> {
	if (isVocabCacheFresh()) return Promise.resolve();
	return runPrefetchFlight(VOCAB_SNAPSHOT_KEY, async () => {
		const [data, usageRaw] = await Promise.all([
			call("get_vocabulary"),
			call("get_correction_usage"),
		]);
		// Never cache error envelopes as an empty vocabulary (flattening
		// one yields [] and the fresh-cache fast path would serve it).
		if (!data || typeof data !== "object") return;
		const rec = data as Record<string, unknown>;
		if (rec.type === "error" || "_error" in rec) return;
		const { entries: unique } = dedupeEntries(
			flattenEntries(data as VocabularyData),
		);
		const withIds = withEntryIds(unique);
		const snap: VocabSnapshot = {
			entries: withIds,
			usageRaw: usageRaw ?? null,
			fetchedAt: Date.now(),
		};
		writeIpcCache(VOCAB_SNAPSHOT_KEY, snap);
		writeIpcCache(VOCAB_CACHE_KEY, withIds);
	});
}

interface UseVocabularyArgs {
	call: PythonCall;
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
	/** Backend reload. Resolves false when the fetch failed (loadError set). */
	loadVocabulary: () => Promise<boolean>;
	persistVocabulary: (updated: VocabRow[]) => Promise<void>;
	instantDeleteEntry: (entry: VocabRow) => Promise<void>;
	setEntries: (entries: VocabRow[]) => void;
	/** Per-entry usage map ("used N×"), refreshed on load + after saves. */
	usageByKey: UsageByKey;
	// Search + filter + sort (client-side, applied via useMemo).
	// The search query is READ from the shared global search store
	searchQuery: string;
	sortOrder: VocabSortOrder;
	setSortOrder: (o: VocabSortOrder) => void;
	filteredSorted: VocabRow[];
}

export function useVocabulary({
	call,
	showSnack,
}: UseVocabularyArgs): UseVocabularyResult {
	// SWR seed: revisit renders the last visit's list AND usage map
	// instantly from the module cache (survives page unmount). The
	// mount effect below revalidates only when the snapshot is stale.
	const [initialSnapshot] = useState<VocabSnapshot | null>(readVocabSnapshot);
	const [entries, setEntries] = useState<VocabRow[]>(
		() => initialSnapshot?.entries ?? [],
	);
	const [loading, setLoading] = useState(initialSnapshot === null);
	//fix #8: surface backend-load failures to the user
	// instead of silently masking them as "no entries exist".  Matches
	// the History/Templates retry pattern.
	const [loadError, setLoadError] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);
	// The search query comes from the GLOBAL title-bar search store —
	// there is no per-page search state anymore (the per-page
	// input in the app). Reading it here makes the filteredSorted memo
	// recompute whenever the title-bar query changes.
	const searchQuery = useGlobalSearch((s) => s.query);
	const [sortOrder, setSortOrder] = useFilterState<VocabSortOrder>(
		"vocabulary",
		"sortOrder",
		"newest",
	);

	// Per-correction usage snapshot (``get_correction_usage``), powers
	// the per-row "Used N×" indicator. Fetched alongside the vocabulary
	// and re-fetched after every save (the server prunes usage records
	// for deleted corrections, so the map must track the live entries).
	// Seeded from the snapshot: the map previously rebuilt from empty
	// on every revisit, flashing blank counts under cached entries.
	const [usageByKey, setUsageByKey] = useState<UsageByKey>(() =>
		buildUsageMap(
			(initialSnapshot?.usageRaw ?? {}) as {
				entries?: Record<
					string,
					Record<string, { count: number; last_ts: number }>
				>;
			},
		),
	);

	// `instantDeleteEntry` undo callback can read the LATEST list at
	// undo callback closed over `entries` from the render that created
	// `instantDeleteEntry`, that snapshot STILL INCLUDED the deleted
	// entry (because `instantDeleteEntry` reads `entries` to compute
	// `updated` via `.filter`, but never replaces `entries` in the
	// closure).  When the user clicked Undo, `restored = [...entries]`
	// contained `entry` at its original index, `restored.indexOf(entry)`
	// returned that index, and `restored.splice(idx, 0, entry)`
	// (deleteCount=0) INSERTED A SECOND COPY at that index, the entry
	// reappeared TWICE after Undo.  The closure was also stale with
	// respect to any other vocabulary edits made between the delete and
	// the Undo click, those edits were silently lost.
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
	const callRef = useLatestRef(call);

	// Per-correction usage snapshot (``get_correction_usage``), powers
	// the per-row "Used N×" indicator. Fetched alongside the vocabulary
	// and re-fetched after every save (the server prunes usage records
	// for deleted corrections, so the map must track the live entries).
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract, .current must NOT become a dep
	const loadUsage = useCallback(async (): Promise<unknown> => {
		try {
			const snapshot = await callRef.current<{
				entries?: Record<
					string,
					Record<string, { count: number; last_ts: number }>
				>;
			}>("get_correction_usage");
			setUsageByKey(buildUsageMap(snapshot ?? {}));
			// Merge the usage half into the snapshot (read-modify-write:
			// entries ride along untouched, see loadVocabulary below).
			const prev = readVocabSnapshot();
			writeIpcCache(VOCAB_SNAPSHOT_KEY, {
				entries: prev?.entries ?? [],
				usageRaw: snapshot ?? null,
				fetchedAt: Date.now(),
			} satisfies VocabSnapshot);
			return snapshot ?? null;
		} catch (err) {
			// Usage is a progressive enhancement, a failure to load it
			// must not break the vocabulary list (entries still render
			// without the "used N×" line).
			console.error(
				"[renderer:useVocabulary] Failed to load correction usage:",
				err,
			);
			setUsageByKey(new Map());
			return null;
		}
	}, []);

	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract, .current must NOT become a dep
	const loadVocabulary = useCallback(async (): Promise<boolean> => {
		setLoading(true);
		// Clear any prior load error before retrying so the EmptyState
		// swaps back to the spinner during the retry attempt (matches
		// the History/Templates retry pattern).
		setLoadError(null);
		try {
			const data = await callRef.current<VocabularyData>("get_vocabulary");
			const flat = flattenEntries(data ?? {});
			// Merge exact duplicates (same original+correction+category)
			// on load, the add dialog blocks new ones and import
			// de-dupes, but legacy files / hand-edited JSON can still
			// contain exact repeats. Keep the first occurrence and tell
			// the user; the next save persists the merged list.
			const { entries: unique, mergedCount } = dedupeEntries(flat);
			const withIds = withEntryIds(unique);
			setEntries(withIds);
			// SWR write-through, the next visit seeds from this snapshot.
			writeIpcCache(VOCAB_CACHE_KEY, withIds);
			// Merge the entries half into the snapshot (the usage half
			// rides along untouched, see loadUsage above).
			const prev = readVocabSnapshot();
			writeIpcCache(VOCAB_SNAPSHOT_KEY, {
				entries: withIds,
				usageRaw: prev?.usageRaw ?? null,
				fetchedAt: Date.now(),
			} satisfies VocabSnapshot);
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
			// SWR: keep the seeded/previous list on a FAILED revalidation —
			// stale content beats wiping the page. The page still renders
			// the retry EmptyState when there is genuinely nothing to show
			// (entries.length === 0).
			//fix #8: capture the error message so the render
			// path can show a retry EmptyState instead of an ambiguous
			// empty list.
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
			return false;
		} finally {
			setLoading(false);
		}
		return true;
	}, []);

	// Re-reads the snapshot cache into state (mount adopts a hover
	// prefetch that landed after first paint, C-CACHE-2). Idempotent.
	const hydrateFromCache = useCallback((): boolean => {
		const snap = readVocabSnapshot();
		if (!snap) return false;
		setEntries(snap.entries);
		setUsageByKey(
			buildUsageMap(
				(snap.usageRaw ?? {}) as {
					entries?: Record<
						string,
						Record<string, { count: number; last_ts: number }>
					>;
				},
			),
		);
		setLoading(false);
		setLoadError(null);
		return true;
	}, []);

	// Mount load with the TTL fast path (C-CACHE-2): a fresh snapshot
	// (recent visit or hover prefetch) renders from cache with no IPC.
	// A hover still in flight is awaited first and hydrated, so the
	// mount piggybacks it instead of duplicating it. Cold-start storm
	// (Dashboard/Models pattern): a failed mount load re-races once
	// after 8s so a boot-time failure heals without manual Retry; a
	// second failure keeps the error screen (bounded, unmount cancels).
	useEffect(() => {
		let cancelled = false;
		let timer: ReturnType<typeof setTimeout> | null = null;
		const startedAt = Date.now();
		void (peekVocabPrefetch() ?? Promise.resolve()).then(() => {
			if (cancelled) return;
			if (isVocabCacheFresh()) {
				hydrateFromCache();
				return;
			}
			void loadVocabulary().then((ok) => {
				// Re-race only fast failures (C-CACHE-10).
				if (
					!ok &&
					!cancelled &&
					Date.now() - startedAt < MOUNT_RERACE_FAST_WINDOW_MS
				) {
					timer = setTimeout(() => {
						if (!cancelled) void loadVocabulary();
					}, 8000);
				}
			});
			loadUsage();
		});
		return () => {
			cancelled = true;
			if (timer !== null) clearTimeout(timer);
		};
	}, [loadVocabulary, loadUsage, hydrateFromCache]);

	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract, .current must NOT become a dep
	const persistVocabulary = useCallback(
		async (updated: VocabRow[]) => {
			// Strip the client-side ``_id`` before sending to the backend
			// (the backend's save_vocabulary expects the raw
			// VocabularyEntry shape, extra fields would be ignored but
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
				//, refresh the map so removed entries stop showing counts.
				const usageRaw = await loadUsage();
				// Saves rewrite the snapshot with exactly what was saved:
				// the next revisit serves post-save state, never pre-save
				// (C-CACHE-5 invalidation-by-rewrite).
				writeIpcCache(VOCAB_SNAPSHOT_KEY, {
					entries: updated,
					usageRaw,
					fetchedAt: Date.now(),
				} satisfies VocabSnapshot);
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
	// `entries` via `entriesRef.current` (kept in sync by the effect
	// declared near the state) instead of closing over the render-time
	// `entries` snapshot.  This fixes two bugs:
	// contained the deleted entry, so `indexOf(entry)` returned the
	// original index and `splice(idx, 0, entry)` (deleteCount=0)
	// INSERTED a second copy at that index, the entry reappeared
	// TWICE after Undo.
	// 2. The lost-edits bug: any add/edit of OTHER entries between the
	// delete and the Undo click were silently reverted because the
	// restore replaced the current list with the stale pre-delete
	// snapshot.
	// We capture `originalIndex` BEFORE the delete (when entriesRef still
	// holds the pre-delete array).  At undo time we filter the latest
	// interim) and splice the entry back at the captured index, clamped
	// to the current length so a shrunken list doesn't get an out-of-
	// bounds insert.  The filter-then-splice combo guarantees exactly
	// ONE copy of the entry is restored, regardless of any concurrent
	// edits.
	// Deps no longer include `entries`, the callback reads from the ref,
	// so its identity is now stable across renders (it only changes when
	// `persistVocabulary` or `showSnack` change, which themselves only
	// change when `call` changes).  This matches the Templates.tsx
	// `instantDeleteTemplate` pattern (deps: [call, loadRows, showSnack]).
	const instantDeleteEntry = useCallback(
		async (entry: VocabRow) => {
			// Capture the PRE-DELETE snapshot up front. This is the list
			// the failure path must restore, NOT `entriesRef.current`
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
				// Restore the pre-delete list on failure, from the
				// captured snapshot, not the (already-advanced) ref.
				setEntries(currentEntries);
				showSnack(t("vocabulary.deleteFailed"), "error");
			}
		},
		[persistVocabulary, showSnack],
	);

	// ── Search + Filter + Sort (client-side) ──────────────────────────
	// Applied via useMemo so the filter/sort only re-runs when the
	// underlying list, search query, category filter, or sort order
	// changes, not on every keystroke that re-renders the page.

	const fuzzyEntries = useFuzzyFilter(entries, searchQuery, (e) => [
		e.original,
		e.correction,
	]);
	const filteredSorted = useMemo(() => {
		return sortEntries(fuzzyEntries, sortOrder);
	}, [fuzzyEntries, sortOrder]);

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
		sortOrder,
		setSortOrder,
		filteredSorted,
	};
}
