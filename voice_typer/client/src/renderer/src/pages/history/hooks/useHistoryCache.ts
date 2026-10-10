// History cache + IPC lifecycle hook.
// Owns:
// - ``records`` / ``stats`` / ``loading`` / ``loadingMore`` / ``hasMore``
//   / ``loadError`` React state
// - ``filterRef`` (ref mirror of the active ``query`` / ``favoritesOnly``
//   so the debounce + ``refreshFromEvent`` callbacks can read the latest
//   filter without re-creating the ``load`` callback identity on every
//   keystroke)
// - ``load`` (backend → React state; branches on filter to call
//   ``get_history`` / ``get_favorites`` / ``search_history``)
// - ``loadMore`` (paging, appends the next page to ``records``)
// - ``refreshFromEvent`` (re-fetches WITHOUT flipping ``loading`` so
//   background ``transcription_final`` / ``history_changed`` events
//   don't swap the spinner back in over the user's existing list)
// - ``setFilter`` (cheap ref-only update, the page calls this every
//   render to keep the hook's filter mirror in sync with the page's
//   ``searchQuery`` / ``favoritesOnly`` state)
// Cursor pagination: ``loadMore`` now passes
// ``before_timestamp`` + ``before_id`` (the ``(timestamp, id)`` of the
// last row currently in ``records``) so the backend can use keyset
// (cursor) pagination, O(log N) per page via ``idx_timestamp`` —
// instead of OFFSET (O(N) scan). The OFFSET path is kept as a fallback
// for the FIRST load (when ``records`` is empty and there's no last
// row to anchor a cursor on) and for any page where the last row is
// missing a ``timestamp`` or ``id`` field (defensive, older rows
// written before the ``id`` column existed can't be cursor-anchored).
// Pattern mirrors ``useVocabulary`` (sibling hook under
// ``pages/vocabulary/hooks/useVocabulary.ts``), backend list → React
// state, error surfaced via ``loadError`` so the page can render a
// retry EmptyState instead of an ambiguous empty list.
// loop lives in ``useHistoryExport``; the client-side sort lives in
// ``historySort.ts``.

import { useCallback, useEffect, useRef, useState } from "react";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { useLatestRef } from "@/hooks/useLatestRef";
import { usePython } from "@/hooks/usePython";
import { peekIpcCache, writeIpcCache } from "@/lib/ipcCache";
import {
	isSnapshotFresh,
	type PrefetchCall,
	peekPrefetchFlight,
	runPrefetchFlight,
	type TimestampedSnapshot,
} from "@/lib/snapshotCache";
import type { HistoryRecord, TodayStats } from "@/types/ipc";

import { deriveHistoryCursor, type HistoryCursor } from "../utils/cursor";

export type { HistoryCursor };

// Module-cache keys for the SWR seed (see lib/ipcCache.ts). Only the
// FIRST page + stats are cached, that's what a revisit renders
// instantly; `load` always revalidates fresh data over it.
const HISTORY_CACHE_KEY = "history.firstPage";
const HISTORY_STATS_CACHE_KEY = "history.todayStats";
// Timestamped snapshot (C-CACHE-1): records + stats + hasMore written
// atomically, so a revisit restores the full list state (including
// Load-More availability) instead of a rows-only fragment. Legacy keys
// above stay as fallback readers/writers.
const HISTORY_SNAPSHOT_KEY = "history.snapshot";
export const HISTORY_SNAPSHOT_TTL_MS = 30_000;

const EMPTY_STATS: TodayStats = {
	count: 0,
	chars: 0,
	word_count: 0,
	duration: 0,
};

interface HistorySnapshot extends TimestampedSnapshot {
	records: HistoryRecord[];
	stats: TodayStats;
	hasMore: boolean;
}

function readHistorySnapshot(): HistorySnapshot | null {
	const snap = peekIpcCache<HistorySnapshot>(HISTORY_SNAPSHOT_KEY);
	if (snap && Array.isArray(snap.records) && snap.stats) return snap;
	const records = peekIpcCache<HistoryRecord[]>(HISTORY_CACHE_KEY);
	if (records)
		return {
			records,
			stats: peekIpcCache<TodayStats>(HISTORY_STATS_CACHE_KEY) ?? {
				...EMPTY_STATS,
			},
			hasMore: false,
			fetchedAt: 0,
		};
	return null;
}

