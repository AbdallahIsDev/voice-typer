/**
 * ActivityHeatmap — the GRID itself.
 *
 * The sibling `ActivityHeatmap.test.tsx` pins the card's contract (region,
 * catalog strings, totals) but cannot assert cells: in jsdom
 * `@visx/responsive`'s `ParentSize` measures its parent, gets 0, and
 * `HeatmapChart` bails out before rendering anything. Every "the grid
 * looks right" assertion written against that setup passes vacuously.
 *
 * This file supplies a fixed width instead, which is the only way to pin
 * the card's central visual promise: a COMPLETE rectangle of
 * 53 weeks × 7 days — including the empty days before the first record —
 * whose width does not depend on how much history exists.
 *
 * It is also the only place the CELL GEOMETRY can be measured: `fluid`
 * layout sizes every cell from the measured width, so the same stub that
 * makes the grid render at all is what makes its square size assertable.
 */

import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	resetStableMocks,
} from "@/__tests__/helpers/stableMocks";
import { tChoice } from "@/i18n/i18n";

vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());

/** Stand-in viewport for the measured chart parent. */
const CHART_WIDTH = 560;

// `ParentSize` renders nothing until it has a size, so without this stub the
// grid is empty and the assertions below would be vacuously true.
vi.mock("@visx/responsive", () => ({
	ParentSize: ({
		children,
	}: {
		children: (size: { width: number; height: number }) => ReactNode;
	}) => children({ width: CHART_WIDTH, height: 200 }),
}));

import type { HistoryRecord } from "@/types/ipc";
import { buildDictationHeatmap, HEATMAP_MAX_WEEKS } from "../../lib/heatmap";
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

function renderGrid(records: HistoryRecord[]) {
	const heatmap = buildDictationHeatmap(records, NOW);
	const { container } = render(<ActivityHeatmap heatmap={heatmap} />);
	return { heatmap, container };
}

/** One `<g>` per cell, inside the cells layer this project owns. */
function countCells(container: HTMLElement): number {
	return container.querySelectorAll(".visx-heatmap-rects > g").length;
}

/**
 * The grid's DATA rects, one per cell. Each cell renders TWICE — the data
 * rect and a shimmer overlay that is `pointer-events: none` — so this
 * filter is what makes the callers' count assertions real guards: a wrong
 * selector fails there instead of quietly asserting over an empty list.
 */
function dataRectsOf(container: HTMLElement): SVGRectElement[] {
	return Array.from(
		container.querySelectorAll<SVGRectElement>(".visx-heatmap-rect"),
	).filter((rect) => rect.getAttribute("pointer-events") !== "none");
}

/** The grid's first cell — the one every hover assertion drives. */
function firstCell(container: HTMLElement): SVGRectElement {
	const rect = dataRectsOf(container)[0];
	if (!rect) {
		throw new Error("the grid rendered no cells");
	}
	return rect;
}

afterEach(() => {
	cleanup();
	resetStableMocks();
});

