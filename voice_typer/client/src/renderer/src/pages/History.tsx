import {
	AlertCircleIcon,
	ArrowDown01Icon,
	Delete01Icon,
	HistoryIcon,
	Mic02Icon,
	StarIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import ExportFormatMenu from "@/components/common/ExportFormatMenu";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import PageHeading from "@/components/common/PageHeading";
import { SortSelect } from "@/components/common/SortSelect";
import ActivityList from "@/components/dashboard/ActivityList";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Spinner } from "@/components/feedback/Spinner";
import { Button } from "@/components/ui/button";
import { useGlobalSearch } from "@/hooks/useGlobalSearch";
import { useNavigation } from "@/hooks/useNavigation";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { showUndoableToast } from "@/hooks/useSnackbar";
import { getLocale, t } from "@/i18n/i18n";
import {
	HISTORY_PAGE_SIZE,
	useHistoryCache,
} from "./history/hooks/useHistoryCache";
import { useHistoryExport } from "./history/hooks/useHistoryExport";
import {
	type HistorySortOrder,
	sortRecords,
} from "./history/utils/historySort";

//NOTE: App.tsx prop passing will be removed by
//(BACKLOG-004): HistoryPage now obtains `navigate` via the
// useNavigation hook directly, eliminating the `onNavigate` prop drill.
//
//(spaghetti split): the cache + IPC lifecycle (load / loadMore /
// event refresh) lives in `useHistoryCache`, the export paging loop
// lives in `useHistoryExport`, and the client-side sort lives in
// `historySort.ts`. This file is the thin view component.

// Soft display cap — the flat list renders at most this many rows so a
// very long history can't mount thousands of DOM rows at once. Once the
// user has revealed this many rows AND the backend still reports more,
// the "Load More" button is replaced by the cap notice pointing at
// search (further fetches would append invisible rows past the cap).
const HISTORY_DISPLAY_CAP = 200;

