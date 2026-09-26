import {
	differenceInDays,
	differenceInHours,
	differenceInMinutes,
	differenceInSeconds,
} from "date-fns";
import { getLocale, t, tChoice } from "@/i18n/i18n";

// Cutoff for the diagnostics relative-time fallback: timestamps older
// than this render as a localized medium-format date instead of "N d ago".
export const RELATIVE_TIME_WEEK_CUTOFF_DAYS = 7;

// Shared diagnostic relative-time formatter (diagnostics table +
// prewarm "last run" row). Bucket boundaries and i18n keys match the
// previous per-file copies exactly, so rendered output is unchanged.
export function formatDiagnosticRelativeTime(
	iso: string | null,
	nowMs: number = Date.now(),
): string {
	if (!iso) return t("about.neverRun");
	try {
		const then = new Date(iso);
		if (Number.isNaN(then.getTime())) return iso;
		const now = new Date(nowMs);
		if (differenceInMinutes(now, then) < 1)
			return t("about.relativeTime.lessThanMinute");
		const mins = differenceInMinutes(now, then);
		if (mins < 60) return tChoice("about.relativeTime.minutesAgo", mins);
		const hrs = differenceInHours(now, then);
		if (hrs < 24) return tChoice("about.relativeTime.hoursAgo", hrs);
		const days = differenceInDays(now, then);
		if (days < RELATIVE_TIME_WEEK_CUTOFF_DAYS)
			return tChoice("about.relativeTime.daysAgo", days);
		return new Intl.DateTimeFormat(getLocale(), {
			dateStyle: "medium",
		}).format(then);
	} catch {
		return iso;
	}
}

// Shared "last updated" label (useLastUpdated hook). Same buckets and
// keys as the previous inline copy: Just now < 5s, seconds, minutes,
// then unbounded hours.
export function formatLastUpdatedLabel(
	lastUpdated: number | null,
	nowMs: number,
): string {
	if (lastUpdated === null) return t("common.lastUpdatedNever");
	const seconds = Math.max(
		0,
		differenceInSeconds(new Date(nowMs), new Date(lastUpdated)),
	);
	if (seconds < 5) return t("common.lastUpdatedJustNow");
	if (seconds < 60) return tChoice("common.lastUpdatedSecondsAgo", seconds);
	const minutes = Math.floor(seconds / 60);
	if (minutes < 60) return tChoice("common.lastUpdatedMinutesAgo", minutes);
	return tChoice("common.lastUpdatedHoursAgo", Math.floor(minutes / 60));
}
