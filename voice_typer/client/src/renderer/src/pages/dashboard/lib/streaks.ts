//pure dashboard helpers extracted from `pages/Dashboard.tsx`.
//
// These functions are React-agnostic and have no side effects beyond
// reading their arguments, so they can be unit-tested in isolation and
// re-used by the share-image capture path. They were previously inlined
// at module scope in `Dashboard.tsx` (lines ~80-235 of the pre-split
// file); behaviour is unchanged.
//
// Cross-module dependency note:
//   - `computeDailyActivity` calls `dayAbbr` / `dayLabel` from `./format`,
//     and `./format`'s `dayLabel` calls back into `localDateKey` here.
//     The cycle is safe because both directions are only invoked at
//     call-time (never at module-init), so ES module live-binding
//     resolution works.

import type { HistoryRecord } from "@/types/ipc";

import { dayAbbr, dayLabel } from "./format";

// ── Types ────────────────────────────────────────────────────────────

/**
 * Aggregate dashboard metrics derived from the backend's
 * `get_config` / `get_history` / `get_history_count` IPCs.
 *
 * Kept in this module (rather than `lib/format.ts`) because the
 * period/activity helpers' return shapes are the `period` /
 * `activity` fields — co-locating the type with the producer keeps the
 * contract obvious.
 *
 * NOTE: every metric here derives from ONE history sample (the last
 * 500 dictations) so the cards, chart, and streaks can never disagree
 * with each other. The only exception is `totalCount`, which comes
 * from the dedicated `get_history_count` IPC (the true all-time row
 * count). When `totalCount > sampleSize` the char/duration totals are
 * sampled, not complete — the page surfaces that with a footnote.
 */
export interface DashboardData {
	todayCount: number;
	todayChars: number;
	todayWordCount: number;
	todayDuration: number;
	totalCount: number;
	totalChars: number;
	totalDuration: number;
	favoritesCount: number;
	model: string;
	device: string;
	language: string;
	currentStreak: number;
	maxStreak: number;
	activeDays: number;
	/** Size of the history sample the derived stats are computed from. */
	sampleSize: number;
}

// ── Local-date key helpers ───────────────────────────────────────────

/**
 * Format a Date as a YYYY-MM-DD string in LOCAL time (not UTC).
 *
 * : the previous implementation used
 * ``new Date(ts).toISOString().slice(0, 10)`` which formats the date in
 * UTC. For users in negative UTC offsets (the Americas, -05:00 to
 * -10:00), a transcription logged at 8pm local on Tuesday was bucketed
 * into Wednesday's UTC date — so the dashboard's "Today" total stayed
 * at zero until the next local day, and the 7-day activity chart
 * showed entries on the wrong bars. Switching to local-date keys keeps
 * the bucket aligned with the user's calendar day.
 */
export function localDateKey(d: Date): string {
	const y = d.getFullYear();
	const m = String(d.getMonth() + 1).padStart(2, "0");
	const day = String(d.getDate()).padStart(2, "0");
	return `${y}-${m}-${day}`;
}

/**
 * Parse a DB timestamp string into a Date, treating it as UTC.
 *
 * SQLite stores ``timestamp`` as UTC ``"YYYY-MM-DD HH:MM:SS"`` with NO
 * timezone marker. JS parses unmarked date-time strings as LOCAL time,
 * which shifts calendar-day bucketing by the user's UTC offset: a
 * dictation at 00:30 local (stored as yesterday 21:30 UTC) was parsed
 * as yesterday 21:30 LOCAL and bucketed into the WRONG day. This made
 * the chart's today-bar and the streak anchor disagree with the
 * server's (correct, local-midnight) ``get_today_stats`` — the
 * "Dictations Today: 0 but Active Days: 7" inconsistency. Appending
 * the Z marker makes the instant correct; `localDateKey` then converts
 * to the user's calendar day.
 */
export function parseUtcTimestamp(ts: string): Date {
	const s = ts.trim();
	if (!s) return new Date(NaN);
	// Already carries a zone marker, or is a bare date (local-midnight
	// semantics are correct for pure date keys) — parse as-is.
	if (
		/[zZ]$/.test(s) ||
		/[+-]\d{2}:?\d{2}$/.test(s) ||
		/^\d{4}-\d{2}-\d{2}$/.test(s)
	) {
		return new Date(s);
	}
	// "YYYY-MM-DD HH:MM:SS" (or ISO with a space) → ISO + Z (UTC).
	return new Date(`${s.replace(" ", "T")}Z`);
}

