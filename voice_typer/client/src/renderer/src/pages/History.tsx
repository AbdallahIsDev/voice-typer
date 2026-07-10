import {
	ArrowDown01Icon,
	Delete01Icon,
	HistoryIcon,
	Mic02Icon,
	StarIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import ExportFormatMenu from "@/components/common/ExportFormatMenu";
import PageHeading from "@/components/common/PageHeading";
import { SearchField } from "@/components/common/SearchField";
import ActivityList from "@/components/dashboard/ActivityList";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Spinner } from "@/components/feedback/Spinner";
import { Button } from "@/components/ui/button";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { showUndoableToast } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import type {
	HistoryRecord,
	Page,
	TodayStats,
	WindowBridge,
} from "@/types/ipc";

// Module-level cache — persists across page navigations so the records list
// and stats render instantly on re-visit instead of showing a spinner.
let _cachedRecords: HistoryRecord[] = [];
let _cachedStats: TodayStats | null = null;

const PAGE_SIZE = 50;

interface HistoryPageProps {
	/** Navigation callback used by the empty-state's "Start dictation" button. */
	onNavigate?: (page: Page) => void;
}

export default function HistoryPage({ onNavigate }: HistoryPageProps = {}) {
	const { call } = usePython();
	const [records, setRecords] = useState<HistoryRecord[]>(_cachedRecords);
	const [stats, setStats] = useState<TodayStats | null>(_cachedStats);
	const [loading, setLoading] = useState(true);
	const [loadingMore, setLoadingMore] = useState(false);
	const [hasMore, setHasMore] = useState(true);
	const [searchQuery, setSearchQuery] = useState("");
	const [favoritesOnly, setFavoritesOnly] = useState(false);
	// Refs so load() and the event handler always read current filter values
	// without being recreated on every state change (which would break the
	// mount-only useEffect and cause duplicate subscriptions).
	const searchQueryRef = useRef(searchQuery);
	const favoritesOnlyRef = useRef(favoritesOnly);
	searchQueryRef.current = searchQuery;
	favoritesOnlyRef.current = favoritesOnly;
	const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const [showClearConfirm, setShowClearConfirm] = useState(false);
	const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

	const load = useCallback(
		async (query?: string, favs?: boolean) => {
			setLoading(true);
			try {
				const isFav = favs ?? favoritesOnlyRef.current;
				const q = query ?? searchQueryRef.current;

				let recs: HistoryRecord[];
				if (q.trim()) {
					recs = await call<HistoryRecord[]>("search_history", {
						query: q.trim(),
						limit: PAGE_SIZE,
						offset: 0,
					});
				} else if (isFav) {
					recs = await call<HistoryRecord[]>("get_favorites", {
						limit: PAGE_SIZE,
						offset: 0,
					});
				} else {
					recs = await call<HistoryRecord[]>("get_history", {
						limit: PAGE_SIZE,
						offset: 0,
					});
				}
				// Only cache the all-records view — search/filter results are transient
				// and shouldn't pollute the cache that initializes the page on re-visit.
				if (!q.trim() && !isFav) {
					_cachedRecords = recs;
				}
				setHasMore(recs.length >= PAGE_SIZE);
				setRecords(recs);

				const todayStats = await call<TodayStats>("get_today_stats");
				_cachedStats = todayStats;
				setStats(todayStats);
			} catch (err) {
				console.error("Failed to load history:", err);
			} finally {
				setLoading(false);
			}
		},
		[call],
	);

	const loadMore = useCallback(async () => {
		setLoadingMore(true);
		try {
			const isFav = favoritesOnlyRef.current;
			const q = searchQueryRef.current;
			const offset = records.length;

			let newRecs: HistoryRecord[];
			if (q.trim()) {
				newRecs = await call<HistoryRecord[]>("search_history", {
					query: q.trim(),
					limit: PAGE_SIZE,
					offset,
				});
			} else if (isFav) {
				newRecs = await call<HistoryRecord[]>("get_favorites", {
					limit: PAGE_SIZE,
					offset,
				});
			} else {
				newRecs = await call<HistoryRecord[]>("get_history", {
					limit: PAGE_SIZE,
					offset,
				});
			}
			setHasMore(newRecs.length >= PAGE_SIZE);
			if (newRecs.length > 0) {
				setRecords((prev) => [...prev, ...newRecs]);
			}
		} catch (err) {
			console.error("Failed to load more history:", err);
		} finally {
			setLoadingMore(false);
		}
	}, [call, records.length]);

	// ── Proactive background refresh after new transcriptions ────────
	//
	// When a transcription_final event arrives (from any page), refresh the
	// cached stats and records so the next visit to History shows fresh data
	// instead of stale cache.  If the user is *already* on the History page
	// and not mid-search, also update the visible UI.
	usePythonEvent(
		"transcription_final",
		useCallback(() => {
			if (refreshTimer.current) clearTimeout(refreshTimer.current);
			refreshTimer.current = setTimeout(async () => {
				try {
					const [newStats, newRecs] = await Promise.all([
						call<TodayStats>("get_today_stats"),
						call<HistoryRecord[]>("get_history", {
							limit: PAGE_SIZE,
							offset: 0,
						}),
					]);
					_cachedStats = newStats;
					_cachedRecords = newRecs;
					setStats(newStats);
					// Only replace visible records when no search/filter is active
					if (!searchQueryRef.current && !favoritesOnlyRef.current) {
						setHasMore(newRecs.length >= PAGE_SIZE);
						setRecords(newRecs);
					}
				} catch {
					// Silently ignore — the next manual load will pick up fresh data
				}
			}, 500);
		}, [call]),
	);

	// Clean up pending refresh timer on unmount
	useEffect(() => {
		return () => {
			if (refreshTimer.current) clearTimeout(refreshTimer.current);
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
			// NEW-UX-004: capture the record before delete so we can offer Undo.
			const deleted = records.find((r) => r.id === id);
			try {
				await call("delete_history", { id });
				setRecords((prev) => prev.filter((r) => r.id !== id));
				// NEW-UX-004: show an undoable toast.  When the user clicks
				// Undo, we re-add the record via the `restore_history` IPC
				// command (added to the backend below).  This matches the
				// macOS Mail / iOS Photos "delete now, undo for 6 seconds"
				// pattern.
				if (deleted) {
					showUndoableToast(
						t("history.entryDeleted"),
						async () => {
							try {
								await call("restore_history", { record: deleted });
								// Reload to reflect the restored entry.
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
		[call, records, load],
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
		[call],
	);

	const handleClearAll = useCallback(async () => {
		// Nothing to clear — don't call backend, don't show dialog
		if (records.length === 0) return;

		// #7: Show ConfirmDialog instead of the old two-click pattern
		setShowClearConfirm(true);
	}, [records.length]);

	const confirmClearAll = useCallback(async () => {
		try {
			await call("clear_history");
			const emptyStats = { count: 0, chars: 0, word_count: 0, duration: 0 };
			_cachedStats = emptyStats;
			_cachedRecords = [];
			setRecords([]);
			setStats(emptyStats);
			setHasMore(false);
			toast.success(t("history.historyCleared"));
		} catch {
			toast.error(t("history.clearFailed"));
		} finally {
			setShowClearConfirm(false);
		}
	}, [call]);

	const doExport = useCallback(
		async (format: "json" | "csv") => {
			if (records.length === 0) {
				toast.error(t("history.exportEmpty"));
				return;
			}
			try {
				const all = await call<HistoryRecord[]>("get_history", {
					limit: 10000,
				});
				const result = await (window.window_ as WindowBridge).exportHistory(
					all as unknown as Record<string, unknown>[],
					format,
				);
				if (result.success) {
					// ERR-ERR-005 (fix): null-safe path handling instead of `!` assertions
					const path = result.path ?? "";
					const filename = path.split(/[\\/]/).pop() || "untitled";
					toast.success(t("history.exportSaved", { filename }));
				}
			} catch (err) {
				console.error("History export failed:", err);
				toast.error(t("history.exportFailed"));
			}
		},
		[call, records.length],
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
									chars:
										stats.chars > 0
											? ` (${stats.chars.toLocaleString()} chars)`
											: "",
								})
							: t("history.noTranscriptionsToday")
					}
				/>

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
						aria-label={
							favoritesOnly ? t("history.showAll") : t("history.showFavorites")
						}
						className={`gap-2 ${
							favoritesOnly
								? "bg-amber-400/15 text-amber-400 border-amber-400/30 hover:bg-amber-400/20"
								: "text-(--text-muted) hover:text-(--text-primary)"
						}`}
					>
						<HugeiconsIcon
							icon={StarIcon}
							strokeWidth={2}
							className={`h-4 w-4 ${favoritesOnly ? "text-amber-400" : ""}`}
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
							!searchQuery && !favoritesOnly && onNavigate
								? t("history.startDictation")
								: undefined
						}
						actionIcon={Mic02Icon}
						onAction={
							!searchQuery && !favoritesOnly && onNavigate
								? () => onNavigate("home")
								: undefined
						}
					/>
				) : (
					<>
						<ActivityList
							items={records}
							lineClamp={3}
							onDelete={handleDelete}
							onToggleFavorite={handleToggleFavorite}
						/>

						{hasMore && (
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
						)}
					</>
				)}
			</div>

			{/* #7: ConfirmDialog for Clear All */}
			<ConfirmDialog
				open={showClearConfirm}
				title={t("history.clearAllHistory")}
				message={t("history.clearAllMessage")}
				confirmLabel={t("history.clearAllConfirm")}
				onConfirm={confirmClearAll}
				onCancel={() => setShowClearConfirm(false)}
			/>
		</>
	);
}
