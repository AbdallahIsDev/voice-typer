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
import { SearchField } from "@/components/common/SearchField";
import ActivityList from "@/components/dashboard/ActivityList";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Spinner } from "@/components/feedback/Spinner";
import { Button } from "@/components/ui/button";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { useNavigation } from "@/hooks/useNavigation";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { showUndoableToast } from "@/hooks/useSnackbar";
import { getLocale, t } from "@/i18n/i18n";
import { useHistoryCache } from "./history/hooks/useHistoryCache";
import { useHistoryExport } from "./history/hooks/useHistoryExport";
import {
	type HistorySortOrder,
	sortRecords,
} from "./history/utils/historySort";

// NOTE: App.tsx prop passing will be removed by EC-FIX-13.
// EC-FIX-14 (BACKLOG-004): HistoryPage now obtains `navigate` via the
// useNavigation hook directly, eliminating the `onNavigate` prop drill.
//
// BG-54 (spaghetti split): the cache + IPC lifecycle (load / loadMore /
// event refresh) lives in `useHistoryCache`, the export paging loop
// lives in `useHistoryExport`, and the client-side sort lives in
// `historySort.ts`. This file is the thin view component.
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
	const [searchQuery, setSearchQuery] = useState("");
	const [favoritesOnly, setFavoritesOnly] = useState(false);
	const [sortOrder, setSortOrder] = useState<HistorySortOrder>("newest");
	const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const [showClearConfirm, setShowClearConfirm] = useState(false);
	const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const [refreshing, setRefreshing] = useState(false);

	// Keep the cache hook's filter refs in sync with the page state.
	setFilter(searchQuery, favoritesOnly);

	const handleManualRefresh = useCallback(async () => {
		setRefreshing(true);
		try {
			await load();
		} finally {
			setRefreshing(false);
		}
	}, [load]);

	// R7-F13: extracted `debouncedRefreshFromEvent` via useCallback.
	// Wraps `refreshFromEvent` (owned by useHistoryCache) in a 500ms
	// debounce so rapid transcription_final / history_changed events
	// coalesce into a single backend fetch.
	const debouncedRefreshFromEvent = useCallback(():
		| (() => void)
		| undefined => {
		if (refreshTimer.current) clearTimeout(refreshTimer.current);
		refreshTimer.current = setTimeout(async () => {
			try {
				await refreshFromEvent();
			} catch (e) {
				console.warn("[History] background refresh failed:", e);
			}
		}, 500);
		return undefined;
	}, [refreshFromEvent]);

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
		load();
	}, [load]);

	const handleSearch = useCallback(
		(value: string) => {
			setSearchQuery(value);
			if (searchTimer.current) clearTimeout(searchTimer.current);
			searchTimer.current = setTimeout(() => {
				load(value, favoritesOnly);
			}, 200);
		},
		[load, favoritesOnly],
	);

	const toggleFavorites = useCallback(() => {
		const next = !favoritesOnly;
		setFavoritesOnly(next);
		load(searchQuery, next);
	}, [favoritesOnly, load, searchQuery]);

	const handleDelete = useCallback(
		async (id: number) => {
			// NEW-UX-004: capture the record before delete for Undo.
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

	// BG-53: Clear All is ambiguous under an active filter (the visible
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

	// BG-54: doExport (filter-aware paging loop) extracted to
	// useHistoryExport.  BG-52: the hook branches on
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
			<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
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

				{/* F4: "Last updated" indicator + manual refresh. */}
				<div className="flex justify-end pb-1">
					<LastUpdatedIndicator
						agoLabel={agoLabel}
						onRefresh={handleManualRefresh}
						refreshing={refreshing}
					/>
				</div>

				{/* Search */}
				<div className="mt-4">
					<SearchField
						value={searchQuery}
						onChange={handleSearch}
						placeholder={t("history.searchPlaceholder")}
					/>
				</div>

				{/* Action buttons */}
				<div className="flex items-center gap-2 mt-3">
					<Button
						variant="outline"
						size="sm"
						onClick={toggleFavorites}
						// BG-51: aria-pressed conveys the toggle state to
						// assistive tech. The accessible name stays stable
						// (always "Favorites") so the visible label matches
						// the announced name (Label-in-Name). The toggle
						// state is communicated via aria-pressed rather
						// than by swapping the label.
						aria-pressed={favoritesOnly}
						aria-label={t("history.favorites")}
						className={`gap-2 ${
							favoritesOnly
								? "bg-amber-400/15 text-amber-700 border-amber-400/30 hover:bg-amber-400/20 dark:text-amber-400"
								: "text-(--text-muted) hover:text-(--text-primary)"
						}`}
					>
						<HugeiconsIcon
							icon={StarIcon}
							strokeWidth={2}
							className={`h-4 w-4 ${favoritesOnly ? "text-amber-700 dark:text-amber-400" : ""}`}
						/>
						{t("history.favorites")}
					</Button>
					<Button
						variant="outline"
						size="sm"
						onClick={handleClearAll}
						aria-label={t("history.clearAllAria")}
						className="gap-2 text-(--text-muted) hover:text-red-400"
					>
						<HugeiconsIcon
							icon={Delete01Icon}
							strokeWidth={2}
							className="h-4 w-4"
						/>
						{t("history.clearAll")}
					</Button>
					{/* Sort dropdown — client-side re-order of the loaded records. */}
					<Select
						value={sortOrder}
						onValueChange={(v) => setSortOrder(v as HistorySortOrder)}
					>
						<SelectTrigger
							size="sm"
							aria-label={t("common.sortAria")}
							className="gap-2 h-8 rounded-lg border-border px-3 text-xs text-(--text-muted) hover:text-(--text-primary)"
						>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="newest">{t("common.sortNewest")}</SelectItem>
							<SelectItem value="oldest">{t("common.sortOldest")}</SelectItem>
							<SelectItem value="az">{t("common.sortAZ")}</SelectItem>
							<SelectItem value="za">{t("common.sortZA")}</SelectItem>
						</SelectContent>
					</Select>
					<div className="ml-auto">
						<ExportFormatMenu
							onExport={doExport}
							disabled={records.length === 0}
						/>
					</div>
				</div>

				{loading && records.length === 0 ? (
					<div className="flex min-h-full items-center justify-center py-20">
						<Spinner />
					</div>
				) : loadError && records.length === 0 ? (
					// NF-R10-1: distinguish "backend failed to load" from
					// "history is genuinely empty".
					// variant="error" so the destructive
					// tint + Alert02Icon swap make the failure visually
					// distinct from a genuine empty list (matches the
					// Vocabulary/Templates load-failure pattern from BG-60).
					<EmptyState
						variant="error"
						icon={AlertCircleIcon}
						title={t("history.loadFailedTitle")}
						description={loadError}
						actionLabel={t("history.retry")}
						onAction={() => load()}
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
							// R7-F16: cap visible list at 200 items.
							items={sortedRecords.slice(0, 200)}
							lineClamp={3}
							onDelete={handleDelete}
							onToggleFavorite={handleToggleFavorite}
						/>

						{/*
							CR-054: once `records.length` reaches the 200-item
							display cap AND the backend still reports more
							available (`hasMore`), further "Load More" clicks
							would be silent no-ops — `records.slice(0, 200)`
							above hides any items past 200, so the user would
							click Load More and see nothing change for several
							clicks.  Replace the button with a notice pointing
							the user at the search field to find older entries.
							When `records.length < 200`, the Load More button is
							still useful (it grows the visible list below the
							cap), so we keep it.
						*/}
						{records.length >= 200 && hasMore ? (
							<p className="mt-4 text-center text-xs text-(--text-muted)">
								{t("history.showingCap", { shown: "200", total: "N+" })}
							</p>
						) : hasMore ? (
							<Button
								variant="outline"
								size="default"
								onClick={loadMore}
								disabled={loadingMore}
								className="mt-4 w-full gap-2 text-xs rounded-xl border border-dashed border-border/30"
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

			{/* #7: ConfirmDialog for Clear All */}
			<ConfirmDialog
				open={showClearConfirm}
				title={t("history.clearAllHistory")}
				// BG-53: when a filter is active, the default message is
				// ambiguous (the user might think only the visible subset
				// will be deleted).  Use a clearer message that calls out
				// the hidden entries.
				message={
					filterActive
						? t("history.clearAllWithFilterMessage")
						: t("history.clearAllMessage")
				}
				confirmLabel={t("history.clearAllConfirm")}
				onConfirm={confirmClearAll}
				onCancel={() => setShowClearConfirm(false)}
			/>
		</>
	);
}