/** Parse a timestamp string to a YYYY-MM-DD date key (in LOCAL time). */
export function dateKey(ts: string): string {
	try {
		return localDateKey(parseUtcTimestamp(ts));
	} catch {
		return ts;
	}
}

/** Date `now` shifted by `days` days (local calendar arithmetic). */
function addDays(now: Date, days: number): Date {
	const d = new Date(now);
	d.setDate(d.getDate() + days);
	return d;
}

// ── Daily-activity / streak computations ─────────────────────────────

/** Build the 7-day activity array from a list of history records. */
export function computeDailyActivity(
	records: HistoryRecord[],
): { date: string; count: number; label: string; dayName: string }[] {
	const counts = new Map<string, number>();
	for (const r of records) {
		const key = dateKey(r.timestamp);
		counts.set(key, (counts.get(key) ?? 0) + 1);
	}
	const result: {
		date: string;
		count: number;
		label: string;
		dayName: string;
	}[] = [];
	const now = new Date();
	for (let i = 6; i >= 0; i--) {
		const d = addDays(now, -i);
		//use localDateKey (not toISOString().slice) so the
		// 7-day chart buckets honor the user's local calendar day.
		const key = localDateKey(d);
		result.push({
			date: key,
			count: counts.get(key) ?? 0,
			label: dayLabel(key),
			dayName: dayAbbr(key),
		});
	}
	return result;
}

/** Compute consecutive-day streak from history records. */
export function computeStreaks(records: HistoryRecord[]): {
	current: number;
	max: number;
	activeDays: number;
} {
	const days = new Set<string>();
	for (const r of records) {
		days.add(dateKey(r.timestamp));
	}
	const sorted = Array.from(days).sort().reverse();
	if (sorted.length === 0) return { current: 0, max: 0, activeDays: 0 };

	//use localDateKey (not toISOString().slice) so streak
	// calculations anchor on the user's local calendar day.
	const today = localDateKey(new Date());
	const yesterday = localDateKey(addDays(new Date(), -1));

	// Current streak (must include today or yesterday)
	let current = 0;
	if (sorted[0] === today || sorted[0] === yesterday) {
		for (let i = 0; i < sorted.length; i++) {
			const expected = localDateKey(addDays(new Date(), -i));
			if (sorted[i] === expected) current++;
			else break;
		}
	}

	// Max streak (scan all)
	let max = 1;
	let run = 1;
	for (let i = 1; i < sorted.length; i++) {
		// noUncheckedIndexedAccess: `sorted[i]` is `string | undefined`;
		// skip undefined entries — the diff computation is meaningless
		// for missing data and the rest of the loop would yield NaN.
		const prevStr = sorted[i - 1];
		const currStr = sorted[i];
		if (prevStr === undefined || currStr === undefined) continue;
		const prev = new Date(prevStr);
		const curr = new Date(currStr);
		const diffMs = prev.getTime() - curr.getTime();
		if (diffMs <= 86400000 * 1.5) {
			run++;
			if (run > max) max = run;
		} else {
			run = 1;
		}
	}
	if (sorted.length === 1) max = 1;

	return { current, max, activeDays: sorted.length };
}

// ── Time-range period computation (single source of truth) ───────────

/** Selectable analytics time ranges. */
export type RangeId = "today" | "7d" | "30d" | "all";

/** Number of calendar days a range covers (null = unbounded / all-time). */
export function rangeDaySpan(range: RangeId): number | null {
	switch (range) {
		case "today":
			return 1;
		case "7d":
			return 7;
		case "30d":
			return 30;
		case "all":
			return null;
	}
}

/**
 * Aggregated stats for one time range + the PREVIOUS window of the
 * same length (for trend indicators). Every number derives from the
 * same history sample, so the cards can never contradict the chart.
 */
export interface PeriodStats {
	range: RangeId;
	count: number;
	chars: number;
	wordCount: number;
	duration: number;
	/** Distinct calendar days with ≥1 dictation inside the window. */
	activeDays: number;
	/** chars / count (0 when the window has no dictations). */
	avgCharsPerDictation: number;
	/** Longest single dictation duration in the window (seconds). */
	longestSession: number;
	/** Weekday index (0=Sunday…6=Saturday) with the most dictations, or null when empty. */
	peakWeekday: number | null;
	/** Same-length previous window, or null for "all" (no prior period). */
	prev: { count: number; chars: number; duration: number } | null;
}

interface WindowAgg {
	records: HistoryRecord[];
	count: number;
	chars: number;
	wordCount: number;
	duration: number;
	days: Set<string>;
	peakWeekday: number | null;
	longestSession: number;
}

