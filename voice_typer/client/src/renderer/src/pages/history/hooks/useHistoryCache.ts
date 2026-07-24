import { useCallback, useState } from "react";
import type { HistoryRecord, TodayStats } from "@/types/ipc";

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
	const [loading] = useState(false);
	const [loadingMore] = useState(false);
	const [loadError] = useState<string | null>(null);

	const load = useCallback(
		async (_query?: string, _favoritesOnly?: boolean) => {},
		[],
	);
	const loadMore = useCallback(async () => {}, []);
	const refreshFromEvent = useCallback(async () => {}, []);

	return {
		records,
		stats,
		loading,
		loadingMore,
		hasMore,
		loadError,
		agoLabel: "",
		setRecords,
		setStats,
		setHasMore,
		load,
		loadMore,
		refreshFromEvent,
		setFilter: () => {},
	};
}
