//pure dashboard display/format helpers extracted from
// `pages/Dashboard.tsx`.
//
// These helpers render human-facing strings (day-of-week abbreviations,
// "Today"/"Yesterday"/date labels, bar-height scaling). They have no
// React dependency — `t` / `getLocale` resolve the active i18n locale
// at call time.

import { getLocale, t } from "@/i18n/i18n";

import { localDateKey } from "./streaks";

/** Determine the max bar height based on data range. */
export function barHeight(count: number, max: number): number {
	if (max === 0) return 8;
	return Math.max(8, Math.round((count / max) * 64));
}

/** Get day-of-week abbreviation for a date string. */
export function dayAbbr(dateStr: string): string {
	const days = [
		t("analytics.days.sun"),
		t("analytics.days.mon"),
		t("analytics.days.tue"),
		t("analytics.days.wed"),
		t("analytics.days.thu"),
		t("analytics.days.fri"),
		t("analytics.days.sat"),
	];
	try {
		return days[new Date(dateStr).getDay()];
	} catch {
		return dateStr;
	}
}

/** Get a human-friendly label like "Today", "Yesterday", or the date. */
export function dayLabel(dateStr: string): string {
	try {
		const today = new Date();
		const yesterday = new Date(today);
		yesterday.setDate(yesterday.getDate() - 1);
		//use localDateKey (not toISOString().slice) so the
		// "Today" / "Yesterday" comparison honors the user's local
		// calendar day instead of UTC.
		if (dateStr === localDateKey(today)) return t("analytics.today");
		if (dateStr === localDateKey(yesterday)) return t("analytics.yesterday");
		//format the MM-DD fallback in the user-selected UI
		// locale instead of slicing the ISO string (which is always
		// Gregorian/ASCII and ignores locale-aware month formatting).
		try {
			return new Intl.DateTimeFormat(getLocale(), {
				month: "short",
				day: "2-digit",
			}).format(new Date(dateStr));
		} catch {
			return dateStr.slice(5); // "MM-DD"
		}
	} catch {
		return dateStr;
	}
}