function isDefaultFilter(query: string, favoritesOnly: boolean): boolean {
	return query.trim() === "" && !favoritesOnly;
}

/** True when a fresh default-view snapshot makes a mount load redundant. */
export function isHistoryCacheFresh(): boolean {
	return isSnapshotFresh(readHistorySnapshot(), HISTORY_SNAPSHOT_TTL_MS);
}

/** In-flight hover prefetch, if any (mount awaits it before deciding). */
export function peekHistoryPrefetch(): Promise<void> | null {
	return peekPrefetchFlight(HISTORY_SNAPSHOT_KEY);
}

// Hover/focus data prefetch: warms the default-view snapshot before
// navigation. Same four guards as the Analytics prefetch (C-CACHE-4).
export function prefetchHistoryData(call: PrefetchCall): Promise<void> {
	if (isHistoryCacheFresh()) return Promise.resolve();
	return runPrefetchFlight(HISTORY_SNAPSHOT_KEY, async () => {
		const [rows, todayStats] = await Promise.all([
			call("get_history", { limit: HISTORY_PAGE_SIZE, offset: 0 }),
			call("get_today_stats"),
		]);
		if (!Array.isArray(rows)) return;
		const firstPage = (rows as HistoryRecord[]).slice(0, HISTORY_MAX_ROWS);
		const stats =
			todayStats &&
			typeof todayStats === "object" &&
			typeof (todayStats as TodayStats).count === "number"
				? (todayStats as TodayStats)
				: { ...EMPTY_STATS };
		const snap: HistorySnapshot = {
			records: firstPage,
			stats,
			hasMore: rows.length >= HISTORY_PAGE_SIZE,
			fetchedAt: Date.now(),
		};
		writeIpcCache(HISTORY_SNAPSHOT_KEY, snap);
		writeIpcCache(HISTORY_CACHE_KEY, firstPage);
		writeIpcCache(HISTORY_STATS_CACHE_KEY, stats);
	});
}

// Page size used for both the initial load and ``loadMore`` paging.
// Mirrors the Python ``history_db.get_history`` default limit (50).
// Exported so the view layer (History.tsx) can size its visible-row
// window to exactly one fetched page without duplicating the literal
// (the two MUST stay in lockstep or "Load More" reveals nothing).
export const HISTORY_PAGE_SIZE = 50;

// Safety cap on the number of rows the renderer will hold in memory
// at once. The backend enforces a frame cap of its own; this is a
// second line of defense against unbounded growth in the UI.
const HISTORY_MAX_ROWS = 5000;

function isRowOlderThan(row: HistoryRecord, anchor: HistoryRecord): boolean {
	if (
		typeof row.timestamp === "string" &&
		typeof anchor.timestamp === "string" &&
		row.timestamp !== anchor.timestamp
	) {
		return row.timestamp < anchor.timestamp;
	}
	if (typeof row.id === "number" && typeof anchor.id === "number") {
		return row.id < anchor.id;
	}
	return true;
}

function mergeRefreshedRecords(
	fresh: HistoryRecord[],
	existing: HistoryRecord[],
): HistoryRecord[] {
	if (fresh.length === 0) return [];
	const freshIds = new Set<number>();
	// Content keys for legacy rows: rows written before the ``id``
	// column existed carry no numeric ``id``, so id-keyed dedup can
	// never match them. A legacy tail row whose ``(timestamp, text)``
	// equals a fresh row IS that row (now carrying its id), drop it
	// instead of rendering the entry twice.
	const freshContentKeys = new Set<string>();
	for (const r of fresh) {
		if (typeof r.id === "number") freshIds.add(r.id);
		freshContentKeys.add(`${r.timestamp}::${r.text}`);
	}
	// The keyset contract sorts the response newest-first, so the LAST
	// fresh row anchors the window's lower boundary.
	const anchor = fresh[fresh.length - 1];
	if (anchor === undefined) return fresh;
	const tail = existing.filter((r) => {
		if (typeof r.id === "number") {
			return !freshIds.has(r.id) && isRowOlderThan(r, anchor);
		}
		if (freshContentKeys.has(`${r.timestamp}::${r.text}`)) return false;
		return isRowOlderThan(r, anchor);
	});
	return [...fresh, ...tail];
}

