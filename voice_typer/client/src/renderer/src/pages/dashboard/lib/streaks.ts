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
 * `get_config` / `get_today_stats` / `get_history` / `get_history_count`
 * IPCs.
 *
 * Kept in this module (rather than `lib/format.ts`) because
 * `computeDailyActivity`'s return shape is the `dailyActivity` field —
 * co-locating the type with the producer keeps the contract obvious.
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
	dailyActivity: {
		date: string;
		count: number;
		label: string;
		dayName: string;
	}[];
	currentStreak: number;
	maxStreak: number;
	activeDays: number;
}

// ── Local-date key helpers ───────────────────────────────────────────

/** Format a Date as a YYYY-MM-DD string in LOCAL time (not UTC).
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

/** Parse a timestamp string to a YYYY-MM-DD date key (in LOCAL time). */
export function dateKey(ts: string): string {
	try {
		return localDateKey(new Date(ts));
	} catch {
		return ts;
	}
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
		const d = new Date(now);
		d.setDate(d.getDate() - i);
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
	const yesterday = localDateKey(new Date(Date.now() - 86400000));

	// Current streak (must include today or yesterday)
	let current = 0;
	if (sorted[0] === today || sorted[0] === yesterday) {
		for (let i = 0; i < sorted.length; i++) {
			const expected = localDateKey(new Date(Date.now() - i * 86400000));
			if (sorted[i] === expected) current++;
			else break;
		}
	}

	// Max streak (scan all)
	let max = 1;
	let run = 1;
	for (let i = 1; i < sorted.length; i++) {
		const prev = new Date(sorted[i - 1]);
		const curr = new Date(sorted[i]);
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
