import { useCallback } from "react";

interface UseHistoryExportParams {
	call: <T = unknown>(
		type: string,
		data?: Record<string, unknown>,
	) => Promise<T>;
	records: unknown[];
	sortOrder: string;
	searchQuery: string;
	favoritesOnly: boolean;
}

interface UseHistoryExportReturn {
	doExport: (format: string) => Promise<void>;
}

export function useHistoryExport(
	_params: UseHistoryExportParams,
): UseHistoryExportReturn {
	const doExport = useCallback(async (_format: string) => {}, []);
	return { doExport };
}
