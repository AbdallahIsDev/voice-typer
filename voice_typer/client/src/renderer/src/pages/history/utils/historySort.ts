import type { HistoryRecord } from "@/types/ipc";

export type HistorySortOrder = "newest" | "oldest";

export function sortRecords(
	records: HistoryRecord[],
	_order: HistorySortOrder,
): HistoryRecord[] {
	return records;
}
