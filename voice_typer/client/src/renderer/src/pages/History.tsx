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
import { Button } from "@/components/ui/button";
import { useLatestRef } from "@/hooks/useLatestRef";
import { useNavigation } from "@/hooks/useNavigation";
import { usePython } from "@/hooks/usePython";
import { getLocale, t } from "@/i18n/i18n";
import { MOUNT_RERACE_FAST_WINDOW_MS } from "@/lib/snapshotCache";
import { useGlobalSearch } from "@/stores/useGlobalSearch";
import { HistorySkeleton } from "./history/components/HistorySkeleton";
import {
	HISTORY_PAGE_SIZE,
	isHistoryCacheFresh,
	peekHistoryPrefetch,
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
		hydrateFromCache,
	} = useHistoryCache();
	// True all-time row count for the display-cap footer: the same
	// `get_history_count` IPC the Analytics page's useDashboardData
	// consumes. The footer interpolates it into the existing
	// `history.showingCap` template instead of the literal "N+"
	// placeholder. `stats` is replaced by useHistoryCache on every rows
	// reload (load + background event refresh), so the count refetches
	// with the list; a failure keeps the previous value and the footer
	// degrades to the "…" placeholder below, never to "N+".
	// `call` is mirrored into a ref (the useDashboardData pattern) so
	// the effect is keyed on `stats` alone, a fresh `call` identity
	// under test mocks would otherwise re-fire it every render.
	const historyCountCallRef = useLatestRef(call);
	const [historyCount, setHistoryCount] = useState<number | null>(null);
	// biome-ignore lint/correctness/useExhaustiveDependencies: stats is a deliberate CHANGE TRIGGER (its identity is replaced on every rows reload), not a value the effect reads, the refetch key for the footer count
	useEffect(() => {
		let cancelled = false;
		historyCountCallRef
			.current<{ count: number }>("get_history_count")
			.then((res) => {
				if (!cancelled && typeof res?.count === "number") {
					setHistoryCount(res.count);
				}
			})
			.catch(() => {
				// Graceful: keep the last known count (or the placeholder).
			});
		return () => {
			cancelled = true;
		};
	}, [stats, historyCountCallRef]);

	const [favoritesOnly, setFavoritesOnly] = useState(false);
	const searchQuery = useGlobalSearch((s) => s.query);
	const [sortOrder, setSortOrder] = useState<HistorySortOrder>("newest");
	// Explicit visible-row window. The cache hook pages 50 records per
	// fetch into `records`; this state controls how many of them the
	// list actually renders. It starts at one page and every "Load More"
	// click BOTH fetches the next page (loadMore) and widens the window
	// by one page, without the widening, appended rows would be sliced
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

	// Background-event refresh pipeline, the 500ms-debounced
	// transcription_final / history_changed handler, the hidden-window
	// stale flag, the visibilitychange one-shot refresh, and the manual
	// refresh wrapper (see useHistoryEventRefresh).
	const { handleManualRefresh, refreshing } = useHistoryEventRefresh({
		refreshFromEvent,
		runLoad,
	});

	// Mount load with the TTL fast path (C-CACHE-2): a fresh
	// default-view snapshot (recent visit or hover prefetch) renders
	// from cache with no refetch. A hover still in flight is awaited
	// first and hydrated, so the mount piggybacks it. Filtered views
	// always load — the snapshot only ever holds the default view.
	// Cold-start storm (Dashboard/Models pattern): a failed mount load
	// re-races once after 8s so a boot-time failure heals without
	// manual Retry; a second failure keeps the error screen (bounded,
	// unmount cancels).
	// The query is read live from the store so filter changes keep
	// flowing through useHistorySearchReload, not this mount effect.
	useEffect(() => {
		let cancelled = false;
		let timer: ReturnType<typeof setTimeout> | null = null;
		const startedAt = Date.now();
		void (peekHistoryPrefetch() ?? Promise.resolve()).then(() => {
			if (cancelled) return;
			if (
				useGlobalSearch.getState().query.trim() === "" &&
				isHistoryCacheFresh()
			) {
				hydrateFromCache();
				return;
			}
			void runLoad().then((ok) => {
				// Re-race only fast failures (C-CACHE-10).
				if (
					!ok &&
					!cancelled &&
					Date.now() - startedAt < MOUNT_RERACE_FAST_WINDOW_MS
				) {
					timer = setTimeout(() => {
						if (!cancelled) void runLoad();
					}, 8000);
				}
			});
		});
		return () => {
			cancelled = true;
			if (timer !== null) clearTimeout(timer);
		};
	}, [runLoad, hydrateFromCache]);

	// Debounced reload driven by the GLOBAL search store, a 200ms-
	// delayed fresh load whenever the query (or the favorites filter)
	// changes, with the first-render guard (see useHistorySearchReload).
	useHistorySearchReload({ searchQuery, favoritesOnly, runLoad });

	const toggleFavorites = useCallback(() => {
		const next = !favoritesOnly;
		setFavoritesOnly(next);
		runLoad(searchQuery, next);
	}, [favoritesOnly, runLoad, searchQuery]);

	// Per-row record actions, delete-with-undo, favorite toggle, and
	// the lazy full-text fetch for expandable rows (see
	// useHistoryRecordActions).
	const { handleDelete, handleToggleFavorite, handleFetchFullText } =
		useHistoryRecordActions({ call, records, load, setRecords });

	// Clear-all flow, the filter-aware short-circuit guards, the
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

	// Sorted view of the loaded records, applied client-side so the
	// user can re-order the displayed list (and the export) without an
	// extra backend round-trip.
	const sortedRecords = useMemo(
		() => sortRecords(records, sortOrder),
		[records, sortOrder],
	);

	const groupByDate = sortOrder === "newest" || sortOrder === "oldest";

	return (
		<>
			<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col gap-6 px-16 pt-20 pb-6">
				<PageHeading
					title={t("history.title")}
					description={
						stats
							? t("history.transcriptionsToday", {
									count: String(stats.count),
									chars:
										stats.chars > 0
											? t("history.charsSuffix", {
													count: stats.chars.toLocaleString(getLocale()),
												})
											: "",
								})
							: t("history.noTranscriptionsToday")
					}
				>
					{/* Freshness + manual refresh ride the heading's own row,
					    top right — the slot the Analytics page already uses
					    for the same control. They used to open the list
					    section, which cost a whole row between the controls
					    and the card to restate a heading the controls had
					    already made obvious. */}
					<LastUpdatedIndicator
						agoLabel={agoLabel}
						onRefresh={handleManualRefresh}
						refreshing={refreshing}
					/>
				</PageHeading>

				{/* Controls and the list are ONE block: the page root's
				    gap-6 separates the heading from this block, and the
				    4px here separates the controls from the card. */}
				<div className="flex w-full flex-col gap-4">
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
										: "text-muted-foreground hover:text-foreground"
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
								className="gap-2 text-muted-foreground hover:border-destructive hover:bg-destructive hover:text-destructive-foreground dark:hover:bg-destructive"
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

					{loading && records.length === 0 ? (
						<HistorySkeleton />
					) : loadError && records.length === 0 ? (
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

							{records.length >= HISTORY_DISPLAY_CAP &&
							visibleCount >= records.length &&
							hasMore ? (
								<p className="text-center text-xs text-muted-foreground">
									{t("history.showingCap", {
										shown: String(HISTORY_DISPLAY_CAP),
										// While the count loads (or if the count fetch
										// fails) render the ellipsis placeholder, the
										// point of the line (list is capped, use search)
										// stays readable without a broken "N+" value.
										total: historyCount !== null ? String(historyCount) : "…",
									})}
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
									className="w-full gap-2 text-xs rounded-lg border border-dashed border-border/8"
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

			<ConfirmDialog
				open={showClearConfirm}
				title={t("history.clearAllHistory")}
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
