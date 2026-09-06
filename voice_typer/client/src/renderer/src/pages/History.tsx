import {
	AlertCircleIcon,
	ArrowDown01Icon,
	Delete01Icon,
	HistoryIcon,
	Mic02Icon,
	StarIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import ExportFormatMenu from "@/components/common/ExportFormatMenu";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import PageHeading from "@/components/common/PageHeading";
import { SortSelect } from "@/components/common/SortSelect";
import ActivityList from "@/components/dashboard/ActivityList";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Spinner } from "@/components/feedback/Spinner";
import { ListPageSkeleton } from "@/components/feedback/skeletons";
import { Button } from "@/components/ui/button";
import { useGlobalSearch } from "@/hooks/useGlobalSearch";
import { useNavigation } from "@/hooks/useNavigation";
import { usePython } from "@/hooks/usePython";
import { getLocale, t } from "@/i18n/i18n";
import {
	HISTORY_PAGE_SIZE,
	useHistoryCache,
} from "./history/hooks/useHistoryCache";
import { useHistoryClearAll } from "./history/hooks/useHistoryClearAll";
import { useHistoryEventRefresh } from "./history/hooks/useHistoryEventRefresh";
import { useHistoryExport } from "./history/hooks/useHistoryExport";
import { useHistoryRecordActions } from "./history/hooks/useHistoryRecordActions";
import { useHistorySearchReload } from "./history/hooks/useHistorySearchReload";
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

	// Background-event refresh pipeline — the 500ms-debounced
	// transcription_final / history_changed handler, the hidden-window
	// stale flag, the visibilitychange one-shot refresh, and the manual
	// refresh wrapper (see useHistoryEventRefresh).
	const { handleManualRefresh, refreshing } = useHistoryEventRefresh({
		refreshFromEvent,
		runLoad,
	});

	useEffect(() => {
		runLoad();
	}, [runLoad]);

	// Debounced reload driven by the GLOBAL search store — a 200ms-
	// delayed fresh load whenever the query (or the favorites filter)
	// changes, with the first-render guard (see useHistorySearchReload).
	useHistorySearchReload({ searchQuery, favoritesOnly, runLoad });

	const toggleFavorites = useCallback(() => {
		const next = !favoritesOnly;
		setFavoritesOnly(next);
		runLoad(searchQuery, next);
	}, [favoritesOnly, runLoad, searchQuery]);

	// Per-row record actions — delete-with-undo, favorite toggle, and
	// the lazy full-text fetch for expandable rows (see
	// useHistoryRecordActions).
	const { handleDelete, handleToggleFavorite, handleFetchFullText } =
		useHistoryRecordActions({ call, records, load, setRecords });

	// Clear-all flow — the filter-aware short-circuit guards, the
	// confirmation-dialog state, and the destructive apply (see
	// useHistoryClearAll).
	const {
		showClearConfirm,
		setShowClearConfirm,
		filterActive,
		handleClearAll,
		confirmClearAll,
	} = useHistoryClearAll({
		call,
		records,
		stats,
		searchQuery,
		favoritesOnly,
		setRecords,
		setStats,
		setHasMore,
	});

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

	// Date-grouped sections are only meaningful when the list reads
	// chronologically — grouping an alphabetical sort would interleave
	// date headers between A→Z entries and break the reading order.
	const groupByDate = sortOrder === "newest" || sortOrder === "oldest";

	return (
		<>
			<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col gap-6 px-16 pt-28 pb-6">
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

				{/* Action buttons — shared filter/sort visual pattern with
                                    Vocabulary/Templates (SortSelect + muted controls,
                                    w-full flex-wrap so row wraps cleanly on narrow
                                    viewports). */}
				<div className="flex w-full flex-wrap items-center justify-between gap-2">
					<div className="flex flex-wrap items-center gap-2">
						<Button
							variant="outline"
							size="sm"
							onClick={toggleFavorites}
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
							className="gap-2 text-(--text-muted) hover:border-destructive hover:bg-destructive hover:text-destructive-foreground dark:hover:bg-destructive"
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
					</div>
					<div>
						<ExportFormatMenu
							onExport={doExport}
							disabled={records.length === 0}
						/>
					</div>
				</div>

				{/* The label row and the content below it form ONE section:
                                    the wrapper's tight gap (matching the list's own
                                    header rhythm on Home) keeps the "Recent Activity"
                                    header glued to its card instead of floating at the
                                    page container's wide gap-6 rhythm. */}
				<div className="flex w-full flex-col gap-2.5">
					{/* Section label + freshness share ONE row: "Recent Activity"
                                            on the left, "Last updated … ago" + refresh on the right
                                            (justify-between). The indicator previously lived in its
                                            own full-width right-aligned row, leaving a large empty
                                            gap on the left; anchoring it to the list header gives
                                            the refresh control a logical home next to the list it
                                            refreshes. Label styling matches the list header row on
                                            Home (`text-[12px] font-semibold`). */}
					<div className="flex w-full items-center justify-between">
						<span className="text-[12px] font-semibold text-(--text-primary)">
							{t("home.recentActivity")}
						</span>
						<LastUpdatedIndicator
							agoLabel={agoLabel}
							onRefresh={handleManualRefresh}
							refreshing={refreshing}
						/>
					</div>

					{loading && records.length === 0 ? (
						<ListPageSkeleton />
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
						<div className="flex flex-col gap-4">
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
								groupByDate={groupByDate}
								onFetchFullText={handleFetchFullText}
								hideHeader
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
								<p className="text-center text-xs text-(--text-muted)">
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
									className="w-full gap-2 text-xs rounded-xl border border-dashed border-border/5"
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
						</div>
					)}
				</div>
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