export interface UseHistoryCacheReturn {
	records: HistoryRecord[];
	stats: TodayStats;
	loading: boolean;
	loadingMore: boolean;
	hasMore: boolean;
	loadError: string | null;
	agoLabel: string;
	setRecords: React.Dispatch<React.SetStateAction<HistoryRecord[]>>;
	setStats: React.Dispatch<React.SetStateAction<TodayStats>>;
	setHasMore: React.Dispatch<React.SetStateAction<boolean>>;
	/** Fresh load. Resolves false when the fetch failed (loadError set). */
	load: (query?: string, favoritesOnly?: boolean) => Promise<boolean>;
	loadMore: () => Promise<void>;
	refreshFromEvent: () => Promise<void>;
	setFilter: (query: string, favoritesOnly: boolean) => void;
	// Re-reads the snapshot cache into state (mount adopts a hover
	// prefetch that landed after first paint, C-CACHE-2). Idempotent,
	// returns false when there is nothing cached.
	hydrateFromCache: () => boolean;
}

export function useHistoryCache(): UseHistoryCacheReturn {
	// SWR seed: render the LAST visit's snapshot immediately (module
	// cache survives page unmount) and skip the loading state — the
	// mount effect in History.tsx revalidates only when the snapshot is
	// stale. Seeded as ONE snapshot so records + stats + hasMore stay
	// consistent (C-CACHE-1/3).
	const [initialSnapshot] = useState<HistorySnapshot | null>(
		readHistorySnapshot,
	);
	const [records, setRecords] = useState<HistoryRecord[]>(
		() => initialSnapshot?.records ?? [],
	);
	const [stats, setStats] = useState<TodayStats>(
		() => initialSnapshot?.stats ?? { ...EMPTY_STATS },
	);
	const [hasMore, setHasMore] = useState(
		() => initialSnapshot?.hasMore ?? false,
	);
	const [loading, setLoading] = useState(initialSnapshot === null);
	const [loadingMore, setLoadingMore] = useState(false);
	const [loadError, setLoadError] = useState<string | null>(null);

	const { agoLabel, markUpdated } = useLastUpdated();
	const { call } = usePython();

	// Ref mirrors of `call` / `markUpdated` so the load callbacks keep
	// STABLE identities (`[]`-ish deps). Both are useCallback-stable in
	// production, but test mocks return FRESH functions per render, an
	// identity churn would re-create `load` every render and re-fire the
	// page's mount-load effect (fetch → setRecords → re-render → new
	// call → loop → worker OOM). Same pattern as useVocabulary.ts.
	const callRef = useLatestRef(call);
	const markUpdatedRef = useRef(markUpdated);
	useEffect(() => {
		markUpdatedRef.current = markUpdated;
	}, [markUpdated]);

	// Ref mirror of the active filter so the page can call
	// ``setFilter(searchQuery, favoritesOnly)`` on every render (cheap —
	// ref-only update) and the debounced / background refresh callbacks
	// can read the LATEST filter without re-creating the ``load``
	// callback identity on every keystroke.
	const filterRef = useRef<{ query: string; favoritesOnly: boolean }>({
		query: "",
		favoritesOnly: false,
	});
	const offsetRef = useRef(initialSnapshot?.records.length ?? 0);

	// ref mirror of the current ``records`` array so ``loadMore``
	// can read the last row's ``(timestamp, id)`` for cursor pagination
	// WITHOUT adding ``records`` to the ``loadMore`` ``useCallback`` dep
	// array (which would re-create the callback identity on every record
	// change and cause the "Load More" button to re-render unnecessarily).
	// The assignment happens during render (before any effect/callback
	// fires) so the ref always reflects the latest committed ``records``.
	const recordsRef = useRef<HistoryRecord[]>([]);
	recordsRef.current = records;

	const deriveCursor = useCallback(deriveHistoryCursor, []);

	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract, .current must NOT become a dep
	const fetchPage = useCallback(
		async (
			query: string,
			favoritesOnly: boolean,
			limit: number,
			offset: number,
			cursor?: HistoryCursor,
		): Promise<HistoryRecord[]> => {
			// build the base payload with ``limit`` + ``offset``
			// (the OFFSET path, always present so the backend can fall
			// back to it when cursor params are absent or the cursor
			// anchor row has been deleted). When ``cursor`` is supplied
			// (i.e. ``loadMore`` paginating past the first page with a
			// valid last-row ``(timestamp, id)``), also pass
			// ``before_timestamp`` + ``before_id`` so the backend uses
			// keyset pagination, O(log N) via ``idx_timestamp`` instead
			// of the O(N) OFFSET scan. The server side (
			// ``server/service/history.py``) accepts both shapes and
			// prefers the cursor when both fields are non-null.
			const payload: Record<string, unknown> = { limit, offset };
			if (
				cursor?.before_timestamp !== undefined &&
				cursor?.before_id !== undefined
			) {
				payload.before_timestamp = cursor.before_timestamp;
				payload.before_id = cursor.before_id;
			}
			// branch on the active filter so the displayed list (and
			// the export) reflects what the user is asking for, not always
			// the full history.
			if (favoritesOnly) {
				return callRef.current<HistoryRecord[]>("get_favorites", payload);
			}
			if (query.trim() !== "") {
				return callRef.current<HistoryRecord[]>("search_history", {
					query,
					...payload,
				});
			}
			return callRef.current<HistoryRecord[]>("get_history", payload);
		},
		[],
	);

	// ``load`` is invoked from the page mount effect, the search debounce,
	// the favorites toggle, the retry button, and the manual refresh
	// button. When called with no args, falls back to the filter ref.
	// Resolves false on failure so the mount effect can schedule its
	// cold-start re-race (mirrors the Dashboard/Models pattern).
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract, .current must NOT become a dep
	const load = useCallback(
		async (query?: string, favoritesOnly?: boolean): Promise<boolean> => {
			// Resolve the effective filter (explicit args win; otherwise read
			// the ref so debounced / background callers see the latest).
			const q = query ?? filterRef.current.query;
			const fav = favoritesOnly ?? filterRef.current.favoritesOnly;
			filterRef.current = { query: q, favoritesOnly: fav };
			offsetRef.current = 0;

			setLoading(true);
			setLoadError(null);
			try {
				// First load, no cursor (OFFSET path). The backend returns
				// the first ``HISTORY_PAGE_SIZE`` rows in ``(timestamp DESC,
				// id DESC)`` order; ``loadMore`` will cursor-anchor on the
				// last row of this page for subsequent fetches.
				const [rows, todayStats] = await Promise.all([
					fetchPage(q, fav, HISTORY_PAGE_SIZE, 0),
					callRef.current<TodayStats>("get_today_stats"),
				]);
				const safeRows = Array.isArray(rows) ? rows : [];
				const firstPage = safeRows.slice(0, HISTORY_MAX_ROWS);
				const nextStats = todayStats ?? {
					count: 0,
					chars: 0,
					word_count: 0,
					duration: 0,
				};
				setRecords(firstPage);
				setStats(nextStats);
				// SWR write-through, the next visit to this page seeds
				// from this snapshot instead of showing a loading state.
				writeIpcCache(HISTORY_CACHE_KEY, firstPage);
				writeIpcCache(HISTORY_STATS_CACHE_KEY, nextStats);
				// Timestamped snapshot for the TTL fast path — default
				// view only. A filtered load must never seed the revisit
				// cache, or a revisit would first-paint another filter's
				// rows (C-CACHE-1).
				if (isDefaultFilter(q, fav)) {
					writeIpcCache(HISTORY_SNAPSHOT_KEY, {
						records: firstPage,
						stats: nextStats,
						hasMore: safeRows.length >= HISTORY_PAGE_SIZE,
						fetchedAt: Date.now(),
					} satisfies HistorySnapshot);
				}
				// ``hasMore`` is true when the backend returned a full page
				// (i.e. there MAY be more rows beyond this offset). The
				// backend's frame cap (200 rows max) means a full page is
				// also the cap, so we treat a full page as "ask again to
				// find out".
				setHasMore(safeRows.length >= HISTORY_PAGE_SIZE);
				offsetRef.current = safeRows.length;
				markUpdatedRef.current();
				return true;
			} catch (err) {
				console.error("[renderer:History] load failed:", err);
				setRecords([]);
				setLoadError(err instanceof Error ? err.message : String(err));
				return false;
			} finally {
				setLoading(false);
			}
		},
		[fetchPage],
	);

	const loadMore = useCallback(async () => {
		const { query, favoritesOnly } = filterRef.current;
		const offset = offsetRef.current;
		// derive cursor params from the last row of the current
		// cache so the backend can use keyset (cursor) pagination. When
		// the cache is empty (first load, shouldn't happen here since
		// ``loadMore`` is only called after ``load``) or the last row is
		// missing ``timestamp`` / ``id``, ``deriveCursor`` returns
		// ``undefined`` and ``fetchPage`` falls back to the OFFSET path.
		const cursor = deriveCursor(recordsRef.current);

		setLoadingMore(true);
		try {
			const rows = await fetchPage(
				query,
				favoritesOnly,
				HISTORY_PAGE_SIZE,
				offset,
				cursor,
			);
			const safeRows = Array.isArray(rows) ? rows : [];
			if (safeRows.length === 0) {
				setHasMore(false);
				return;
			}
			setRecords((prev) => {
				const merged = [...prev, ...safeRows];
				return merged.slice(0, HISTORY_MAX_ROWS);
			});
			offsetRef.current = offset + safeRows.length;
			setHasMore(safeRows.length >= HISTORY_PAGE_SIZE);
		} catch (err) {
			console.error("[renderer:History] loadMore failed:", err);
		} finally {
			setLoadingMore(false);
		}
	}, [fetchPage, deriveCursor]);

	// ``refreshFromEvent`` is invoked by the debounced
	// ``transcription_final`` / ``history_changed`` handlers. It re-runs
	// the load WITHOUT flipping ``loading`` so the spinner doesn't swap
	// back in over the user's existing list during a background refresh.
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract, .current must NOT become a dep
	const refreshFromEvent = useCallback(async () => {
		const { query, favoritesOnly } = filterRef.current;

		try {
			// Refresh always re-fetches from the TOP (offset 0, no cursor)
			//, a background ``transcription_final`` event means a NEW row
			// was inserted at the head of the list, so we want the freshest
			// OFFSET path (no cursor) is correct here. Preserve the current
			// paged-in depth: re-fetch at least as many rows as the user has
			// already loaded via "Load More". Without this, a background
			// refresh would shrink the list back to HISTORY_PAGE_SIZE rows,
			// losing the user's scroll position and loaded entries.
			const refreshLimit = Math.max(HISTORY_PAGE_SIZE, offsetRef.current);
			const [rows, todayStats] = await Promise.all([
				fetchPage(query, favoritesOnly, refreshLimit, 0),
				callRef.current<TodayStats>("get_today_stats"),
			]);
			const safeRows = Array.isArray(rows) ? rows : [];
			// The requested ``refreshLimit`` is only a REQUEST: the
			// backend clamps every single history fetch to its IPC row
			// cap, so a deep-browsed list (offset beyond the cap) gets
			// back only the newest CAPPED window. Replacing ``records``
			// with that window would truncate the list AND kill
			// Load-More (``capped < refreshLimit`` → ``hasMore``
			// false). MERGE the fresh head with the existing tail
			// instead, keyed by ``id``: the keyset order
			// (``timestamp DESC, id DESC``) guarantees the retained
			// tail rows are strictly older than the fresh head, so
			// ``[fresh head, ...tail]`` is still in list order and
			// the id-keyed dedup collapses any overlap. A row that was
			// deleted inside the fresh window is dropped (it is not
			// older than the head's oldest row); one deleted beyond
			// the window lingers as stale until the next full
			// ``load``, an accepted, self-healing trade (detecting
			// it would require re-fetching the full depth, which the
			// server cap exists to prevent). An EMPTY fresh response
			// means the filtered result set is gone entirely —
			// replace, don't retain stale rows.
			// Functional updater: a concurrent Load-More may commit its
			// appended page between this refresh's IPC completion and the
			// state commit, merging against the UP-TO-DATE state (not the
			// snapshot read before the await) preserves that page instead
			// of overwriting it (lost-update hardening). ``hasMore`` and
			// the offset derive from the COMMITTED merge for the same
			// reason: deriving them from the pre-await ``recordsRef``
			// snapshot would drop a concurrent page from ``nextLength``
			// (killing Load-More for one cycle). The ref assignment and
			// ``setHasMore`` are idempotent (same value on repeat), so
			// StrictMode's double-invoked updater is safe.
			setRecords((prevRecords) => {
				const merged =
					safeRows.length === 0
						? []
						: mergeRefreshedRecords(safeRows, prevRecords);
				const capped = merged.slice(0, HISTORY_MAX_ROWS);
				// ``hasMore`` from the MERGED length: with no retained
				// tail the merged length equals the response length, so
				// shallow refreshes keep the exact pre-merge semantics
				// (``safeRows.length >= refreshLimit``); with a retained
				// tail the merged length covers the paged-in depth, so
				// Load-More stays alive exactly when the list still
				// holds ``refreshLimit`` rows (a false positive costs one
				// Load-More click that returns 0 rows and self-corrects).
				// An EMPTY fresh response means the filtered result set
				// is gone entirely, ``capped`` is empty, ``hasMore``
				// goes false, stale rows are not retained.
				offsetRef.current = capped.length;
				setHasMore(capped.length >= refreshLimit);
				return capped;
			});
			setStats(
				todayStats ?? {
					count: 0,
					chars: 0,
					word_count: 0,
					duration: 0,
				},
			);
			// Keep the revisit snapshot moving on background refreshes
			// (default view only, same filter guard as `load`): an event
			// refresh IS a revalidation, so it renews the TTL.
			if (
				isDefaultFilter(
					filterRef.current.query,
					filterRef.current.favoritesOnly,
				)
			) {
				const merged =
					safeRows.length === 0
						? []
						: mergeRefreshedRecords(safeRows, recordsRef.current);
				const capped = merged.slice(0, HISTORY_MAX_ROWS);
				writeIpcCache(HISTORY_SNAPSHOT_KEY, {
					records: capped,
					stats: todayStats ?? { ...EMPTY_STATS },
					hasMore: capped.length >= refreshLimit,
					fetchedAt: Date.now(),
				} satisfies HistorySnapshot);
			}
			markUpdatedRef.current();
		} catch (err) {
			console.warn("[renderer:History] background refresh failed:", err);
		}
	}, [fetchPage]);

	// Cheap ref-only update, called on every page render to keep the
	// hook's filter mirror in sync with the page state. Must NOT
	// trigger a re-render or fetch (the page decides when to fetch via
	// ``load()`` / ``handleSearch`` debounce).
	const setFilter = useCallback((query: string, favoritesOnly: boolean) => {
		filterRef.current = { query, favoritesOnly };
	}, []);

	const hydrateFromCache = useCallback((): boolean => {
		const snap = readHistorySnapshot();
		if (!snap) return false;
		setRecords(snap.records);
		setStats(snap.stats);
		setHasMore(snap.hasMore);
		offsetRef.current = snap.records.length;
		setLoading(false);
		setLoadError(null);
		return true;
	}, []);

	return {
		records,
		stats,
		loading,
		loadingMore,
		hasMore,
		loadError,
		agoLabel,
		setRecords,
		setStats,
		setHasMore,
		load,
		loadMore,
		refreshFromEvent,
		setFilter,
		hydrateFromCache,
	};
}