function aggregate(records: HistoryRecord[]): WindowAgg {
	let chars = 0;
	let wordCount = 0;
	let duration = 0;
	let longestSession = 0;
	const days = new Set<string>();
	const weekdayCounts = new Map<number, number>();
	for (const r of records) {
		const d = parseUtcTimestamp(r.timestamp);
		chars += r.char_count ?? 0;
		wordCount += r.word_count ?? 0;
		duration += r.duration ?? 0;
		longestSession = Math.max(longestSession, r.duration ?? 0);
		days.add(dateKey(r.timestamp));
		if (!Number.isNaN(d.getTime())) {
			weekdayCounts.set(d.getDay(), (weekdayCounts.get(d.getDay()) ?? 0) + 1);
		}
	}
	let peakWeekday: number | null = null;
	let peakCount = 0;
	for (const [day, n] of weekdayCounts) {
		if (n > peakCount) {
			peakCount = n;
			peakWeekday = day;
		}
	}
	return {
		records,
		count: records.length,
		chars,
		wordCount,
		duration,
		days,
		peakWeekday,
		longestSession,
	};
}

/**
 * Compute the stats for `range` (ending today) plus the previous
 * same-length window, from one history sample.
 */
export function computePeriodStats(
	records: HistoryRecord[],
	range: RangeId,
	now: Date = new Date(),
): PeriodStats {
	const todayKey = localDateKey(now);
	const span = rangeDaySpan(range);
	const windowStart =
		span === null ? "0000-00-00" : localDateKey(addDays(now, -(span - 1)));
	const prevWindowStart =
		span === null ? null : localDateKey(addDays(now, -(span * 2 - 1)));
	const prevWindowEnd =
		span === null ? null : localDateKey(addDays(now, -span));

	const inWindow = records.filter((r) => {
		const k = dateKey(r.timestamp);
		return k >= windowStart && k <= todayKey;
	});
	const prevRecords =
		prevWindowStart !== null && prevWindowEnd !== null
			? records.filter((r) => {
					const k = dateKey(r.timestamp);
					return k >= prevWindowStart && k <= prevWindowEnd;
				})
			: [];

	const cur = aggregate(inWindow);
	const prevAgg = aggregate(prevRecords);

	return {
		range,
		count: cur.count,
		chars: cur.chars,
		wordCount: cur.wordCount,
		duration: cur.duration,
		activeDays: cur.days.size,
		avgCharsPerDictation: cur.count > 0 ? Math.round(cur.chars / cur.count) : 0,
		longestSession: cur.longestSession,
		peakWeekday: cur.peakWeekday,
		prev:
			span === null
				? null
				: {
						count: prevAgg.count,
						chars: prevAgg.chars,
						duration: prevAgg.duration,
					},
	};
}

// ── Correction usage (per-range, from the server usage snapshot) ────

/**
 * Shape of the server's ``get_correction_usage`` snapshot
 * (``voice_typer/server/correction_usage.py``).
 *
 * ``corrections_by_day`` / ``dictations_by_day`` are keyed by the
 * LOCAL calendar day (``YYYY-MM-DD``) — the same bucketing as
 * ``localDateKey`` — so the range window math here joins cleanly.
 */
export interface CorrectionUsageSnapshot {
	version?: number;
	entries?: Record<string, Record<string, { count: number; last_ts: number }>>;
	corrections_by_day?: Record<string, number>;
	dictations_by_day?: Record<string, number>;
}

/** Range-aware corrections-applied totals from the usage snapshot. */
export interface CorrectionStats {
	/** Vocabulary corrections the engine applied inside the window. */
	corrections: number;
	/** Completed dictations inside the window (the rate's denominator). */
	dictations: number;
	/** corrections ÷ dictations (0..1), or null when the window has no dictations. */
	rate: number | null;
	/** Corrections in the PREVIOUS same-length window, or null for "all". */
	prevCorrections: number | null;
}

/**
 * Sum the correction/dictation day maps over the same window the
 * period stats use, so the corrections card can never contradict the
 * dictation cards (both are computed from the same range window).
 */
