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
// Pattern mirrors ``useVocabulary`` (sibling hook under
// ``pages/vocabulary/hooks/useVocabulary.ts``) — backend list → React
// state, error surfaced via ``loadError`` so the page can render a
//retry EmptyState instead of an ambiguous empty list ().
//
// Extracted from the former monolithic ``pages/History.tsx`` render
//function as part of the  spaghetti split. The export paging
// loop lives in ``useHistoryExport``; the client-side sort lives in
// ``historySort.ts``.

import { useCallback, useRef, useState } from "react";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { usePython } from "@/hooks/usePython";
import type { HistoryRecord, TodayStats } from "@/types/ipc";

// Page size used for both the initial load and ``loadMore`` paging.
// Mirrors the Python ``history_db.get_history`` default limit (50).
const HISTORY_PAGE_SIZE = 50;

// Safety cap on the number of rows the renderer will hold in memory
// at once. The backend enforces a frame cap of its own; this is a
// second line of defense against unbounded growth in the UI.
const HISTORY_MAX_ROWS = 5000;

type CallFn = <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>;

// Unused at runtime now that the hook calls ``usePython()`` directly, but
// kept as the documented contract for the underlying ``call`` shape (and
// referenced by ``useHistoryExport`` which still accepts a ``call`` arg).
export type { CallFn };

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
	const [records, setRecords] = useState<HistoryRecord[]>([]);
	const [stats, setStats] = useState<TodayStats>({
		count: 0,
		chars: 0,
		word_count: 0,
		duration: 0,
	});
	const [hasMore, setHasMore] = useState(false);
	const [loading, setLoading] = useState(true);
	const [loadingMore, setLoadingMore] = useState(false);
	const [loadError, setLoadError] = useState<string | null>(null);

	const { agoLabel, markUpdated } = useLastUpdated();
	const { call } = usePython();

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

	const fetchPage = useCallback(
		async (
			query: string,
			favoritesOnly: boolean,
			limit: number,
			offset: number,
		): Promise<HistoryRecord[]> => {
			//branch on the active filter so the displayed list (and
			// the export) reflects what the user is asking for, not always
			// the full history.
			if (favoritesOnly) {
				return call<HistoryRecord[]>("get_favorites", { limit, offset });
			}
			if (query.trim() !== "") {
				return call<HistoryRecord[]>("search_history", {
					query,
					limit,
					offset,
				});
			}
			return call<HistoryRecord[]>("get_history", { limit, offset });
		},
		[call],
	);

	// ``load`` is invoked from the page mount effect, the search debounce,
	// the favorites toggle, the retry button, and the manual refresh
	// button. When called with no args, falls back to the filter ref.
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
				const [rows, todayStats] = await Promise.all([
					fetchPage(q, fav, HISTORY_PAGE_SIZE, 0),
					call<TodayStats>("get_today_stats"),
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
				// ``hasMore`` is true when the backend returned a full page
				// (i.e. there MAY be more rows beyond this offset). The
				// backend's frame cap (200 rows max) means a full page is
				// also the cap, so we treat a full page as "ask again to
				// find out".
				setHasMore(safeRows.length >= HISTORY_PAGE_SIZE);
				offsetRef.current = safeRows.length;
				markUpdated();
			} catch (err) {
				console.error("[History] load failed:", err);
				setRecords([]);
				setLoadError(err instanceof Error ? err.message : String(err));
			} finally {
				setLoading(false);
			}
		},
		[fetchPage, call, markUpdated],
	);

	const loadMore = useCallback(async () => {
		const { query, favoritesOnly } = filterRef.current;
		const offset = offsetRef.current;

		setLoadingMore(true);
		try {
			const rows = await fetchPage(
				query,
				favoritesOnly,
				HISTORY_PAGE_SIZE,
				offset,
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
			console.error("[History] loadMore failed:", err);
		} finally {
			setLoadingMore(false);
		}
	}, [fetchPage]);

	// ``refreshFromEvent`` is invoked by the debounced
	// ``transcription_final`` / ``history_changed`` handlers. It re-runs
	// the load WITHOUT flipping ``loading`` so the spinner doesn't swap
	// back in over the user's existing list during a background refresh.
	const refreshFromEvent = useCallback(async () => {
		const { query, favoritesOnly } = filterRef.current;

		try {
			const [rows, todayStats] = await Promise.all([
				fetchPage(query, favoritesOnly, HISTORY_PAGE_SIZE, 0),
				call<TodayStats>("get_today_stats"),
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
			setHasMore(safeRows.length >= HISTORY_PAGE_SIZE);
			offsetRef.current = safeRows.length;
			markUpdated();
		} catch (err) {
			console.warn("[History] background refresh failed:", err);
		}
	}, [fetchPage, call, markUpdated]);

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
