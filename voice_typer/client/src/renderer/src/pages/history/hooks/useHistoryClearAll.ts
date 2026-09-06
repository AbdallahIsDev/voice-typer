// History clear-all flow hook.
//
// Extracted from `pages/History.tsx` (page-root slimming): the Clear All
// flow — confirmation-dialog state, the filter-aware short-circuit
// guards, and the destructive `clear_history` apply — is cohesive event
// logic that belongs in a named, testable hook instead of the page
// root.
//
// Clear All is ambiguous under an active filter (the visible list is a
// subset of ALL history). When a filter is active the arm step checks
// the cached stats count instead of the visible rows (the visible list
// may be empty while the total is not) and the confirmation message
// calls out the hidden entries — the page renders the filter-aware
// message via the returned `filterActive`.

import { useCallback, useState } from "react";
import { toast } from "sonner";
import type { usePython } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
import type { HistoryRecord, TodayStats } from "@/types/ipc";

export interface UseHistoryClearAllOptions {
	/** The Python bridge call (from usePython). */
	call: ReturnType<typeof usePython>["call"];
	/** The currently loaded (visible-window) records. */
	records: HistoryRecord[];
	/** The cached today-stats (carries the authoritative total count). */
	stats: TodayStats | null;
	/** The active global-search query (filter guard). */
	searchQuery: string;
	/** Whether the favorites-only filter is active (filter guard). */
	favoritesOnly: boolean;
	/** The cache hook's records-state setter. */
	setRecords: React.Dispatch<React.SetStateAction<HistoryRecord[]>>;
	/** The cache hook's stats-state setter. */
	setStats: React.Dispatch<React.SetStateAction<TodayStats>>;
	/** The cache hook's hasMore-state setter. */
	setHasMore: React.Dispatch<React.SetStateAction<boolean>>;
}

export interface UseHistoryClearAllReturn {
	/** Confirm-dialog visibility state (rendered by the page). */
	showClearConfirm: boolean;
	setShowClearConfirm: React.Dispatch<React.SetStateAction<boolean>>;
	/** Whether a search/favorites filter is active (message variant). */
	filterActive: boolean;
	/** Arm the confirmation dialog (guarded; may no-op). */
	handleClearAll: () => void;
	/** The confirmed wipe (ConfirmDialog's onConfirm). */
	confirmClearAll: () => Promise<void>;
}

/**
 * Clear-all flow for the History page. See the file header for the
 * extraction rationale.
 */
export function useHistoryClearAll({
	call,
	records,
	stats,
	searchQuery,
	favoritesOnly,
	setRecords,
	setStats,
	setHasMore,
}: UseHistoryClearAllOptions): UseHistoryClearAllReturn {
	const [showClearConfirm, setShowClearConfirm] = useState(false);

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

	return {
		showClearConfirm,
		setShowClearConfirm,
		filterActive,
		handleClearAll,
		confirmClearAll,
	};
}
