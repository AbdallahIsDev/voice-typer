import { describe, expect, it } from "vitest";

import { computeShareStats } from "./useStatsShare";

describe("computeShareStats", () => {
	it("computes cloud dictation speed and saved minutes", () => {
		const stats = computeShareStats(
			{ count: 3, chars: 600, word_count: 120, duration: 60 },
			"openai",
		);

		expect(stats).toEqual({
			wpm: 120,
			wpmDisplay: "120",
			minutesSaved: 2,
			minutesSavedDisplay: "2",
			modeDisplay: "Cloud",
			modeDetail: "Cloud API",
			fasterThanAvg: "200% faster than avg typer",
		});
	});

	it("handles zero-duration offline stats without division artifacts", () => {
		const stats = computeShareStats(
			{ count: 0, chars: 0, word_count: 0, duration: 0 },
			"parakeet",
		);

		expect(stats.wpm).toBe(0);
		expect(stats.minutesSaved).toBe(0);
		expect(stats.modeDisplay).toBe("Offline");
		expect(stats.modeDetail).toBe("Local Model");
		expect(stats.fasterThanAvg).toBe("0% faster than avg typer");
	});
});
