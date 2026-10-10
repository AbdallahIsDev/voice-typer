/**
 * ActivityHeatmap — the Analytics page's dictation heatmap card.
 *
 * The chart itself is a vendored Bklit component whose <svg> is
 * `aria-hidden`, and in jsdom `@visx/responsive` reports a width of 0 so
 * the grid renders nothing at all. What is worth pinning here is
 * therefore the CARD's contract, which is what this project owns:
 *
 *   1. The card is a labelled region, and the chart body is exposed to
 *      assistive tech as ONE `role="img"` whose label carries the totals
 *      — not 180 unlabelled hover targets, and not nothing at all (the
 *      `aria-hidden` svg alone would leave the card mute).
 *   2. Title / subtitle / legend labels come from the translation
 *      catalog (C-I18N-1), so the Arabic UI is not half English.
 *   3. The covered window is stated by the grid's own x-axis rather than
 *      by a subtitle, because the card ignores the range control in the
 *      title bar and the axis already spans it.
 *   4. The truncation note appears only when the grid is capped, so it
 *      cannot cry wolf on a short history.
 *   5. Rendering survives an empty history (the chart's own zero-width
 *      bail-out must not take the card down with it).
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const fs = require("node:fs");
const path = require("node:path");

import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	resetStableMocks,
} from "@/__tests__/helpers/stableMocks";

vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());

import { t } from "@/i18n/i18n";
import type { HistoryRecord } from "@/types/ipc";
import { buildDictationHeatmap } from "../../lib/heatmap";
import { ActivityHeatmap } from "../ActivityHeatmap";

const NOW = new Date(2026, 9, 6, 15, 0, 0);

function recordOn(date: Date, id: number): HistoryRecord {
	return {
		id,
		text: "",
		timestamp: new Date(
			date.getFullYear(),
			date.getMonth(),
			date.getDate(),
			12,
			0,
			0,
		)
			.toISOString()
			.slice(0, 19)
			.replace("T", " "),
		duration: 1,
		model: "",
		device: "",
		word_count: 1,
		char_count: 1,
		favorite: 0,
		language: "en",
	};
}

function renderHeatmap(records: HistoryRecord[]) {
	const heatmap = buildDictationHeatmap(records, NOW);
	return {
		heatmap,
		...render(<ActivityHeatmap heatmap={heatmap} />),
	};
}

afterEach(() => {
	cleanup();
	resetStableMocks();
});

describe("ActivityHeatmap", () => {
	it("is a labelled region whose chart body carries a totals summary", () => {
		const { heatmap } = renderHeatmap([
			recordOn(new Date(2026, 9, 6), 1),
			recordOn(new Date(2026, 9, 6), 2),
			recordOn(new Date(2026, 9, 5), 3),
		]);

		expect(
			screen.getByRole("region", { name: t("analytics.heatmap.title") }),
		).toBeTruthy();
		// Regex, not a bare string: the heading is the card's title with
		// the active-days figure in front of it, so its accessible name is
		// "7 Active Days" while the region keeps the bare title.
		expect(
			screen.getByRole("heading", {
				name: new RegExp(t("analytics.heatmap.title")),
			}),
		).toBeTruthy();

		// The summary must report the SAME numbers the cells encode.
		const summary = screen.getByRole("img");
		expect(summary.getAttribute("aria-label")).toBe(
			t("analytics.heatmap.aria", {
				total: String(heatmap.total),
				days: String(heatmap.activeDays),
			}),
		);
		expect(summary.getAttribute("aria-label")).toContain("3");
	});

	it("renders its title and legend labels from the catalog", () => {
		renderHeatmap([recordOn(new Date(2026, 9, 6), 1)]);

		expect(
			screen.getByRole("heading", {
				name: new RegExp(t("analytics.heatmap.title")),
			}),
		).toBeTruthy();
		expect(screen.getByText(t("analytics.heatmap.less"))).toBeTruthy();
		expect(screen.getByText(t("analytics.heatmap.more"))).toBeTruthy();
	});

	it("states the covered window on the axis, not as a subtitle", () => {
		// The card used to print "Oct 5, 2025 – Oct 8 · per day" under the
		// title. The x-axis already labels ~13 months across the grid, so
		// that line said it twice; nothing may put it back.
		renderHeatmap([recordOn(new Date(2026, 9, 6), 1)]);

		expect(screen.queryByText(new RegExp(t("analytics.byDay")))).toBeNull();
	});

	it("names the card by what it shows, not by the chart type", () => {
		// "Heatmap" is chart jargon; the reader is looking at their own
		// usage history. The title and the aria label are the same
		// string, so a screen reader hears the heading it can see.
		expect(t("analytics.heatmap.title")).not.toMatch(/heatmap/i);
		expect(t("analytics.heatmap.aria", { total: "0", days: "0" })).not.toMatch(
			/heatmap/i,
		);
	});

	it("renders no icon in the card header", () => {
		// The header is the figure, the card's title and the streak; the
		// grid glyph that used to open it is gone.
		const { container } = renderHeatmap([recordOn(new Date(2026, 9, 6), 1)]);
		expect(container.querySelector('[data-testid="hugeicon"]')).toBeNull();
	});

	describe("ActivityHeatmap reserved geometry (C-LIFE-2)", () => {
		it("reserves the chart box on first paint (no zero-size jump)", () => {
			// jsdom never measures (ParentSize stays 0×0), which is exactly
			// the production first frame — the reservation must already be
			// in the tree before any measure lands.
			const { container } = renderHeatmap([recordOn(new Date(2026, 9, 6), 1)]);
			const box = container.querySelector('[class*="aspect-"]');
			expect(box).not.toBeNull();
			expect(box?.className).toContain("min-h-16");
		});

		it("keeps the chart mounted across data updates (same node, no remount)", () => {
			const { container, rerender } = render(
				<ActivityHeatmap
					heatmap={buildDictationHeatmap(
						[recordOn(new Date(2026, 9, 6), 1)],
						NOW,
					)}
				/>,
			);
			const before = container.querySelector('[role="img"]');
			expect(before).not.toBeNull();
			rerender(
				<ActivityHeatmap
					heatmap={buildDictationHeatmap(
						[
							recordOn(new Date(2026, 9, 6), 1),
							recordOn(new Date(2026, 9, 5), 2),
						],
						NOW,
					)}
					currentStreak={2}
				/>,
			);
			// Same DOM node after new data: React updated it, nothing
			// reconstructed it (no key swap, no conditional branch).
			expect(container.querySelector('[role="img"]')).toBe(before);
		});
	});

	it("asks the vendored chart to skip its staggered entrance", () => {
		// jsdom renders no cells at all (see the file header), so this
		// contract can only be pinned at the source: the grid must be
		// complete on the first paint rather than fading in cell by cell.
		const src = fs.readFileSync(
			path.resolve(__dirname, "..", "ActivityHeatmap.tsx"),
			"utf8",
		);
		expect(src).toMatch(/animate=\{false\}/);
	});

	it("surfaces the active-days figure the heatmap itself counted", () => {
		// The Active Days stat card is gone; its figure is a whole-year
		// number, so it leads this card's heading instead of sitting
		// beside range-aware numbers it shares no window with.
		const { heatmap } = renderHeatmap([
			recordOn(new Date(2026, 9, 6), 1),
			recordOn(new Date(2026, 9, 5), 2),
		]);

		expect(heatmap.activeDays).toBe(2);
		// The figure keeps its own element so it stays a `tabular-nums`
		// run; the words come from the catalog (C-I18N-1).
		expect(screen.getByText(String(heatmap.activeDays))).toBeTruthy();
		expect(screen.getByText(t("analytics.heatmap.title"))).toBeTruthy();
	});

	it("adds the streak line only when a streak is passed in", () => {
		const heatmap = buildDictationHeatmap(
			[recordOn(new Date(2026, 9, 6), 1)],
			NOW,
		);
		const { rerender } = render(<ActivityHeatmap heatmap={heatmap} />);
		expect(
			screen.queryByText(t("analytics.dayStreak", { count: "5" })),
		).toBeNull();

		rerender(<ActivityHeatmap heatmap={heatmap} currentStreak={5} />);
		expect(
			screen.getByText(t("analytics.dayStreak", { count: "5" })),
		).toBeTruthy();
	});

	it("does not claim a truncated window when the history fits", () => {
		const { heatmap } = renderHeatmap([recordOn(new Date(2026, 9, 6), 1)]);

		expect(heatmap.truncated).toBe(false);
		// Regex, not a bare string: the note rides on the heading line
		// next to the figure and the title, and `getByText(string)` only
		// matches an element whose WHOLE text is that string — a bare
		// string here would match nothing whether or not the note
		// rendered, i.e. the assertion would be vacuous.
		expect(
			screen.queryByText(new RegExp(t("analytics.heatmap.truncated"))),
		).toBeNull();
	});

	it("says the window is capped when the history reaches past a year", () => {
		const { heatmap } = renderHeatmap([
			recordOn(new Date(2023, 4, 2), 1),
			recordOn(new Date(2026, 9, 6), 2),
		]);

		expect(heatmap.truncated).toBe(true);
		expect(
			screen.getByText(new RegExp(t("analytics.heatmap.truncated"))),
		).toBeTruthy();
	});

	it("survives an empty history without taking the card down", () => {
		const { heatmap } = renderHeatmap([]);

		expect(heatmap.total).toBe(0);
		expect(
			screen.getByRole("region", { name: t("analytics.heatmap.title") }),
		).toBeTruthy();
		expect(screen.getByRole("img").getAttribute("aria-label")).toBe(
			t("analytics.heatmap.aria", { total: "0", days: "0" }),
		);
	});
});
