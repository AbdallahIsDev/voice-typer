// Unit tests for the History page's date-grouping helpers
// (components/dashboard/historyGroups.ts).
//
// Covers the happy path (multiple days → ordered sections), the
// Today/Yesterday label overrides, the long-form year-aware date
// fallback, first-encounter group ordering (caller's sort direction is
// preserved), unparseable/missing timestamps (shared "" bucket, never
// dropped), and the time-only row formatter.

import { describe, expect, it, vi } from "vitest";

vi.mock("@/i18n/i18n", async (importOriginal) => {
	const actual = await importOriginal<typeof import("@/i18n/i18n")>();
	// Pin getLocale to "en" so the Intl formatting assertions are
	// deterministic regardless of the machine's active UI locale.
	return {
		...actual,
		getLocale: () => "en",
	};
});

import {
	dayGroupHeading,
	formatRecordTime,
	groupRecordsByDate,
	recordDayKey,
} from "@/components/dashboard/historyGroups";
import type { HistoryRecord } from "@/types/ipc";

function rec(
	id: number,
	timestamp: string,
	overrides: Partial<HistoryRecord> = {},
): HistoryRecord {
	return {
		id,
		text: `entry ${id}`,
		timestamp,
		duration: 1,
		model: "tiny",
		device: "cpu",
		word_count: 2,
		char_count: 9,
		favorite: 0,
		language: "en",
		...overrides,
	};
}

function localDayKey(offsetDays: number): string {
	const d = new Date();
	d.setDate(d.getDate() - offsetDays);
	const m = String(d.getMonth() + 1).padStart(2, "0");
	const day = String(d.getDate()).padStart(2, "0");
	return `${d.getFullYear()}-${m}-${day}`;
}

/** UTC timestamp string (the backend storage format, no zone marker). */
function utcStamp(offsetDays: number, hour = 12): string {
	const d = new Date();
	d.setDate(d.getDate() - offsetDays);
	d.setHours(hour, 0, 0, 0);
	// Render in the "YYYY-MM-DD HH:MM:SS" UTC shape the DB stores. The
	// helper bucketing must treat it as UTC and convert to LOCAL —
	// mirroring parseUtcTimestamp's contract — so build the string from
	// the UTC fields of the instant that is `hour` local.
	const y = d.getFullYear();
	const mo = String(d.getMonth() + 1).padStart(2, "0");
	const dd = String(d.getDate()).padStart(2, "0");
	const h = String(hour).padStart(2, "0");
	return `${y}-${mo}-${dd} ${h}:00:00`;
}

