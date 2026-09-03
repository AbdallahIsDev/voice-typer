// History date-grouping helpers.
//
// Pure, React-agnostic helpers that turn the flat (already-sorted)
// history record list into per-day sections for the History page's
// grouped list view. Bucketing reuses the shared UTC-timestamp parsing
// (`parseUtcTimestamp`) + local-calendar day keys (`localDateKey` from
// `@/lib/format`) so a group header can never disagree with the
// Dashboard's day-bucketed stats for the same record.
//
// Records arrive in the page's sort order (newest→oldest for the
// default sort, oldest→newest when the user flips it); groups are
// emitted in FIRST-ENCOUNTER order so section headers follow the
// user's chosen sort direction. Alphabetical sorts ("az" / "za") never
// reach this module — the page disables grouping for them because
// interleaved date headers would break the alphabetical reading order.

import { getLocale, t } from "@/i18n/i18n";
import { localDateKey, parseUtcTimestamp } from "@/lib/format";
import type { HistoryRecord } from "@/types/ipc";

/** One per-day section of the History page's grouped list. */
export interface HistoryDateGroup {
	/** Local-calendar ``YYYY-MM-DD`` key ("" for records with no parseable timestamp). */
	key: string;
	/** Localized heading ("Today" / "Yesterday" / long date; "" for the invalid bucket). */
	label: string;
	/** The day's records, in the caller's sort order. */
	records: HistoryRecord[];
}

/** Local-calendar day key for a history timestamp ("" when missing/unparseable). */
export function recordDayKey(timestamp: string | undefined): string {
	if (!timestamp) return "";
	try {
		const d = parseUtcTimestamp(timestamp);
		return Number.isNaN(d.getTime()) ? "" : localDateKey(d);
	} catch {
		return "";
	}
}

/**
 * Localized section heading for a day key.
 *
 * Today / Yesterday reuse the ``analytics.today`` / ``analytics.yesterday``
 * keys (shipped in all 8 locales by the dashboard). Older days render
 * as a locale-aware long date — month + day, plus the year when the
 * entry is not from the current year (history routinely spans years).
 */
export function dayGroupHeading(key: string): string {
	if (!key) return "";
	const now = new Date();
	if (key === localDateKey(now)) return t("analytics.today");
	const yesterday = new Date(now);
	yesterday.setDate(yesterday.getDate() - 1);
	if (key === localDateKey(yesterday)) return t("analytics.yesterday");
	const parts = key.split("-");
	const y = Number(parts[0]);
	const m = Number(parts[1]);
	const d = Number(parts[2]);
	if (!y || !m || !d) return key;
	const date = new Date(y, m - 1, d);
	const opts: Intl.DateTimeFormatOptions = { month: "long", day: "numeric" };
	if (y !== now.getFullYear()) opts.year = "numeric";
	return date.toLocaleDateString(getLocale(), opts);
}

/**
 * Chunk records into per-day groups, preserving the caller's order.
 * Records with missing/unparseable timestamps share one trailing-key
 * bucket (key "" / empty label) that the renderer lists without a
 * section header instead of crashing or silently dropping rows.
 */
export function groupRecordsByDate(
	records: HistoryRecord[],
): HistoryDateGroup[] {
	const groups: HistoryDateGroup[] = [];
	const byKey = new Map<string, HistoryDateGroup>();
	for (const record of records) {
		const key = recordDayKey(record.timestamp);
		let group = byKey.get(key);
		if (!group) {
			group = { key, label: dayGroupHeading(key), records: [] };
			byKey.set(key, group);
			groups.push(group);
		}
		group.records.push(record);
	}
	return groups;
}

/**
 * Locale-aware time-of-day for a history timestamp ("05:54 PM").
 * Rows in a grouped list show ONLY the time — the date lives in the
 * section header. Falls back to the raw string when unparseable.
 */
export function formatRecordTime(timestamp: string | undefined): string {
	if (!timestamp) return "";
	try {
		const d = parseUtcTimestamp(timestamp);
		if (Number.isNaN(d.getTime())) return timestamp;
		return d.toLocaleTimeString(getLocale(), {
			hour: "2-digit",
			minute: "2-digit",
		});
	} catch {
		return timestamp;
	}
}