export default function HistoryPage() {
	const { navigate } = useNavigation();
	const { call } = usePython();
	const {
		records,
		stats,
		loading,
		loadError,
		loadingMore,
		hasMore,
		agoLabel,
		setRecords,
		setStats,
		setHasMore,
		load,
		loadMore,
		refreshFromEvent,
		setFilter,
	} = useHistoryCache();
	const [favoritesOnly, setFavoritesOnly] = useState(false);
	const searchQuery = useGlobalSearch((s) => s.query);
	const [sortOrder, setSortOrder] = useState<HistorySortOrder>("newest");
	// Explicit visible-row window. The cache hook pages 50 records per
	// fetch into `records`; this state controls how many of them the
	// list actually renders. It starts at one page and every "Load More"
	// click BOTH fetches the next page (loadMore) and widens the window
	// by one page — without the widening, appended rows would be sliced
	// off by the render cap below and the click would look like a
	// dead-zone no-op. Reset to one page whenever a fresh load runs.
	const [visibleCount, setVisibleCount] = useState(HISTORY_PAGE_SIZE);
	const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	// Guards the debounced-load effect so the initial mount load
	// (handled by the separate mount effect) is not re-fired when the
	// global query starts at "" — only query CHANGES trigger a reload.
	const isFirstRenderRef = useRef(true);
	const [showClearConfirm, setShowClearConfirm] = useState(false);
	const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const [refreshing, setRefreshing] = useState(false);

	// stale-data flag. Set to `true` when a `transcription_final`
	// or `history_changed` event arrives while the window is hidden
	// (document.visibilityState !== "visible"). The visibilitychange
	// listener below checks this flag on focus and triggers a single
	// debounced refresh — so background events don't fire IPC calls
	// while the user isn't looking at the page. The next focus
	// collapses the backlog into ONE fetch (per-page; only the visible
	// page's listener actually runs because only the mounted page
	// subscribes).
	const staleRef = useRef(false);

	// Keep the cache hook's filter refs in sync with the page state.
	setFilter(searchQuery, favoritesOnly);

	// Fresh-load wrapper: every NEW fetch (mount, search, favorites
	// toggle, manual refresh, error retry) restarts the visible window
	// at one page so the list never shows a stale widened window over
	// freshly-fetched rows. Background event refreshes bypass this —
	// they must preserve the user's loaded depth.
	const runLoad = useCallback(
		(query?: string, favoritesOnly?: boolean) => {
			setVisibleCount(HISTORY_PAGE_SIZE);
			return load(query, favoritesOnly);
		},
		[load],
	);

	const handleManualRefresh = useCallback(async () => {
		setRefreshing(true);
		try {
			await runLoad();
		} finally {
			setRefreshing(false);
		}
	}, [runLoad]);

	// R7-F13: extracted `debouncedRefreshFromEvent` via useCallback.
	// Wraps `refreshFromEvent` (owned by useHistoryCache) in a 500ms
	// debounce so rapid transcription_final / history_changed events
	// coalesce into a single backend fetch.
	const debouncedRefreshFromEvent = useCallback(():
		| (() => void)
		| undefined => {
		// skip the IPC round-trips when the window is hidden.
		// The visibilitychange listener below will trigger a single
		// refresh when the user returns to the page.
		if (
			typeof document !== "undefined" &&
			document.visibilityState !== "visible"
		) {
			staleRef.current = true;
			return undefined;
		}
		if (refreshTimer.current) clearTimeout(refreshTimer.current);
		refreshTimer.current = setTimeout(async () => {
			try {
				await refreshFromEvent();
			} catch (e) {
				console.warn("[renderer:History] background refresh failed:", e);
			}
		}, 500);
		return undefined;
	}, [refreshFromEvent]);

	// refresh on focus when stale. When the window regains
	// visibility AND a stale flag was set by a background event, fire
	// a single debounced refresh.
	useEffect(() => {
		const onVisibility = () => {
			if (document.visibilityState === "visible" && staleRef.current) {
				staleRef.current = false;
				debouncedRefreshFromEvent();
			}
		};
		document.addEventListener("visibilitychange", onVisibility);
		return () => {
			document.removeEventListener("visibilitychange", onVisibility);
		};
	}, [debouncedRefreshFromEvent]);

	usePythonEvent("transcription_final", debouncedRefreshFromEvent);
	// F11-FIX: invalidate cache on external history_changed events.
	usePythonEvent("history_changed", debouncedRefreshFromEvent);

	// Clean up pending refresh + search timers on unmount.
	useEffect(() => {
		return () => {
			if (refreshTimer.current) clearTimeout(refreshTimer.current);
			// clear searchTimer to prevent load()
			// firing on an unmounted component.
			if (searchTimer.current) {
				clearTimeout(searchTimer.current);
				searchTimer.current = null;
			}
		};
	}, []);

	useEffect(() => {
		runLoad();
	}, [runLoad]);

	// Debounced reload driven by the GLOBAL search store. The query now
	// lives in the title bar's global search bar; when it changes this
	// effect schedules a 200ms-delayed runLoad with the new query. The
	// first-render guard keeps the mount load from double-firing.
	useEffect(() => {
		if (isFirstRenderRef.current) {
			isFirstRenderRef.current = false;
			return;
		}
		if (searchTimer.current) clearTimeout(searchTimer.current);
		searchTimer.current = setTimeout(() => {
			runLoad(searchQuery, favoritesOnly);
		}, 200);
		return () => {
			if (searchTimer.current) {
				clearTimeout(searchTimer.current);
				searchTimer.current = null;
			}
		};
	}, [searchQuery, runLoad, favoritesOnly]);

	const toggleFavorites = useCallback(() => {
		const next = !favoritesOnly;
		setFavoritesOnly(next);
		runLoad(searchQuery, next);
	}, [favoritesOnly, runLoad, searchQuery]);

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
								await call("restore_history", { record: deleted });
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
				const res = await call<{ favorite: number }>("toggle_favorite", { id });
				setRecords((prev) =>
					prev.map((r) => (r.id === id ? { ...r, favorite: res.favorite } : r)),
				);
			} catch {
				toast.error(t("history.favoriteFailed"));
			}
		},
		[call, setRecords],
	);

	//Clear All is ambiguous under an active filter (the visible
	// list is a subset of ALL history).  When a filter is active:
	// - Skip the `records.length === 0` short-circuit using the cached
	// stats count instead (visible list may be empty while total is not).
	// - Show a different confirmation message that makes it clear ALL
	// history (including hidden entries) will be deleted.
	const filterActive = searchQuery.trim() !== "" || favoritesOnly;

	const handleClearAll = useCallback(() => {
		const totalCount = stats?.count ?? records.length;
		if (filterActive) {
			if (totalCount === 0) return;
		} else {
			if (records.length === 0) return;
		}
		setShowClearConfirm(true);
	}, [records.length, stats, filterActive]);

	const confirmClearAll = useCallback(async () => {
		try {
			await call("clear_history");
			const emptyStats = { count: 0, chars: 0, word_count: 0, duration: 0 };
			setRecords([]);
			setStats(emptyStats);
			setHasMore(false);
			toast.success(t("history.historyCleared"));
		} catch {
			toast.error(t("history.clearFailed"));
		} finally {
			setShowClearConfirm(false);
		}
	}, [call, setRecords, setStats, setHasMore]);

	//doExport (filter-aware paging loop) extracted to
	//useHistoryExport.  : the hook branches on
	// searchQuery / favoritesOnly so the export matches the active
	// filter instead of silently dumping ALL history.
	const { doExport } = useHistoryExport({
		call,
		records,
		sortOrder,
		searchQuery,
		favoritesOnly,
	});

	// Sorted view of the loaded records — applied client-side so the
	// user can re-order the displayed list (and the export) without an
	// extra backend round-trip.
	const sortedRecords = useMemo(
		() => sortRecords(records, sortOrder),
		[records, sortOrder],
	);

	return (
		<>
			<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col px-16 pt-28 pb-6">
				<PageHeading
					title={t("history.title")}
					description={
						stats
							? t("history.transcriptionsToday", {
									count: String(stats.count),
									// resolve the "chars" suffix via t() so
									// other locales can translate it and the digit
									// grouping respects getLocale().
									chars:
										stats.chars > 0
											? t("history.charsSuffix", {
													count: stats.chars.toLocaleString(getLocale()),
												})
											: "",
								})
							: t("history.noTranscriptionsToday")
					}
				/>

				{/* F4: "Last updated" indicator + manual refresh.
                                    Uses `pb-2` to match the spacing on the sibling feature pages
                                    (Microphone/Templates/Vocabulary) which all settled on `pb-2`
                                    for their LastUpdatedIndicator wrapper — pre-fix this was
                                    `pb-1`, producing a visible vertical alignment mismatch on
                                    the page-header row across pages. */}
				<div className="flex justify-end pb-2">
					<LastUpdatedIndicator
						agoLabel={agoLabel}
						onRefresh={handleManualRefresh}
						refreshing={refreshing}
					/>
				</div>

				{/* Action buttons — shared filter/sort visual pattern with
                                    Vocabulary/Templates (SortSelect + muted controls,
                                    w-full flex-wrap so row wraps cleanly on narrow
                                    viewports). */}
				<div className="flex w-full flex-wrap items-center gap-2 mt-3">
					<Button
						variant="outline"
						size="sm"
						onClick={toggleFavorites}
						//aria-pressed conveys the toggle state to
						// assistive tech. The accessible name stays stable
						// (always "Favorites") so the visible label matches
						// the announced name (Label-in-Name). The toggle
						// state is communicated via aria-pressed rather
						// than by swapping the label.
						aria-pressed={favoritesOnly}
						aria-label={t("history.favorites")}
						className={`gap-2 ${
							favoritesOnly
								? "bg-warning/15 text-warning border-warning/30 hover:bg-warning/25"
								: "text-(--text-muted) hover:text-(--text-primary)"
						}`}
					>
						<HugeiconsIcon
							icon={StarIcon}
							strokeWidth={2}
							className={`h-4 w-4 ${favoritesOnly ? "text-warning" : ""}`}
						/>
						{t("history.favorites")}
					</Button>
					<Button
						variant="outline"
						size="sm"
						onClick={handleClearAll}
						aria-label={t("history.clearAllAria")}
						// Destructive Clear All — muted at rest like the
						// sibling Import/Export/Favorites controls; on hover
						// the background becomes the same solid destructive
						// red used by ConfirmDialog's Clear All action
						// (bg-destructive + text-destructive-foreground / white
						// icon). Shared pattern across History / Vocabulary /
						// Templates so hover always reads as solid red.
						className="gap-2 text-(--text-muted) hover:border-destructive hover:bg-destructive hover:text-destructive-foreground"
					>
						<HugeiconsIcon
							icon={Delete01Icon}
							strokeWidth={2}
							className="h-4 w-4"
						/>
						{t("history.clearAll")}
					</Button>
					<SortSelect
						value={sortOrder}
						onValueChange={(v) => setSortOrder(v as HistorySortOrder)}
					/>
					<div className="ms-auto">
						<ExportFormatMenu
							onExport={doExport}
							disabled={records.length === 0}
						/>
					</div>
				</div>

				{loading && records.length === 0 ? (
					<div className="flex min-h-full items-center justify-center py-20">
						<Spinner label={t("history.loading")} />
					</div>
				) : loadError && records.length === 0 ? (
					//distinguish "backend failed to load" from
					// "history is genuinely empty".
					// variant="error" so the destructive
					// tint + Alert02Icon swap make the failure visually
					// distinct from a genuine empty list (matches the
					//Vocabulary/Templates load-failure pattern from ).
					<EmptyState
						variant="error"
						icon={AlertCircleIcon}
						title={t("history.loadFailedTitle")}
						description={loadError}
						actionLabel={t("history.retry")}
						onAction={() => runLoad()}
					/>
				) : records.length === 0 ? (
					<EmptyState
						icon={HistoryIcon}
						title={
							searchQuery
								? t("history.noResults")
								: favoritesOnly
									? t("history.noFavorites")
									: t("history.noTranscriptions")
						}
						description={
							searchQuery
								? t("history.noResultsDescription")
								: favoritesOnly
									? t("history.noFavoritesDescription")
									: t("history.noTranscriptionsDescription")
						}
						actionLabel={
							!searchQuery && !favoritesOnly
								? t("history.startDictation")
								: undefined
						}
						actionIcon={Mic02Icon}
						onAction={
							!searchQuery && !favoritesOnly
								? () => navigate("home")
								: undefined
						}
					/>
				) : (
					<>
						<ActivityList
							// Visible window: at most `visibleCount` rows of the
							// loaded cache are mounted (starts at one page;
							// "Load More" widens it). The hard cap below keeps
							// the window from ever exceeding the display limit.
							items={sortedRecords.slice(
								0,
								Math.min(visibleCount, HISTORY_DISPLAY_CAP),
							)}
							lineClamp={3}
							onDelete={handleDelete}
							onToggleFavorite={handleToggleFavorite}
						/>

						{/*once the visible window reaches BOTH the end of the
                                                        loaded cache AND the 200-row display cap while the
                                                        backend still reports more available (`hasMore`),
                                                        further "Load More" clicks could not reveal anything —
                                                        rows past the cap stay hidden, so the user would click
                                                         and see nothing change. Replace the button with a
                                                        notice pointing the user at the search field to find
                                                        older entries. Below the cap (or when loaded rows are
                                                        still unrevealed), the Load More button stays useful:
                                                        each click fetches the next page AND widens the visible
                                                        window to include it.
                                                */}
						{records.length >= HISTORY_DISPLAY_CAP &&
						visibleCount >= records.length &&
						hasMore ? (
							<p className="mt-4 text-center text-xs text-(--text-muted)">
								{t("history.showingCap", { shown: "200", total: "N+" })}
							</p>
						) : hasMore ? (
							<Button
								variant="outline"
								size="default"
								onClick={() => {
									void loadMore();
									setVisibleCount((c) => c + HISTORY_PAGE_SIZE);
								}}
								disabled={loadingMore}
								className="mt-4 w-full gap-2 text-xs rounded-xl border border-dashed border-border/5"
							>
								{loadingMore ? (
									<>
										<Spinner className="border-current" />
										{t("history.loading")}
									</>
								) : (
									<>
										<HugeiconsIcon
											icon={ArrowDown01Icon}
											strokeWidth={2}
											className="h-4 w-4"
										/>
										{t("history.loadMore")}
									</>
								)}
							</Button>
						) : null}
					</>
				)}
			</div>

			{/* ConfirmDialog for Clear All. variant="destructive" is
			    explicit to match the Vocabulary Clear-All dialog — the
			    confirm button carries the destructive treatment for an
			    irreversible, privacy-adjacent wipe. */}
			<ConfirmDialog
				open={showClearConfirm}
				title={t("history.clearAllHistory")}
				//when a filter is active, the default message is
				// ambiguous (the user might think only the visible subset
				// will be deleted).  Use a clearer message that calls out
				// the hidden entries.
				message={
					filterActive
						? t("history.clearAllWithFilterMessage")
						: t("history.clearAllMessage")
				}
				confirmLabel={t("history.clearAllConfirm")}
				variant="destructive"
				onConfirm={confirmClearAll}
				onCancel={() => setShowClearConfirm(false)}
			/>
		</>
	);
}
