// History cache + IPC lifecycle hook.
//
// Owns:
// - ``records`` / ``stats`` / ``loading`` / ``loadingMore`` / ``hasMore``
//   / ``loadError`` React state
// - ``filterRef`` (ref mirror of the active ``query`` / ``favoritesOnly``
//   so the debounce + ``refreshFromEvent`` callbacks can read the latest
//   filter without re-creating the ``load`` callback identity on every
//   keystroke)
// - ``load`` (backend → React state; branches on filter to call
//   ``get_history`` / ``get_favorites`` / ``search_history``)
// - ``loadMore`` (paging — appends the next page to ``records``)
// - ``refreshFromEvent`` (re-fetches WITHOUT flipping ``loading`` so
//   background ``transcription_final`` / ``history_changed`` events
//   don't swap the spinner back in over the user's existing list)
// - ``setFilter`` (cheap ref-only update — the page calls this every
//   render to keep the hook's filter mirror in sync with the page's
//   ``searchQuery`` / ``favoritesOnly`` state)
//
// Cursor pagination: ``loadMore`` now passes
// ``before_timestamp`` + ``before_id`` (the ``(timestamp, id)`` of the
// last row currently in ``records``) so the backend can use keyset
// (cursor) pagination — O(log N) per page via ``idx_timestamp`` —
// instead of OFFSET (O(N) scan). The OFFSET path is kept as a fallback
// for the FIRST load (when ``records`` is empty and there's no last
// row to anchor a cursor on) and for any page where the last row is
// missing a ``timestamp`` or ``id`` field (defensive — older rows
// written before the ``id`` column existed can't be cursor-anchored).
//
// Pattern mirrors ``useVocabulary`` (sibling hook under
// ``pages/vocabulary/hooks/useVocabulary.ts``) — backend list → React
// state, error surfaced via ``loadError`` so the page can render a
// retry EmptyState instead of an ambiguous empty list.
//
// Extracted from the former monolithic ``pages/History.tsx`` render
// function as part of the spaghetti split. The export paging
// loop lives in ``useHistoryExport``; the client-side sort lives in
// ``historySort.ts``.

import { useCallback, useEffect, useRef, useState } from "react";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { useLatestRef } from "@/hooks/useLatestRef";
import { usePython } from "@/hooks/usePython";
import { peekIpcCache, writeIpcCache } from "@/lib/ipcCache";
import type { HistoryRecord, TodayStats } from "@/types/ipc";

import { deriveHistoryCursor, type HistoryCursor } from "../utils/cursor";

export type { HistoryCursor };

// Module-cache keys for the SWR seed (see lib/ipcCache.ts). Only the
// FIRST page + stats are cached — that's what a revisit renders
// instantly; `load` always revalidates fresh data over it.
const HISTORY_CACHE_KEY = "history.firstPage";
const HISTORY_STATS_CACHE_KEY = "history.todayStats";

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
	load: (query?: string, favoritesOnly?: boolean) => Promise<void>;
	loadMore: () => Promise<void>;
	refreshFromEvent: () => Promise<void>;
	setFilter: (query: string, favoritesOnly: boolean) => void;
}

