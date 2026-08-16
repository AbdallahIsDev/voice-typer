import { describe, expect, it } from "vitest";

import { computeShareStats } from "./useStatsShare";

describe("computeShareStats", () => {
	it("computes cloud dictation speed and saved minutes", () => {
		const stats = computeShareStats(
			{ count: 3, chars: 600, word_count: 120, duration: 60 },
			"openai",
			{
				totalCount: 40,
				totalChars: 8_000,
				totalDuration: 3_600,
				activeDays: 12,
				currentStreak: 4,
				model: "gpt-4o-transcribe",
				device: "cpu",
			},
		);

		expect(stats).toEqual({
			wpm: 120,
			wpmDisplay: "120",
			minutesSaved: 2,
			minutesSavedDisplay: "2",
			modeDisplay: "Cloud",
			modeDetail: "Cloud API",
			fasterThanAvg: "200% faster than avg typer",
			hasTodayActivity: true,
			dictations: "40",
			activeDays: "12",
			activeDaysDetail: "4-day streak",
			chars: "8K",
			recordingTime: "1h",
			model: "gpt-4o-transcribe",
			device: "cpu",
		});
	});

	it("handles zero-duration offline stats without division artifacts", () => {
		const stats = computeShareStats(
			{ count: 0, chars: 0, word_count: 0, duration: 0 },
			"parakeet",
		);

		expect(stats.wpm).toBe(0);
		// No dictation today → the image must not claim "0 WPM" as a
		// real stat: the display shows "—" and no faster-than-avg line.
		expect(stats.wpmDisplay).toBe("—");
		expect(stats.fasterThanAvg).toBeNull();
		expect(stats.hasTodayActivity).toBe(false);
		expect(stats.minutesSaved).toBe(0);
		expect(stats.modeDisplay).toBe("Offline");
		expect(stats.modeDetail).toBe("Local Model");
	});

	it("defaults lifetime fields to today's values when extras are omitted", () => {
		const stats = computeShareStats(
			{ count: 5, chars: 250, word_count: 50, duration: 120 },
			"local",
		);

		expect(stats.dictations).toBe("5");
		expect(stats.chars).toBe("250");
		expect(stats.recordingTime).toBe("2m");
		expect(stats.model).toBe("");
		expect(stats.device).toBe("");
		// No extras → no streak line.
		expect(stats.activeDaysDetail).toBeNull();
	});

	it("suppresses the streak line when there is no current streak", () => {
		const stats = computeShareStats(
			{ count: 2, chars: 100, word_count: 20, duration: 60 },
			"groq",
			{ currentStreak: 0, activeDays: 3 },
		);

		expect(stats.activeDays).toBe("3");
		expect(stats.activeDaysDetail).toBeNull();
	});
});
