import { getLocale } from "@/i18n/i18n";
import type { HistoryRecord } from "@/types/ipc";

export type HistorySortOrder = "newest" | "oldest" | "az" | "za";

export function sortRecords(
	records: HistoryRecord[],
	order: HistorySortOrder,
): HistoryRecord[] {
	const collator = new Intl.Collator(getLocale(), {
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