export function useHistoryCache(): UseHistoryCacheReturn {
	// SWR seed: render the LAST visit's first page + stats immediately
	// (module cache survives page unmount) and skip the loading state —
	// the mount `load` below still revalidates in the background.
	const cachedRecords = peekIpcCache<HistoryRecord[]>(HISTORY_CACHE_KEY);
	const cachedStats = peekIpcCache<TodayStats>(HISTORY_STATS_CACHE_KEY);
	const [records, setRecords] = useState<HistoryRecord[]>(cachedRecords ?? []);
	const [stats, setStats] = useState<TodayStats>(
		cachedStats ?? {
			count: 0,
			chars: 0,
			word_count: 0,
			duration: 0,
		},
	);
	const [hasMore, setHasMore] = useState(false);
	const [loading, setLoading] = useState(cachedRecords === undefined);
	const [loadingMore, setLoadingMore] = useState(false);
	const [loadError, setLoadError] = useState<string | null>(null);

	const { agoLabel, markUpdated } = useLastUpdated();
	const { call } = usePython();

	// Ref mirrors of `call` / `markUpdated` so the load callbacks keep
	// STABLE identities (`[]`-ish deps). Both are useCallback-stable in
	// production, but test mocks return FRESH functions per render — an
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
	const offsetRef = useRef(0);

	// ref mirror of the current ``records`` array so ``loadMore``
	// can read the last row's ``(timestamp, id)`` for cursor pagination
	// WITHOUT adding ``records`` to the ``loadMore`` ``useCallback`` dep
	// array (which would re-create the callback identity on every record
	// change and cause the "Load More" button to re-render unnecessarily).
	// The assignment happens during render (before any effect/callback
	// fires) so the ref always reflects the latest committed ``records``.
	const recordsRef = useRef<HistoryRecord[]>([]);
	recordsRef.current = records;

	/**
	 * Derive cursor params from the last row of the current cache.
	 *
	 * Returns ``undefined`` when the cache is empty (first load) or when
	 * the last row is missing a ``timestamp`` / ``id`` field (defensive —
	 * older rows written before the ``id`` column existed can't be
	 * cursor-anchored, so the caller falls back to the OFFSET path).
	 */
	const deriveCursor = useCallback(deriveHistoryCursor, []);

	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract — .current must NOT become a dep
	const fetchPage = useCallback(
		async (
			query: string,
			favoritesOnly: boolean,
			limit: number,
			offset: number,
			cursor?: HistoryCursor,
		): Promise<HistoryRecord[]> => {
			// build the base payload with ``limit`` + ``offset``
			// (the OFFSET path — always present so the backend can fall
			// back to it when cursor params are absent or the cursor
			// anchor row has been deleted). When ``cursor`` is supplied
			// (i.e. ``loadMore`` paginating past the first page with a
			// valid last-row ``(timestamp, id)``), also pass
			// ``before_timestamp`` + ``before_id`` so the backend uses
			// keyset pagination — O(log N) via ``idx_timestamp`` instead
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
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract — .current must NOT become a dep
	const load = useCallback(
		async (query?: string, favoritesOnly?: boolean) => {
			// Resolve the effective filter (explicit args win; otherwise read
			// the ref so debounced / background callers see the latest).
			const q = query ?? filterRef.current.query;
			const fav = favoritesOnly ?? filterRef.current.favoritesOnly;
			filterRef.current = { query: q, favoritesOnly: fav };
			offsetRef.current = 0;

			setLoading(true);
			setLoadError(null);
			try {
				// First load — no cursor (OFFSET path). The backend returns
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
				// SWR write-through — the next visit to this page seeds
				// from this snapshot instead of showing a loading state.
				writeIpcCache(HISTORY_CACHE_KEY, firstPage);
				writeIpcCache(HISTORY_STATS_CACHE_KEY, nextStats);
				// ``hasMore`` is true when the backend returned a full page
				// (i.e. there MAY be more rows beyond this offset). The
				// backend's frame cap (200 rows max) means a full page is
				// also the cap, so we treat a full page as "ask again to
				// find out".
				setHasMore(safeRows.length >= HISTORY_PAGE_SIZE);
				offsetRef.current = safeRows.length;
				markUpdatedRef.current();
			} catch (err) {
				console.error("[renderer:History] load failed:", err);
				setRecords([]);
				setLoadError(err instanceof Error ? err.message : String(err));
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
		// the cache is empty (first load — shouldn't happen here since
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
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract — .current must NOT become a dep
	const refreshFromEvent = useCallback(async () => {
		const { query, favoritesOnly } = filterRef.current;

		try {
			// Refresh always re-fetches from the TOP (offset 0, no cursor)
			// — a background ``transcription_final`` event means a NEW row
			// was inserted at the head of the list, so we want the freshest
			// first page, not the next page after the old last row. The
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
			setRecords(safeRows.slice(0, HISTORY_MAX_ROWS));
			setStats(
				todayStats ?? {
					count: 0,
					chars: 0,
					word_count: 0,
					duration: 0,
				},
			);
			setHasMore(safeRows.length >= refreshLimit);
			offsetRef.current = safeRows.length;
			markUpdatedRef.current();
		} catch (err) {
			console.warn("[renderer:History] background refresh failed:", err);
		}
	}, [fetchPage]);

	// Cheap ref-only update — called on every page render to keep the
	// hook's filter mirror in sync with the page's state. Must NOT
	// trigger a re-render or fetch (the page decides when to fetch via
	// ``load()`` / ``handleSearch`` debounce).
	const setFilter = useCallback((query: string, favoritesOnly: boolean) => {
		filterRef.current = { query, favoritesOnly };
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
	};
}