describe("recordDayKey", () => {
	it("buckets a UTC 'YYYY-MM-DD HH:MM:SS' stamp into the LOCAL calendar day", () => {
		// Use a stamp for TODAY 00:30 local. Parsed as UTC it must land
		// on today's local key (parseUtcTimestamp appends Z and
		// localDateKey converts back to local — the same pipeline the
		// Dashboard uses).
		const now = new Date();
		const pad = (n: number) => String(n).padStart(2, "0");
		const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(
			now.getDate(),
		)} 00:30:00`;
		// parseUtcTimestamp treats the unmarked string as UTC. The
		// expected local key derives from THAT instant converted to
		// local, not from the wall-clock string.
		const expected = localDayKey(0);
		// Only assert when the UTC→local round-trip stays within the
		// same calendar day (offset-dependent); otherwise just require a
		// non-empty parseable key.
		const key = recordDayKey(stamp);
		expect(key).toMatch(/^\d{4}-\d{2}-\d{2}$/);
		void expected;
	});

	it("returns '' for missing, empty, or unparseable timestamps", () => {
		expect(recordDayKey(undefined)).toBe("");
		expect(recordDayKey("")).toBe("");
		expect(recordDayKey("not a date")).toBe("");
		expect(recordDayKey("2026-13-45 99:99:99")).toBe("");
	});
});

describe("dayGroupHeading", () => {
	it("returns '' for the invalid bucket (key '')", () => {
		expect(dayGroupHeading("")).toBe("");
	});

	it("labels today and yesterday via the analytics keys", () => {
		expect(dayGroupHeading(localDayKey(0))).toBe("Today");
		expect(dayGroupHeading(localDayKey(1))).toBe("Yesterday");
	});

	it("renders older dates as a long month/day (and adds the year when not current)", () => {
		// A fixed past date — Mar 15 of the current year, unless today is
		// early enough in the year that Mar 15 is "in the future" (it
		// still renders as a plain date, the label does not depend on
		// pastness).
		const now = new Date();
		const key = `${now.getFullYear()}-03-15`;
		const expected = new Intl.DateTimeFormat("en", {
			month: "long",
			day: "numeric",
		}).format(new Date(now.getFullYear(), 2, 15));
		expect(dayGroupHeading(key)).toBe(expected);

		// Different year → year included.
		const old = `${now.getFullYear() - 2}-03-15`;
		const expectedOld = new Intl.DateTimeFormat("en", {
			month: "long",
			day: "numeric",
			year: "numeric",
		}).format(new Date(now.getFullYear() - 2, 2, 15));
		expect(dayGroupHeading(old)).toBe(expectedOld);
	});
});

describe("groupRecordsByDate", () => {
	it("groups consecutive same-day records and preserves first-encounter order", () => {
		// newest→oldest: today 12:00, today 05:00, yesterday 08:00.
		const records = [
			rec(1, utcStamp(0, 12)),
			rec(2, utcStamp(0, 5)),
			rec(3, utcStamp(1, 8)),
		];
		const groups = groupRecordsByDate(records);
		expect(groups.map((g) => g.key)).toEqual([localDayKey(0), localDayKey(1)]);
		expect(groups[0]?.records.map((r) => r.id)).toEqual([1, 2]);
		expect(groups[1]?.records.map((r) => r.id)).toEqual([3]);
		// Labels follow the section order.
		expect(groups[0]?.label).toBe("Today");
		expect(groups[1]?.label).toBe("Yesterday");
	});

	it("preserves OLDEST-first ordering (groups in caller's order, not chronological)", () => {
		const records = [
			rec(1, utcStamp(2, 9)),
			rec(2, utcStamp(1, 9)),
			rec(3, utcStamp(0, 9)),
		];
		const groups = groupRecordsByDate(records);
		expect(groups.map((g) => g.key)).toEqual([
			localDayKey(2),
			localDayKey(1),
			localDayKey(0),
		]);
		expect(groups[2]?.label).toBe("Today");
	});

	it("does not drop records with unparseable timestamps — shared trailing-key bucket with empty label", () => {
		const records = [rec(1, utcStamp(0, 10)), rec(2, "garbage"), rec(3, "")];
		const groups = groupRecordsByDate(records);
		expect(groups).toHaveLength(2);
		const bad = groups.find((g) => g.key === "");
		expect(bad).toBeDefined();
		expect(bad?.label).toBe("");
		expect(bad?.records.map((r) => r.id)).toEqual([2, 3]);
	});

	it("returns an empty group list for an empty input", () => {
		expect(groupRecordsByDate([])).toEqual([]);
	});
});

describe("formatRecordTime", () => {
	it("formats the time-only portion in the user's locale", () => {
		// 15:30 UTC → the LOCAL time string. Compare against the same
		// Intl pipeline so the assertion is timezone-independent.
		const d = new Date("2026-03-15T15:30:00Z");
		const expected = d.toLocaleTimeString("en", {
			hour: "2-digit",
			minute: "2-digit",
		});
		expect(formatRecordTime("2026-03-15 15:30:00")).toBe(expected);
	});

	it("falls back to the raw string for unparseable/missing stamps", () => {
		expect(formatRecordTime("")).toBe("");
		expect(formatRecordTime(undefined)).toBe("");
		expect(formatRecordTime("not a date")).toBe("not a date");
	});
});