export function computeCorrectionStats(
	usage: CorrectionUsageSnapshot | null,
	range: RangeId,
	now: Date = new Date(),
): CorrectionStats {
	if (!usage)
		return { corrections: 0, dictations: 0, rate: null, prevCorrections: null };

	const correctionsByDay = usage.corrections_by_day ?? {};
	const dictationsByDay = usage.dictations_by_day ?? {};
	const todayKey = localDateKey(now);
	const span = rangeDaySpan(range);
	const windowStart =
		span === null ? "0000-00-00" : localDateKey(addDays(now, -(span - 1)));
	const prevWindowStart =
		span === null ? null : localDateKey(addDays(now, -(span * 2 - 1)));
	const prevWindowEnd =
		span === null ? null : localDateKey(addDays(now, -span));

	let corrections = 0;
	let dictations = 0;
	let prevCorrections = 0;
	for (const [day, n] of Object.entries(correctionsByDay)) {
		if (day >= windowStart && day <= todayKey) corrections += n ?? 0;
		if (prevWindowStart !== null && prevWindowEnd !== null) {
			if (day >= prevWindowStart && day <= prevWindowEnd)
				prevCorrections += n ?? 0;
		}
	}
	for (const [day, n] of Object.entries(dictationsByDay)) {
		if (day >= windowStart && day <= todayKey) dictations += n ?? 0;
	}

	return {
		corrections,
		dictations,
		rate: dictations > 0 ? corrections / dictations : null,
		prevCorrections: prevWindowStart === null ? null : prevCorrections,
	};
}

// ── Chart bars (per-range, with zero-vs-missing distinction) ─────────

/** One bar in the activity chart. */
export interface ActivityBar {
	key: string;
	/** Short tick label ("Mon", "9", "12"). */
	label: string;
	count: number;
	/**
	 * True when the bar represents a slot where data CANNOT exist yet
	 * (a future hour) or a day OLDER than the oldest record in the
	 * history sample (i.e. the sample simply doesn't cover it). Visually
	 * distinct from a genuine zero-activity slot.
	 */
	isMissing: boolean;
}

export type ChartKind = "hourly" | "daily";

export interface ActivityChartData {
	bars: ActivityBar[];
	kind: ChartKind;
	/** Oldest date key covered by the sample (null when empty). */
	coveredFromKey: string | null;
	/** Number of calendar days the bars span. */
	daySpan: number;
}

/** Build the chart bars for the selected range from the history sample. */
export function buildActivityBars(
	records: HistoryRecord[],
	range: RangeId,
	now: Date = new Date(),
): ActivityChartData {
	if (range === "today") {
		return buildHourlyBars(records, now);
	}
	// "all" renders the trailing 30 days (per-day bars are unbounded
	// otherwise); the subtitle communicates the window.
	const span = rangeDaySpan(range) ?? 30;
	const todayKey = localDateKey(now);
	const startKey = localDateKey(addDays(now, -(span - 1)));

	const counts = new Map<string, number>();
	let coveredFromKey: string | null = null;
	for (const r of records) {
		const k = dateKey(r.timestamp);
		if (coveredFromKey === null || k < coveredFromKey) coveredFromKey = k;
		if (k >= startKey && k <= todayKey) {
			counts.set(k, (counts.get(k) ?? 0) + 1);
		}
	}

	const bars: ActivityBar[] = [];
	for (let i = 0; i < span; i++) {
		const key = localDateKey(addDays(now, -(span - 1 - i)));
		const missing = coveredFromKey !== null && key < coveredFromKey;
		bars.push({
			key,
			label: dayAbbr(key),
			count: counts.get(key) ?? 0,
			isMissing: missing,
		});
	}
	return { bars, kind: "daily", coveredFromKey, daySpan: span };
}

/** Per-hour bars for a single day (used by the "Today" range). */
function buildHourlyBars(
	records: HistoryRecord[],
	now: Date,
): ActivityChartData {
	const todayKey = localDateKey(now);
	const counts = new Map<number, number>();
	let coveredFromKey: string | null = null;
	for (const r of records) {
		const d = parseUtcTimestamp(r.timestamp);
		const k = dateKey(r.timestamp);
		if (coveredFromKey === null || k < coveredFromKey) coveredFromKey = k;
		if (k === todayKey && !Number.isNaN(d.getTime())) {
			counts.set(d.getHours(), (counts.get(d.getHours()) ?? 0) + 1);
		}
	}
	const currentHour = now.getHours();
	const bars: ActivityBar[] = [];
	for (let h = 0; h < 24; h++) {
		bars.push({
			key: `${todayKey}-${h}`,
			label: String(h),
			count: counts.get(h) ?? 0,
			// Future hours can't have data yet — a "no data" slot, not
			// a zero-activity one.
			isMissing: h > currentHour,
		});
	}
	return { bars, kind: "hourly", coveredFromKey, daySpan: 1 };
}
