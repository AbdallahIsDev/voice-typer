/**
 * Shared keyset-pagination cursor for the History page.
 *
 * Both history data paths — the page cache (`hooks/useHistoryCache`) and
 * the export loop (`hooks/useHistoryExport`) — paginate with the same
 * keyset contract: ask the backend for rows strictly older than
 * `(before_timestamp, before_id)` in `(timestamp DESC, id DESC)` order.
 * When either field is absent, the backend falls back to the OFFSET path
 * (backward-compat with the pre-cursor contract).
 */

import type { HistoryRecord } from "@/types/ipc";

/** Cursor params for keyset pagination (see module docstring). */
export interface HistoryCursor {
	before_timestamp?: string;
	before_id?: number;
}

/**
 * Derive the next page's cursor from the LAST row of the accumulated
 * result set.
 *
 * Returns `undefined` when the last row lacks a usable `timestamp`/`id`
 * (e.g. legacy rows written before the `id` column existed) — the caller
 * then falls back to the OFFSET path, same as the backend's contract.
 */
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
