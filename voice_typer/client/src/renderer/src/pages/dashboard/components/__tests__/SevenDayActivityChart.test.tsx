/**
 * SevenDayActivityChart — the Analytics page's Activity card.
 *
 * The chart is presentational: it receives bars that are already
 * bucketed (see `../../lib/streaks`, covered by `lib/__tests__/
 * streaks.test.ts`) and owns the bar RENDER contract, which is what
 * this file pins:
 *
 *   1. A slot with dictations draws a bar, filled at full strength.
 *   2. A covered slot with NO dictations draws nothing at all, so a
 *      quiet day reads as an empty column rather than as a bar of
 *      height zero — and stays inert on hover, because it has no count
 *      to report.
 *   3. A slot the sample does not cover keeps its dashed tick, because
 *      "outside the sample" is a different claim from "nothing
 *      happened".
 *   4. The header is text only — no icon.
 *   5. The header is ONE line: title leading, range/unit trailing on
 *      the same baseline (same shape as the heatmap card's header).
 *   6. No count is printed on the chart. The count lives in a tooltip
 *      that follows the cursor, opens on whichever side of it has room,
 *      and carries the count ALONE — the slot's label is already
 *      rendered on the x axis directly below the bar. An empty slot
 *      reads "0 dictations"; the tooltip never changes shape.
 *
 * Slots and marks are located by `data-slot`, so these assertions stay
 * independent of the classes the fill happens to use.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
} from "@/__tests__/helpers/stableMocks";

vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());

import { t, tChoice } from "@/i18n/i18n";
import type { ActivityBar, ActivityChartData } from "../../lib/streaks";
import { ActivityChart } from "../SevenDayActivityChart";

function chart(bars: ActivityBar[]): ActivityChartData {
	return { bars, kind: "daily", coveredFromKey: null, daySpan: bars.length };
}

/** A span-wide daily chart whose bars are labelled `d0`…`d<n-1>`. */
function wideChart(count: number): ActivityChartData {
	return chart(
		Array.from({ length: count }, (_, i) => ({
			key: `k${i}`,
			label: `d${i}`,
			count: 0,
			isMissing: false,
		})),
	);
}

/** A 24-bar hourly chart, labelled `0`…`23`. */
function hourlyChart(): ActivityChartData {
	return {
		bars: Array.from({ length: 24 }, (_, h) => ({
			key: `h${h}`,
			label: String(h),
			count: 0,
			isMissing: false,
		})),
		kind: "hourly",
		coveredFromKey: null,
		daySpan: 1,
	};
}

/** The x-axis ticks, in bar order (every bar renders one, most hidden). */
function tickEls(): HTMLElement[] {
	return Array.from(
		document.querySelectorAll<HTMLElement>('[data-slot="activity-tick"]'),
	);
}

/** Indices whose x tick is visible. */
function visibleTicks(): number[] {
	return tickEls()
		.map((el, i) => (el.className.includes("invisible") ? -1 : i))
		.filter((i) => i >= 0);
}

/** The visible ticks' text, in bar order. */
function visibleTickText(): string[] {
	return tickEls()
		.filter((el) => !el.className.includes("invisible"))
		.map((el) => el.textContent ?? "");
}

// Mon has dictations, Tue is a covered day with none, Wed predates the
// history sample.
const ACTIVITY = chart([
	{ key: "mon", label: "Mon", count: 3, isMissing: false },
	{ key: "tue", label: "Tue", count: 0, isMissing: false },
	{ key: "wed", label: "Wed", count: 0, isMissing: true },
]);

const MON_TEXT = tChoice("analytics.heatmap.tooltip", 3);
// Zero dictations — the same wording whether the slot is a quiet day or
// one the sample does not cover.
const ZERO_TEXT = tChoice("analytics.heatmap.tooltip", 0);

function renderChart() {
	return render(<ActivityChart range="7d" activity={ACTIVITY} />);
}

/** The three day slots, in bar order (a slot may draw no mark). */
function slots(container: HTMLElement): HTMLElement[] {
	return Array.from(
		container.querySelectorAll<HTMLElement>('[data-slot="activity-bar-slot"]'),
	);
}

/** A slot — the hover target (the mark inside it is only a few px wide). */
function slot(container: HTMLElement, index: number): HTMLElement {
	const found = slots(container)[index];
	if (!found) throw new Error(`no slot at index ${index}`);
	return found;
}

/** The mark inside a slot, or null when the slot drew nothing. */
function mark(container: HTMLElement, index: number): HTMLElement | null {
	return (
		slot(container, index).querySelector<HTMLElement>(
			'[data-slot="activity-bar"]',
		) ?? null
	);
}

/**
 * Give the plot a real box — jsdom reports 0×0 for every element, so
 * without this the right-vs-left decision would be settled by a
 * zero-width container instead of by where the cursor is.
 */
function boxThePlot(container: HTMLElement, width = 400, height = 144) {
	const plot = container.querySelector<HTMLElement>(
		'[data-slot="activity-plot"]',
	);
	if (!plot) throw new Error("no plot");
	plot.getBoundingClientRect = () =>
		({
			x: 0,
			y: 0,
			left: 0,
			top: 0,
			right: width,
			bottom: height,
			width,
			height,
			toJSON: () => ({}),
		}) as DOMRect;
	return plot;
}

