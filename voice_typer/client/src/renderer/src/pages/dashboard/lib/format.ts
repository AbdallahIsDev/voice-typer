//pure dashboard display/format helpers extracted from
// `pages/Dashboard.tsx`.
//
// These helpers render human-facing strings (day-of-week abbreviations
// for chart tick labels). They have no React dependency — `t` resolves
// the active i18n locale at call time.
//
// This module imports ONLY from `@/i18n/i18n` — in particular it does
// NOT import from `./streaks`, so no import cycle can form between the
// two dashboard lib modules (`./streaks` imports `dayAbbr` from here).

import { t } from "@/i18n/i18n";

/** Get day-of-week abbreviation for a date string. */
export function dayAbbr(dateStr: string): string {
	try {
		const label = weekdayLabel(new Date(dateStr).getDay());
		// noUncheckedIndexedAccess: weekdayLabel returns "" for an
		// out-of-range index — fall back to the original input string so
		// we never lie about the return type.
		return label || dateStr;
	} catch {
		return dateStr;
	}
}

/** Get the localized name for a weekday index (0=Sunday…6=Saturday). */
export function weekdayLabel(index: number): string {
	const days = [
		t("analytics.days.sun"),
		t("analytics.days.mon"),
		t("analytics.days.tue"),
		t("analytics.days.wed"),
		t("analytics.days.thu"),
		t("analytics.days.fri"),
		t("analytics.days.sat"),
	];
	return days[index] ?? "";
}
