/**
 * FIX-16 / A11Y-10: i18n SVG <title> test for StatsShareImage.
 *
 * The pre-fix component hardcoded the English SVG <title> text
 * "Background waveform" inside the decorative waveform SVG. After the
 * fix the title text comes from the `stats.shareImage.backgroundWaveform`
 * i18n key, so non-English users get a localized tooltip when hovering
 * the waveform or reading it via AT.
 *
 * Note: the SVG itself remains `aria-hidden` / `role="presentation"`
 * because it is purely decorative — the <title> only surfaces as a
 * browser hover tooltip, not as an AT announcement (aria-hidden takes
 * precedence). Translating the <title> still improves the hover-tooltip
 * experience for non-English users and removes a hardcoded English
 * literal from the component.
 *
 * The test verifies:
 *   1. The SVG <title> text matches the en.json catalog value
 *      `stats.shareImage.backgroundWaveform`.
 *   2. Mocking `t()` to return a sentinel makes the title flip to the
 *      sentinel — proving the title is NOT a hardcoded literal.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vi.mock is HOISTED before imports, so the mocked `t` is in place by
// the time StatsShareImage imports it. We use importOriginal to
// preserve the rest of the i18n module and only override `t` when the
// `useSentinel` flag is set.
let useSentinel = false;
vi.mock("@/i18n/i18n", async (importOriginal) => {
	const actual = await importOriginal<typeof import("@/i18n/i18n")>();
	return {
		...actual,
		t: (key: string, params?: Record<string, string>) => {
			if (useSentinel && key === "stats.shareImage.backgroundWaveform") {
				return "SENTINEL_WAVEFORM_TITLE";
			}
			return actual.t(key, params);
		},
	};
});

import { StatsShareImage } from "@/components/dashboard/StatsShareImage";
import { t } from "@/i18n/i18n";
import type { ShareStats } from "@/types/stats";

const stats: ShareStats = {
	wpm: 92,
	wpmDisplay: "92",
	minutesSaved: 18,
	minutesSavedDisplay: "18",
	modeDisplay: "Cloud",
	modeDetail: "Cloud ASR (OpenAI)",
	fasterThanAvg: "100% faster than avg typer",
};

describe("StatsShareImage — A11Y-10 (i18n SVG title)", () => {
	afterEach(() => {
		cleanup();
		useSentinel = false;
	});

	beforeEach(() => {
		useSentinel = false;
	});

	it("renders an SVG <title> sourced from stats.shareImage.backgroundWaveform", () => {
		render(<StatsShareImage stats={stats} />);
		// en.json: stats.shareImage.backgroundWaveform = "Background waveform".
		const expected = t("stats.shareImage.backgroundWaveform");
		const titleEl = screen.getByText(expected);
		expect(titleEl).toBeInTheDocument();
		expect(titleEl.tagName.toLowerCase()).toBe("title");
	});

	it("title flips to the sentinel when t() is mocked (proves no hardcoded literal)", () => {
		useSentinel = true;
		render(<StatsShareImage stats={stats} />);
		const titleEl = screen.getByText("SENTINEL_WAVEFORM_TITLE");
		expect(titleEl).toBeInTheDocument();
		expect(titleEl.tagName.toLowerCase()).toBe("title");

		// The old hardcoded literal must NOT be present.
		expect(screen.queryByText("Background waveform")).toBeNull();
	});
});
