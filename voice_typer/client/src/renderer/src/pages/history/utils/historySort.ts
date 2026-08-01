import type { HistoryRecord } from "@/types/ipc";

export type HistorySortOrder = "newest" | "oldest" | "az" | "za";

/**
 * Sort history records by the given order.
 *
 * `newest` / `oldest` sort by `timestamp` (descending / ascending).
 * `az` / `za` sort by the transcription text using locale-aware collation
 * (ascending / descending). When text is empty or equal, falls back to
 * `timestamp` so the order is deterministic.
 */
export function sortRecords(
	records: HistoryRecord[],
	order: HistorySortOrder,
): HistoryRecord[] {
	const collator = new Intl.Collator(undefined, {
		sensitivity: "base",
		numeric: true,
	});
	const copy = [...records];

	switch (order) {
		case "newest":
			return copy.sort(
				(a, b) => Date.parse(b.timestamp ?? "") - Date.parse(a.timestamp ?? ""),
			);
		case "oldest":
			return copy.sort(
				(a, b) => Date.parse(a.timestamp ?? "") - Date.parse(b.timestamp ?? ""),
			);
		case "az":
			return copy.sort((a, b) => {
				const cmp = collator.compare(a.text ?? "", b.text ?? "");
				return cmp !== 0
					? cmp
					: Date.parse(b.timestamp ?? "") - Date.parse(a.timestamp ?? "");
			});
		case "za":
			return copy.sort((a, b) => {
				const cmp = collator.compare(b.text ?? "", a.text ?? "");
				return cmp !== 0
					? cmp
					: Date.parse(b.timestamp ?? "") - Date.parse(a.timestamp ?? "");
			});
		default:
			return copy;
	}
}

/**
 * Runtime type guard for `HistorySortOrder`.
 *
 * Returns the value verbatim when it is one of the four valid sort orders;
 * otherwise falls back to `"newest"` (the backend default).
 */
export function parseHistorySortOrder(value: unknown): HistorySortOrder {
	if (
		value === "newest" ||
		value === "oldest" ||
		value === "az" ||
		value === "za"
	) {
		return value;
	}
	return "newest";
}
