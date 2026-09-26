import { describe, expect, it, vi } from "vitest";

vi.mock("@/i18n/i18n", () => ({
	getLocale: () => "en",
	t: (key: string) => key,
	tChoice: (key: string, count: number) => `${key}:${count}`,
}));

import {
	formatDiagnosticRelativeTime,
	formatLastUpdatedLabel,
	RELATIVE_TIME_WEEK_CUTOFF_DAYS,
} from "@/lib/relativeTime";

const NOW = new Date("2026-09-01T12:00:00Z").getTime();
const isoAgo = (ms: number) => new Date(NOW - ms).toISOString();

describe("formatDiagnosticRelativeTime", () => {
	it("keeps the neverRun sentinel for null", () => {
		expect(formatDiagnosticRelativeTime(null, NOW)).toBe("about.neverRun");
	});

	it("renders lessThanMinute under one minute", () => {
		expect(formatDiagnosticRelativeTime(isoAgo(30_000), NOW)).toBe(
			"about.relativeTime.lessThanMinute",
		);
	});

	it("buckets minutes / hours / days through the same keys", () => {
		expect(formatDiagnosticRelativeTime(isoAgo(5 * 60_000), NOW)).toBe(
			"about.relativeTime.minutesAgo:5",
		);
		expect(formatDiagnosticRelativeTime(isoAgo(3 * 3_600_000), NOW)).toBe(
			"about.relativeTime.hoursAgo:3",
		);
		expect(formatDiagnosticRelativeTime(isoAgo(3 * 86_400_000), NOW)).toBe(
			"about.relativeTime.daysAgo:3",
		);
	});

	it("falls back to a medium date at the 7-day cutoff", () => {
		expect(RELATIVE_TIME_WEEK_CUTOFF_DAYS).toBe(7);
		const result = formatDiagnosticRelativeTime(isoAgo(10 * 86_400_000), NOW);
		expect(result).not.toContain("daysAgo");
		expect(result).toMatch(/\d{4}/);
	});

	it("returns the raw string for unparseable input", () => {
		expect(formatDiagnosticRelativeTime("not-a-date", NOW)).toBe("not-a-date");
	});
});

describe("formatLastUpdatedLabel", () => {
	it("keeps the never sentinel for null", () => {
		expect(formatLastUpdatedLabel(null, NOW)).toBe("common.lastUpdatedNever");
	});

	it("renders Just now under 5s, then seconds / minutes / hours", () => {
		expect(formatLastUpdatedLabel(NOW - 2_000, NOW)).toBe(
			"common.lastUpdatedJustNow",
		);
		expect(formatLastUpdatedLabel(NOW - 30_000, NOW)).toBe(
			"common.lastUpdatedSecondsAgo:30",
		);
		expect(formatLastUpdatedLabel(NOW - 5 * 60_000, NOW)).toBe(
			"common.lastUpdatedMinutesAgo:5",
		);
		expect(formatLastUpdatedLabel(NOW - 3 * 3_600_000, NOW)).toBe(
			"common.lastUpdatedHoursAgo:3",
		);
	});
});
