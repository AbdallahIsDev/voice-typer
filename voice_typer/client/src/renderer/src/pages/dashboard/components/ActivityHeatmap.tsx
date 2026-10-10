// Contribution-style dictation heatmap card for the Analytics (Dashboard)
// page. Wraps the vendored Bklit UI chart
// (`@bklit/heatmap-chart`, see `components.json` → `registries.@bklit`,
// source under `components/charts/heatmap/`).
//
// Why this card is NOT range-aware: the title bar's range control answers
// "what happened in this window"; the heatmap answers "how consistent
// have I been", which only reads at a scale of months. Driving it from
// the selector would collapse "Today" to a single cell. The grid's own
// x-axis prints ~13 month labels spanning the year, so the window it
// covers is legible from the axis itself — the card used to restate it
// as a prose subtitle ("Oct 5, 2025 – Oct 8 · per day"), which was the
// axis said twice. The active-days count and the current streak are
// whole-history figures of the same kind, so they are surfaced in this
// card's header instead of as range-aware stat cards.
//
// The grid is FIXED-WIDTH — always 53 weeks ending with the current week
// (see `../lib/heatmap`). A two-week history therefore renders a full
// year of cells that are mostly empty, rather than a two-column sliver;
// the empty cells left of the first record mean "no data yet".
//
// Accessibility: the chart's <svg> is `aria-hidden`, so the whole card
// body is exposed as ONE `role="img"` with a summary label (same
// contract as the activity chart) — 180+ hover-only cells must not
// become 180 tab stops, and there is no keyboard path to a cell's
// tooltip, so the summary carries the totals instead.
//
// i18n: the vendored chart hardcodes English formatting in
// `HeatmapXAxis` (month names), `HeatmapYAxis` (weekday names) and
// `HeatmapTooltip` (date/weekday). All three take override props, and this
// card feeds them locale-derived values, so every string the chart renders
// here is translated (C-I18N-1). The month labels matter most: a
// full-year grid shows ~13 of them.

import { useMemo } from "react";
import {
	HeatmapCells,
	HeatmapChart,
	HeatmapInteractionBoundary,
	HeatmapInteractionProvider,
	HeatmapLegend,
	HeatmapTooltip,
	HeatmapXAxis,
	HeatmapYAxis,
} from "@/components/charts/heatmap";
import { getLocale, t, tChoice } from "@/i18n/i18n";
import type { DictationHeatmap } from "../lib/heatmap";

export interface ActivityHeatmapProps {
	heatmap: DictationHeatmap;
	/**
	 * Current consecutive-day streak (0 = no streak, hint omitted).
	 * Shown beside the active-days count: both count the heatmap's own
	 * whole-history window, which is why the removed Active Days stat
	 * card's figures live here instead of beside range-aware numbers.
	 */
	currentStreak?: number;
}

