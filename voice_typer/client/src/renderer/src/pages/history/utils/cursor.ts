import type { HistoryRecord } from "@/types/ipc";

/** Cursor params for keyset pagination (see module docstring). */
export interface HistoryCursor {
	before_timestamp?: string;
	before_id?: number;
}

export function deriveHistoryCursor(
	rows: HistoryRecord[],
): HistoryCursor | undefined {
	const last = rows[rows.length - 1];
	if (!last) return undefined;
	if (typeof last.timestamp !== "string" || last.timestamp.length === 0) {
		return undefined;
	}
	if (typeof last.id !== "number" || !Number.isFinite(last.id)) {
		return undefined;
	}
	return { before_timestamp: last.timestamp, before_id: last.id };
}