/** The positioned wrapper of the tooltip showing `text`. */
function tooltipBox(text: string): HTMLElement {
	const wrapper = screen.getByText(text).parentElement?.parentElement;
	if (!wrapper) throw new Error("tooltip wrapper not found");
	return wrapper;
}

/** The tooltip's resolved x, in plot-relative px. */
function tooltipLeft(text: string): number {
	return Number.parseFloat(tooltipBox(text).style.left || "0");
}

afterEach(cleanup);

describe("ActivityChart bar rendering", () => {
	it("marks a day with dictations and leaves a quiet day unmarked", () => {
		// Only Mon (3 dictations) and Wed (no data) have anything to
		// say; Tue renders no mark at all.
		const { container } = renderChart();
		expect(slots(container)).toHaveLength(3);
		expect(mark(container, 0)).toBeTruthy();
		expect(mark(container, 1)).toBeNull();
		expect(mark(container, 2)).toBeTruthy();
	});

	it("fills the bar at full opacity, with no hover state of its own", () => {
		// The bar is a read-out, not a control: the hover affordance
		// belongs to the slot that contains it.
		const bar = mark(renderChart().container, 0);
		expect(bar?.className).toContain("bg-accent");
		expect(bar?.className).not.toMatch(/bg-accent\//);
		expect(bar?.className).not.toMatch(/hover:/);
	});

	it("keeps the dashed tick only for slots outside the sample", () => {
		const { container } = renderChart();
		expect(mark(container, 0)?.className).not.toContain("border-dashed");
		expect(mark(container, 2)?.className).toContain("border-dashed");
	});

	it("renders no icon in the card header", () => {
		expect(
			renderChart().container.querySelector('[data-testid="hugeicon"]'),
		).toBeNull();
	});

	it("puts the range/unit line on the title's row, trailing it", () => {
		// Title and window share one baseline row; a stacked column
		// would make the card a row taller for no added information.
		const { container } = renderChart();
		const title = container.querySelector("h2");
		const header = title?.parentElement;
		expect(header?.className).not.toContain("flex-col");
		expect(header?.className).toContain("items-baseline");
		expect(header?.className).toContain("justify-between");
		const sub = header?.querySelector("p");
		expect(sub?.textContent).toBe(
			`${t("analytics.range.7d")} · ${t("analytics.byDay")}`,
		);
		expect(sub?.className).toContain("shrink-0");
	});
});

describe("ActivityChart x ticks", () => {
	it("labels every bar when the span is a week or less", () => {
		render(<ActivityChart range="7d" activity={wideChart(7)} />);
		expect(visibleTicks()).toEqual([0, 1, 2, 3, 4, 5, 6]);
		expect(visibleTickText()).toEqual([
			"d0",
			"d1",
			"d2",
			"d3",
			"d4",
			"d5",
			"d6",
		]);
	});

	it("always labels the newest bar on a wide span", () => {
		// The newest bar is the one a reader looks for first. It used to
		// be the ONLY bar left blank: a fixed "every 5th" rule put the
		// last tick on index 25 of 30.
		render(<ActivityChart range="30d" activity={wideChart(30)} />);
		const ticks = visibleTicks();
		expect(ticks[ticks.length - 1]).toBe(29);
		expect(ticks).toEqual([0, 6, 12, 17, 23, 29]);
	});

	it("never lands two wide-span labels on adjacent bars", () => {
		// The labels are dates ("Sep 10"), wide enough to overlap if two
		// ticks ever ended up side by side. Rounding the fractional
		// positions keeps every gap at 2 columns or more, at any span.
		for (const count of [8, 9, 10, 11, 13, 14, 22, 26, 28, 30, 31, 40]) {
			cleanup();
			render(<ActivityChart range="30d" activity={wideChart(count)} />);
			const ticks = visibleTicks();
			const gaps = ticks.slice(1).map((tick, i) => tick - (ticks[i] ?? 0));
			expect(
				Math.min(...gaps),
				`a span of ${count} bars`,
			).toBeGreaterThanOrEqual(2);
			expect(ticks[ticks.length - 1], `a span of ${count} bars`).toBe(
				count - 1,
			);
		}
	});

	it("labels the newest hour on the Today view", () => {
		render(<ActivityChart range="today" activity={hourlyChart()} />);
		// Whole-hour steps, plus hour 23 — the bar the axis used to stop
		// three hours short of.
		expect(visibleTickText()).toEqual([
			"0",
			"3",
			"6",
			"9",
			"12",
			"15",
			"18",
			"21",
			"23",
		]);
	});
});

describe("ActivityChart x-axis row height (C-LIFE-2)", () => {
	/** Tick row element (the reservation under test). */
	function tickRow(): HTMLElement | null {
		return document.querySelector<HTMLElement>('[data-slot="activity-ticks"]');
	}

	it("reserves the same two-line row for hourly and month-day labels", () => {
		// Hourly ticks ("23") take one line; month-day ticks ("Sep 11")
		// wrap to two in narrow columns. The row is fixed-height so the
		// card never grows when switching ranges.
		const monthDay = chart(
			Array.from({ length: 30 }, (_, i) => ({
				key: `k${i}`,
				label: `Sep ${i + 1}`,
				count: 0,
				isMissing: false,
			})),
		);
		render(<ActivityChart range="today" activity={hourlyChart()} />);
		expect(tickRow()?.className).toContain("h-8");
		cleanup();
		render(<ActivityChart range="30d" activity={monthDay} />);
		expect(tickRow()?.className).toContain("h-8");
		// Readability kept: the month-day text still reaches the reader
		// (index 12 of 30 ticks "Sep 13", see the spacing rule above).
		expect(visibleTickText()).toContain("Sep 13");
	});
});

describe("ActivityChart count tooltip", () => {
	it("prints no count on the chart until a slot is hovered", () => {
		// The count used to sit permanently above every bar, which made
		// the card read as a table of numbers with bars drawn behind it.
		// The slots now hold a mark and nothing else.
		const { container } = renderChart();
		for (const s of slots(container)) {
			expect(s.textContent).toBe("");
		}
		expect(screen.queryByText(MON_TEXT)).toBeNull();
		expect(screen.queryByText(ZERO_TEXT)).toBeNull();
	});

	it("shows the hovered slot's count near the cursor", () => {
		const { container } = renderChart();
		boxThePlot(container);
		fireEvent.pointerMove(slot(container, 0), { clientX: 40, clientY: 30 });
		expect(screen.getByText(MON_TEXT)).toBeTruthy();
		// The tooltip belongs to the hovered slot only.
		expect(screen.queryByText(ZERO_TEXT)).toBeNull();
	});

	it("reports zero dictations for a slot outside the sample", () => {
		// The dashed tick says "outside the sample" visually; the tooltip
		// still has ONE shape — the count — so an empty slot reads
		// "0 dictations" rather than switching to a different sentence.
		const { container } = renderChart();
		boxThePlot(container);
		fireEvent.pointerMove(slot(container, 2), { clientX: 300, clientY: 30 });
		expect(screen.getByText(ZERO_TEXT)).toBeTruthy();
	});

	it("carries the count alone — the slot's label is already on the x axis", () => {
		// "Wed" is rendered under the bar by the x axis, so the tooltip
		// repeating it ("Wed: 3 dictations") said the same thing twice.
		const { container } = renderChart();
		boxThePlot(container);
		fireEvent.pointerMove(slot(container, 0), { clientX: 40, clientY: 30 });
		const text = screen.getByText(MON_TEXT).textContent ?? "";
		expect(text).toContain("3");
		expect(text).not.toContain("Mon");
	});

	it("gives the panel the same 8% border the card and heatmap tooltip use", () => {
		const { container } = renderChart();
		boxThePlot(container);
		fireEvent.pointerMove(slot(container, 0), { clientX: 40, clientY: 30 });
		// The panel is the text's direct parent; TooltipBox's wrapper
		// above it is transparent, so the border has to be on the panel.
		const panel = screen.getByText(MON_TEXT).parentElement;
		expect(panel?.className).toContain("border-border/8");
	});

	it("clears on a quiet day rather than leaving the neighbour's count up", () => {
		// Tue is a covered day that draws no mark at all. Sliding onto it
		// must not leave Mon's "3 dictations" on screen under Tue's
		// column — and Tue gets no tooltip of its own, because it has no
		// mark to hover.
		const { container } = renderChart();
		boxThePlot(container);
		fireEvent.pointerMove(slot(container, 0), { clientX: 40, clientY: 30 });
		expect(screen.getByText(MON_TEXT)).toBeTruthy();
		fireEvent.pointerOver(slot(container, 1), { clientX: 150, clientY: 30 });
		expect(screen.queryByText(MON_TEXT)).toBeNull();
		expect(screen.queryByText(ZERO_TEXT)).toBeNull();
	});

	it("opens to the right of the cursor when the plot has room there", () => {
		const { container } = renderChart();
		boxThePlot(container);
		fireEvent.pointerMove(slot(container, 0), { clientX: 40, clientY: 30 });
		expect(tooltipLeft(MON_TEXT)).toBeGreaterThan(40);
	});

	it("flips to the left of the cursor when the right side runs out of room", () => {
		const { container } = renderChart();
		boxThePlot(container);
		// 380 of a 400px plot: the panel plus its 16px offset cannot fit
		// to the right, so it opens leftwards instead of overflowing.
		fireEvent.pointerMove(slot(container, 2), { clientX: 380, clientY: 30 });
		expect(tooltipLeft(ZERO_TEXT)).toBeLessThan(380);
	});

	it("hides when the pointer leaves the plot", () => {
		const { container } = renderChart();
		const plot = boxThePlot(container);
		fireEvent.pointerMove(slot(container, 0), { clientX: 40, clientY: 30 });
		expect(screen.getByText(MON_TEXT)).toBeTruthy();
		fireEvent.pointerOut(plot, { relatedTarget: document.body });
		expect(screen.queryByText(MON_TEXT)).toBeNull();
	});
});