export function ActivityHeatmap({
	heatmap,
	currentStreak = 0,
}: ActivityHeatmapProps) {
	const { columns, total, activeDays, startDate, endDate, truncated } = heatmap;

	const locale = getLocale();

	// The vendored chart defaults to English-only weekday/date formatting;
	// this card is translated, so it derives every label the chart renders
	// from the active locale (C-I18N-1) via the chart's override props.
	// Sunday-first, matching `weekStartDay={0}` below.
	const dayLabels = useMemo(() => {
		const fmt = new Intl.DateTimeFormat(locale, { weekday: "short" });
		// 2023-01-01 is a Sunday, so index 0 stays Sunday-first.
		return Array.from({ length: 7 }, (_, i) =>
			fmt.format(new Date(2023, 0, 1 + i)),
		);
	}, [locale]);

	// January-first, because `buildHeatmapMonthTicks` indexes it by
	// `Date.getMonth()`. A full-year grid renders ~13 month labels, so
	// leaving them English would be the loudest untranslated string on the
	// card (C-I18N-1).
	const monthLabels = useMemo(() => {
		const fmt = new Intl.DateTimeFormat(locale, { month: "short" });
		return Array.from({ length: 12 }, (_, i) =>
			fmt.format(new Date(2023, i, 1)),
		);
	}, [locale]);

	const formatDate = useMemo(() => {
		const fmt = new Intl.DateTimeFormat(locale, {
			day: "numeric",
			month: "long",
			year: "numeric",
		});
		return (date: Date) => fmt.format(date);
	}, [locale]);

	const formatWeekday = useMemo(() => {
		const fmt = new Intl.DateTimeFormat(locale, { weekday: "long" });
		return (date: Date) => fmt.format(date);
	}, [locale]);

	// The card's name, and the words that follow the figure in its
	// heading. One string serves both: the section's accessible name and
	// the visible label are the same phrase, so a screen reader hears the
	// heading it can see (see the file header on why this replaced
	// "Usage History").
	const title = t("analytics.heatmap.title");

	return (
		<section
			aria-label={title}
			className="flex flex-col gap-4 rounded-lg border border-border/8 bg-surface-subtle p-4"
		>
			{/* ONE header line: the card's own KPI is its title, with the
			    secondary whole-history figure pushed to the far end. The
			    count uses the heatmap's OWN `activeDays`, not the
			    range-aware period count: this card ignores the title bar's
			    range control, and a window-scoped number printed over a
			    full-year grid would contradict what the axis shows.
			    `truncated` stays on this line as a muted suffix rather than
			    a subtitle of its own — it is the one fact the axis cannot
			    state (that the grid is CAPPED, not that history began a
			    year ago), so it appears only when the cap actually bites. */}
			<div className="flex items-baseline justify-between gap-3">
				<h2 className="min-w-0 truncate font-sans text-sm font-medium text-foreground">
					<span className="tabular-nums">{activeDays}</span>{" "}
					<span className="font-normal text-muted-foreground">{title}</span>
					{truncated && (
						<span className="font-normal text-muted-foreground">
							{` · ${t("analytics.heatmap.truncated")}`}
						</span>
					)}
				</h2>
				{currentStreak > 0 && (
					<p className="shrink-0 text-xs leading-tight text-muted-foreground">
						{t("analytics.dayStreak", { count: String(currentStreak) })}
					</p>
				)}
			</div>

			<div
				role="img"
				aria-label={t("analytics.heatmap.aria", {
					total: String(total),
					days: String(activeDays),
				})}
			>
				{/* One interaction provider for chart AND legend, placed above
				    both: `HeatmapInteractionBoundary` requires one, and the
				    chart skips its own internal provider when it finds this
				    one, so the two share a single hover/tooltip state. The
				    legend no longer dims anything with it (see
				    `inactiveOpacity` below), so it is marked
				    non-interactive rather than advertising a pointer cursor
				    for a hover that has no effect. */}
				<HeatmapInteractionProvider>
					<HeatmapInteractionBoundary className="w-full">
						<HeatmapChart
							data={columns}
							layout="fluid"
							// Reserved geometry (C-LIFE-2): the vendored chart
							// measures its box via ParentSize, whose initial
							// size is 0×0 — the first frame renders a 28px
							// stub that jumps to full height on measure.
							// Fluid height is exact math (top margin 28 +
							// 7 rows × (W − left 40) / 53 columns), so W/H
							// sits between ~4.8 and ~6.2 across 300–800px:
							// 6/1 reserves within ±12px everywhere and the
							// 64px floor covers narrow windows, so the card
							// holds its height from first paint and the
							// measured grid fills the reservation instead of
							// pushing the page down.
							className="aspect-[6/1] min-h-16"
							// Right margin reclaimed so the grid ends flush with
							// the card's content box: the chart reserved 16px of
							// its own right padding on top of the card's `p-4`,
							// which left the last column short of the edge the
							// legend below already aligns to. Fluid sizing grows
							// `binHeight` with `binWidth`, so the cells stay
							// square as they take the space. `HeatmapXAxis`
							// clamps its last month label, which is what makes
							// the reclaim safe on every calendar date.
							margin={{ right: 0 }}
							// Cell size comes from the full grid width, not from
							// the columns the x-domain filter happens to keep:
							// `fluid` divides innerWidth by this, so using the
							// filtered count would resize all 53 cells whenever
							// one boundary column drops.
							sizingColumnCount={columns.length}
							// No entrance animation. The vendored chart's
							// staggered reveal fades the year in cell by cell
							// (random per-cell delays inside a 1s window), so
							// the grid arrives dripping rather than drawn; the
							// card is a read-out, and it must be complete on
							// the first paint.
							animate={false}
							weekStartDay={0}
							xDomain={[startDate, endDate]}
						>
							{/* Ghost cells stay ON screen. The chart's ghost logic
							    infers a GitHub-style calendar range from the grid
							    shape and hides the bins outside it — which, on
							    the days that inference matches, would shave the
							    leading days of column 0 and the trailing days of
							    the current week, leaving a ragged edge that
							    changes shape by date. The card's whole point is a
							    complete rectangle, so opt out. */}
							{/* `inactiveOpacity={1}` switches OFF the vendored
							    hover dim, which drops every OTHER cell to 30%
							    while one is pointed at. On a 371-cell grid that
							    turns finding the hovered cell into work: the
							    tooltip already says which day it is, and the
							    dimming also washed out the legend. */}
							<HeatmapCells hideGhostCells={false} inactiveOpacity={1} />
							<HeatmapXAxis monthLabels={monthLabels} />
							<HeatmapYAxis dayLabels={dayLabels} />
							<HeatmapTooltip
								formatDate={formatDate}
								formatLabel={(count) =>
									tChoice("analytics.heatmap.tooltip", count)
								}
								formatWeekday={formatWeekday}
								// The panel is the one floating surface on the
								// card, so it carries the card's own outline
								// rather than only a shadow (C-DESIGN-2).
								panelClassName="border border-border/8"
							/>
						</HeatmapChart>
						<HeatmapLegend
							className="mt-3"
							// `inactiveOpacity={1}` turns off the legend's half
							// of the same hover effect, and `interactive={false}`
							// follows from it: with nothing left to show on
							// hover, the vendored pointer cursor would be
							// advertising an interaction that does not happen.
							inactiveOpacity={1}
							interactive={false}
							lessLabel={t("analytics.heatmap.less")}
							moreLabel={t("analytics.heatmap.more")}
						/>
					</HeatmapInteractionBoundary>
				</HeatmapInteractionProvider>
			</div>
		</section>
	);
}
