/**
 * Unit tests for the dashboard's derived-stat helpers (lib/streaks.ts).
 *
 * Covers the data-consistency fixes:
 *   - UTC timestamp parsing: SQLite stores `timestamp` as UTC
 *     ("YYYY-MM-DD HH:MM:SS", no zone marker). JS parses unmarked
 *     date-times as LOCAL, which shifted calendar-day bucketing by the
 *     UTC offset — that's why the 7-day chart's today-bar and the
 *     streak anchor disagreed with the server's (correct) today stats.
 *   - computePeriodStats: one sample, one bucketing → range-aware
 *     cards + trends that can never contradict the chart.
 *   - buildActivityBars: zero-activity slots vs no-data slots
 *     (future hours / days older than the sample) are distinguished.
 *
 * All records are built RELATIVE to a fixed `now` so the assertions
 * are deterministic on any machine / timezone.
 */
import { describe, expect, it } from "vitest";

import type { HistoryRecord } from "@/types/ipc";

import {
	type ActivityBar,
	buildActivityBars,
	computePeriodStats,
	computeStreaks,
	dateKey,
	localDateKey,
	parseUtcTimestamp,
} from "../streaks";

/**
 * Narrowed bar accessor. Tests assert `bars.length` first, so an
 * out-of-range index is a test bug — throw rather than risk a silent
 * `undefined` propagation (avoids non-null assertions per repo lint).
 */
function bar(bars: ActivityBar[], i: number): ActivityBar {
	const b = bars[i];
	if (b === undefined) throw new Error(`missing bar at index ${i}`);
	return b;
}

// Fixed "now" in LOCAL time — window math anchors on this machine's
// calendar day, and records are built relative to it.
const NOW = new Date(2026, 7, 16, 15, 0, 0); // Aug 16 2026, 3pm local

function rec(
	daysAgo: number,
	opts: { chars?: number; duration?: number; hour?: number } = {},
): HistoryRecord {
	const d = new Date(NOW);
	d.setDate(d.getDate() - daysAgo);
	d.setHours(opts.hour ?? 12, 0, 0, 0);
	return {
		id: Math.floor(Math.random() * 1e9),
		text: "",
		timestamp: d.toISOString(),
		duration: opts.duration ?? 10,
		model: "tiny",
		device: "cpu",
		word_count: 20,
		char_count: opts.chars ?? 100,
		favorite: 0,
		language: "en",
	};
}

describe("UTC timestamp parsing (data-consistency fix)", () => {
	it("parses a bare SQLite UTC timestamp as UTC, not local", () => {
		const fixed = parseUtcTimestamp("2026-08-16 03:00:00");
		expect(fixed.getTime()).toBe(Date.UTC(2026, 7, 16, 3, 0, 0));

		// The naive `new Date(ts)` parse interprets the string as LOCAL
		// (machine-dependent shift); the fixed parse is UTC — so unless
		// the machine is at UTC+0 the two instants differ.
		const naive = new Date("2026-08-16 03:00:00");
		expect(fixed.getTime()).not.toBe(naive.getTime());
	});

	it("buckets an evening-UTC record into the correct LOCAL calendar day", () => {
		// 22:00 UTC on the 16th. Depending on the machine's offset this
		// is the 16th or 17th LOCAL — either way it must equal the
		// UTC-correct local day, not the naive-local parse's day.
		const ts = "2026-08-16 22:00:00";
		const expected = localDateKey(parseUtcTimestamp(ts));
		expect(dateKey(ts)).toBe(expected);
		// And it differs from the naive parse when the offset crosses
		// midnight (the old bug). On UTC+0 machines this is vacuous.
		const naiveDay = localDateKey(new Date(ts));
		expect(dateKey(ts) === naiveDay).toBe(expected === naiveDay);
	});

	it("accepts ISO strings with a Z marker unchanged", () => {
		const iso = new Date(Date.UTC(2026, 7, 16, 3)).toISOString();
		expect(parseUtcTimestamp(iso).getTime()).toBe(Date.UTC(2026, 7, 16, 3));
	});

	it("streak days are bucketed in local time (evening UTC record counts for its local day)", () => {
		// A dictation at 23:30 LOCAL today stores as a bare UTC string
		// whose UTC calendar date may be TOMORROW (on negative-offset
		// machines). Parsing it as UTC (correct) must land it back on
		// today's LOCAL day; the naive local parse would put it on the
		// wrong day and break the streak anchor. The assertion holds on
		// every offset: on UTC+ machines the UTC date happens to equal
		// the local date (vacuous), on UTC- machines it exercises the bug.
		const late = new Date(NOW);
		late.setHours(23, 30, 0, 0);
		// Bare SQLite-style UTC string, no Z marker.
		const utcBare = late.toISOString().replace("T", " ").slice(0, 19);
		const records = [rec(0), { ...rec(0), timestamp: utcBare }];
		const streaks = computeStreaks(records);
		// Both records are today's LOCAL day → streak of 1, one active day.
		expect(streaks.current).toBeGreaterThan(0);
		expect(streaks.activeDays).toBe(1);
	});
});