describe("ActivityHeatmap grid", () => {
	it("renders every cell of a full year, empty days included", () => {
		// A single dictation today: 370 of the 371 cells have no data.
		const { container } = renderGrid([recordOn(new Date(2026, 9, 6), 1)]);

		expect(countCells(container)).toBe(HEATMAP_MAX_WEEKS * 7);
	});

	it("draws each square one gap short of its cell pitch", () => {
		// The grid is a fixed 53 columns across whatever width the card
		// has, so the chart's `gap` is the ONLY slack in the square size:
		// every pixel it takes is a pixel the squares lose. 560px of
		// chart, minus the chart's own 40px left margin (the card reclaims
		// the right one — see the next test), leaves 520px of pitch =
		// 9.811px per cell, drawn 7.811px wide with the vendored 2px gap.
		const { container } = renderGrid([recordOn(new Date(2026, 9, 6), 1)]);
		const pitch = (CHART_WIDTH - 40) / HEATMAP_MAX_WEEKS;
		const cell = container.querySelector(".visx-heatmap-rect");

		expect(cell).toBeTruthy();
		expect(Number(cell?.getAttribute("width"))).toBeCloseTo(pitch - 2, 3);
		expect(Number(cell?.getAttribute("height"))).toBeCloseTo(pitch - 2, 3);
	});

	it("spends the chart's full width on the grid", () => {
		// The card zeroes the chart's right margin, which the vendored
		// default sets to 16px on top of the card's own `p-4`. That margin
		// was dead space: it left the last column 16px short of the edge
		// the legend below already aligns to, and every one of those
		// pixels is a pixel of square size the grid could have had.
		//
		// Measured in PLOT coordinates (the <g> is translated by
		// `margin.left`), so a full-width grid ends at
		// `CHART_WIDTH - margin.left - gap`: the trailing 2px is the
		// gutter the last column keeps, not reclaimable space.
		const { container } = renderGrid([recordOn(new Date(2026, 9, 6), 1)]);
		const rightEdge = Math.max(
			...Array.from(container.querySelectorAll(".visx-heatmap-rect")).map(
				(rect) =>
					Number(rect.getAttribute("x")) + Number(rect.getAttribute("width")),
			),
		);

		expect(rightEdge).toBeCloseTo(CHART_WIDTH - 40 - 2, 3);
	});

	it("renders the same full rectangle for a three-year history", () => {
		const { container } = renderGrid([
			recordOn(new Date(2023, 4, 2), 1),
			recordOn(new Date(2026, 9, 6), 2),
		]);

		// Fixed width: history older than the window is truncated, NOT
		// rendered as extra columns.
		expect(countCells(container)).toBe(HEATMAP_MAX_WEEKS * 7);
	});

	it("renders the same full rectangle for an empty history", () => {
		const { container } = renderGrid([]);

		expect(countCells(container)).toBe(HEATMAP_MAX_WEEKS * 7);
	});

	it("paints empty days with step 01, the neutral hairline", () => {
		// Level 0 is "no dictations", so it must not borrow the colour
		// that means "a lot of dictations": it resolves to scale step 01
		// (--border at 8%), while a day that HAS a dictation starts the
		// accent ramp at step 02. `index-css-chart-tokens-follow-theme`
		// holds the other half — that step 01 really is the neutral.
		const { container } = renderGrid([recordOn(new Date(2026, 9, 6), 1)]);
		const fills = new Set(
			Array.from(container.querySelectorAll(".visx-heatmap-rect")).map((rect) =>
				rect.getAttribute("fill"),
			),
		);

		expect(fills.has("var(--chart-scale-01)")).toBe(true);
		expect(fills.has("var(--chart-scale-02)")).toBe(true);
	});

	it("does not inflate the totals with the empty cells it draws", () => {
		const { heatmap } = renderGrid([recordOn(new Date(2026, 9, 6), 1)]);

		// 53×7 cells are on screen; only one day actually has a dictation.
		expect(heatmap.total).toBe(1);
		expect(heatmap.activeDays).toBe(1);
	});

	it("labels the x-axis once per month in the active locale", () => {
		const { container } = renderGrid([recordOn(new Date(2026, 9, 6), 1)]);

		// The vendored axis used to hardcode English. Its labels now come
		// from the card, so a full-year grid must show ~13 of them rather
		// than one or two.
		const labels = Array.from(
			container.querySelectorAll("span.text-chart-label"),
		).map((node) => node.textContent ?? "");

		expect(labels.length).toBeGreaterThanOrEqual(12);
		expect(labels.every((label) => label.trim().length > 0)).toBe(true);
	});

	it("anchors the last month label to the chart's right edge", () => {
		// The card reclaims the chart's right margin, so the grid ends
		// flush with the container. A month label is WIDER than the column
		// it names, and the final one sits on whichever column that month
		// began — the last column on some dates, four columns earlier on
		// others. Anchoring it to the right edge is what keeps it inside
		// the card instead of spilling into the card's padding; every other
		// label keeps its column anchor so the axis still reads as month
		// boundaries.
		const { container } = renderGrid([recordOn(new Date(2026, 9, 6), 1)]);
		// The Y axis reuses `text-chart-label` for its weekday names; it
		// marks itself with a vertical centring transform, so that is what
		// separates the two rows.
		const xWrappers = Array.from(
			container.querySelectorAll("span.text-chart-label"),
		)
			.map((span) => span.parentElement)
			.filter((node): node is HTMLElement => node?.style.transform === "");

		expect(xWrappers.length).toBeGreaterThanOrEqual(12);

		const rightAnchored = xWrappers.filter(
			(node) => node.style.right === "0px",
		);

		expect(rightAnchored).toHaveLength(1);
		expect(rightAnchored[0]).toBe(xWrappers[xWrappers.length - 1]);
	});

	it("outlines the tooltip with the card's own border", async () => {
		// The tooltip is the only floating surface on this card, so it
		// carries the same `border-border/8` outline as the card instead of
		// relying on its shadow alone (C-DESIGN-2: 8% is the border for
		// cards and panels). jsdom applies no stylesheet, so the class on
		// the panel is what is assertable here.
		const { container } = renderGrid([recordOn(new Date(2026, 9, 6), 1)]);

		fireEvent.pointerOver(firstCell(container));

		await waitFor(() => {
			expect(
				container.querySelector(".text-chart-tooltip-foreground"),
			).toBeTruthy();
		});
		const dateLine = container.querySelector<HTMLElement>(
			".text-chart-tooltip-foreground",
		);
		// The panel is the nearest ancestor that owns the rounded surface.
		const panel = dateLine?.closest<HTMLElement>('[class*="rounded-lg"]');

		expect(panel).toBeTruthy();
		expect(panel?.className).toContain("border-border/8");
	});

	it("leaves the empty legend swatch without an outline", () => {
		// The vendored swatch drew a 1px border in its own colour for level
		// 0. That colour is `--chart-scale-01`, an 8%-alpha hairline, so
		// the border composited over the fill and read as a second, darker
		// step in the scale. The empty swatch keeps its fill and nothing
		// else.
		const { container } = renderGrid([recordOn(new Date(2026, 9, 6), 1)]);
		const swatches = Array.from(
			container.querySelectorAll<HTMLElement>("span.block.shrink-0"),
		);

		expect(swatches.length).toBeGreaterThanOrEqual(5);
		expect(swatches.filter((node) => node.style.border !== "")).toEqual([]);
	});

	it("dims nothing when a cell is hovered", async () => {
		// The vendored default drops every OTHER cell to 30% while one is
		// pointed at, and syncs the Less → More row to the same level. On a
		// 371-cell grid that turns finding the hovered cell into work, and
		// it washed out the legend below. The card switches the effect off,
		// so a hover may raise the cell's tooltip and nothing else.
		const { container } = renderGrid([recordOn(new Date(2026, 9, 6), 1)]);
		const dataRects = dataRectsOf(container);
		expect(dataRects.length).toBe(HEATMAP_MAX_WEEKS * 7);

		// `pointerover`, not `pointerenter`: React synthesises the enter
		// handlers from the bubbling over event, so a dispatched
		// `pointerenter` never reaches the cell and the hover below would
		// silently never happen.
		fireEvent.pointerOver(firstCell(container));

		// The hover has to be OBSERVED before "nothing dimmed" can mean
		// anything. The tooltip is the dim-independent proof of it — it
		// comes from the same handler as the hovered-cell state, so once it
		// is on screen the card is really hovering. Without this wait the
		// opacity assertions below would run before the event was
		// processed and pass whether or not dimming is on.
		await waitFor(() => {
			expect(container.textContent).toContain(
				tChoice("analytics.heatmap.tooltip", 0),
			);
		});
		// One frame for the motion values to reach the DOM.
		await new Promise((resolve) => setTimeout(resolve, 150));

		expect(dataRects.every((rect) => rect.style.opacity === "1")).toBe(true);

		// Same for the legend row, which mirrors the chart's hover level.
		const legendSwatches = Array.from(
			container.querySelectorAll<HTMLElement>("span.leading-none"),
		);
		expect(legendSwatches.length).toBeGreaterThan(0);
		expect(
			legendSwatches.filter((node) => Number(node.style.opacity) < 1),
		).toEqual([]);
	});
});