describe("computePeriodStats", () => {
	it("counts only the window days + exposes the previous window", () => {
		const records = [
			rec(0), // today
			rec(1), // yesterday
			rec(3), // 3 days ago
			rec(6), // 6 days ago
			rec(7), // outside 7d window (prev window's last day)
			rec(10), // prev window
			rec(14), // outside both
		];
		const p = computePeriodStats(records, "7d", NOW);
		expect(p.count).toBe(4);
		expect(p.activeDays).toBe(4);
		// prev window = days 7..13 ago → rec(7) + rec(10)
		expect(p.prev?.count).toBe(2);
	});

	it("today range counts only today; prev is yesterday", () => {
		const records = [rec(0), rec(0), rec(1), rec(2)];
		const p = computePeriodStats(records, "today", NOW);
		expect(p.count).toBe(2);
		expect(p.prev?.count).toBe(1);
	});

	it("all-time has no previous period and includes everything ≤ today", () => {
		const records = [rec(0), rec(100)];
		const p = computePeriodStats(records, "all", NOW);
		expect(p.count).toBe(2);
		expect(p.prev).toBeNull();
	});

	it("derived metrics: avg chars, longest session, peak weekday", () => {
		const records = [
			rec(0, { chars: 200, duration: 60 }), // today
			rec(0, { chars: 400, duration: 120 }), // today
			rec(1, { chars: 300, duration: 30 }), // yesterday
		];
		const p = computePeriodStats(records, "7d", NOW);
		expect(p.avgCharsPerDictation).toBe(300); // 900 / 3
		expect(p.longestSession).toBe(120);
		// today has 2 dictations → peak weekday = today's weekday
		expect(p.peakWeekday).toBe(NOW.getDay());
	});

	it("excludes future-dated records from the window", () => {
		const future = new Date(NOW);
		future.setDate(future.getDate() + 2);
		const records = [{ ...rec(0), timestamp: future.toISOString() }];
		const p = computePeriodStats(records, "7d", NOW);
		expect(p.count).toBe(0);
	});
});

describe("buildActivityBars", () => {
	it("7-day range: 7 bars, zero vs missing distinguished by sample coverage", () => {
		// Records on days 0 (today), 1, and 3 ago. The sample's oldest
		// record is 3 days ago → days 4-6 ago are NOT covered (missing),
		// day 2 ago is covered-but-zero, today/yesterday/3d have counts.
		const records = [rec(0), rec(1), rec(3)];
		const { bars, kind, coveredFromKey, daySpan } = buildActivityBars(
			records,
			"7d",
			NOW,
		);
		expect(kind).toBe("daily");
		expect(daySpan).toBe(7);
		expect(bars.length).toBe(7);

		// bars are oldest → newest: index 0 = 6 days ago … index 6 = today
		expect(bar(bars, 6).count).toBe(1); // today
		expect(bar(bars, 6).isMissing).toBe(false);
		expect(bar(bars, 5).count).toBe(1); // yesterday
		expect(bar(bars, 4).count).toBe(0); // 2 days ago — zero, not missing
		expect(bar(bars, 4).isMissing).toBe(false);
		expect(bar(bars, 3).count).toBe(1); // 3 days ago
		expect(bar(bars, 3).isMissing).toBe(false);
		expect(bar(bars, 0).isMissing).toBe(true); // 6 days ago — not covered
		expect(bar(bars, 1).isMissing).toBe(true); // 5 days ago — not covered
		expect(bar(bars, 2).isMissing).toBe(true); // 4 days ago — not covered
		// Oldest record in the sample is 3 days ago → coverage starts there.
		const threeDaysAgo = new Date(NOW);
		threeDaysAgo.setDate(threeDaysAgo.getDate() - 3);
		expect(coveredFromKey).toBe(localDateKey(threeDaysAgo));
	});

	it("all-time range renders the trailing 30 days", () => {
		const records = [rec(0)];
		const { daySpan, kind } = buildActivityBars(records, "all", NOW);
		expect(kind).toBe("daily");
		expect(daySpan).toBe(30);
	});

	it("today range: 24 hourly bars, future hours are missing, past zeros are not", () => {
		// Two records today: one at local hour 9, one at local hour 12.
		const records = [rec(0, { hour: 9 }), rec(0, { hour: 12 })];
		const { bars, kind } = buildActivityBars(records, "today", NOW);
		expect(kind).toBe("hourly");
		expect(bars.length).toBe(24);
		expect(bar(bars, 9).count).toBe(1);
		expect(bar(bars, 12).count).toBe(1);
		expect(bar(bars, 12).isMissing).toBe(false);
		// Hour 14 (past, no dictation) → zero, not missing.
		expect(bar(bars, 14).count).toBe(0);
		expect(bar(bars, 14).isMissing).toBe(false);
		// NOW is 3pm local → hours 16..23 are in the future → missing.
		expect(bar(bars, 16).isMissing).toBe(true);
		expect(bar(bars, 23).isMissing).toBe(true);
		// Hour 0 (past) is zero, not missing.
		expect(bar(bars, 0).isMissing).toBe(false);
	});
});

describe("rangeDaySpan", () => {
	it("maps each range to its day count", async () => {
		const { rangeDaySpan } = await import("../streaks");
		expect(rangeDaySpan("today")).toBe(1);
		expect(rangeDaySpan("7d")).toBe(7);
		expect(rangeDaySpan("30d")).toBe(30);
		expect(rangeDaySpan("all")).toBeNull();
	});
});
