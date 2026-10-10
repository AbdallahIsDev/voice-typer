// Range-aware activity chart for the Dashboard: one column per slot
// (a day, or an hour on the "Today" view) with tick labels + horizontal
// gridlines, bars scaled to the max value in the range, and a
// cursor-following tooltip carrying the hovered slot's dictation count
// (tChoice, locale-aware plurals).
// X ticks: weekday names up to a week, month + day past it — a weekday
// stops identifying a day across a 30-day span — and the NEWEST bar is
// always labelled, at every span, because it is the one a reader looks
// for first.
// Zero-vs-no-data: a slot with no dictations draws NOTHING, so a quiet
// day reads as an empty column instead of as a tiny bar; a NO-DATA slot
// — a future hour on the "Today" view, or a day OLDER than the oldest
// record in the history sample — keeps a dashed tick, because "outside
// the sample" is a different claim from "nothing happened".
// The count used to be printed above every bar permanently, which made
// the card read as a table of numbers with bars drawn behind it. It now
// appears only on hover, near the cursor, and carries the count ALONE:
// the slot's label is already on the x axis directly below the bar, so
// repeating it in the tooltip said the same thing twice.
// Accessibility (preserved contract): the whole chart is exposed to AT
// as a single role="img" with a descriptive aria-label that already
// spells out every slot's count, so taking the count out of the visible
// text costs AT nothing; each bar stays a non-interactive <div> (no
// dead-end tab stops), and the hover affordance hangs off the whole
// slot rather than off the mark.

import { useCallback, useRef, useState } from "react";
import { TooltipBox } from "@/components/charts/tooltip/tooltip-box";
import { t, tChoice } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

import {
	type ActivityChartData,
	type ChartKind,
	type RangeId,
	WEEKDAY_LABEL_MAX_SPAN,
} from "../lib/streaks";

export interface ActivityChartProps {
	range: RangeId;
	activity: ActivityChartData;
	/** Pre-formatted custom window ("Oct 1 – Oct 9"); shown instead of
	 *  the range preset label when `range === "custom"`. */
	customWindowLabel?: string;
}

/** Cursor position in plot-relative px, plus the plot's own box. */
interface BarHover {
	text: string;
	x: number;
	y: number;
	containerWidth: number;
	containerHeight: number;
}

/**
 * Bar indices that carry an x tick.
 *
 * The newest bar is ALWAYS labelled. It is the one a reader looks for
 * first, and on the wide ranges it used to be the one bar left blank —
 * the ticks ran out four or five columns short of the end. The remaining
 * ticks are spread so that no two labels can collide.
 */
function tickIndices(count: number, kind: ChartKind): Set<number> {
	if (kind === "hourly") {
		// A clock axis reads in whole hours: step by 3, then add the
		// newest hour — two columns of clearance is enough for a
		// two-digit label.
		const ticks = new Set<number>();
		for (let i = 0; i < count; i += 3) ticks.add(i);
		ticks.add(count - 1);
		return ticks;
	}
	if (count <= WEEKDAY_LABEL_MAX_SPAN) {
		// A week or less: every bar has room for its own label.
		return new Set(Array.from({ length: count }, (_, i) => i));
	}
	// Wider spans: at most six ticks, oldest and newest included.
	// Rounding the exact fractional positions keeps every gap within one
	// column of the others — never 1 — so no two labels can overlap even
	// in a narrow window.
	const slots = Math.min(5, Math.max(1, Math.floor((count - 1) / 2)));
	const ticks = new Set<number>();
	for (let k = 0; k <= slots; k++) {
		ticks.add(Math.round((k * (count - 1)) / slots));
	}
	return ticks;
}

export function ActivityChart({
	range,
	activity,
	customWindowLabel,
}: ActivityChartProps) {
	const { bars, kind } = activity;
	const maxCount = Math.max(1, ...bars.map((b) => b.count));
	// Y-axis ticks at max / mid / 0. When the max is 1, the mid tick
	// would duplicate it (the "1" printed twice at two heights bug) —
	// drop the mid tick so every label is unique. maxCount=2 →
	// [2, 1, 0], still unique.
	const midCount = maxCount > 1 ? Math.max(1, Math.round(maxCount / 2)) : 0;
	const yTicks = maxCount > 1 ? [maxCount, midCount, 0] : [1, 0];
	const ticks = tickIndices(bars.length, kind);

	// Range label for the header's trailing line + aria-label. Custom
	// windows show their formatted span ("Oct 1 – Oct 9"); the bare
	// "Custom" preset label alone would not say which span.
	const rangeLabel =
		range === "custom"
			? (customWindowLabel ?? t("analytics.range.custom"))
			: t(`analytics.range.${range}`);
	const unitLabel =
		kind === "hourly" ? t("analytics.byHour") : t("analytics.byDay");

	const ariaCounts = bars.map((b) => `${b.label}: ${b.count}`).join(", ");

	const plotRef = useRef<HTMLDivElement>(null);
	const [hover, setHover] = useState<BarHover | null>(null);

	// The tooltip tracks the CURSOR (the heatmap's tracks the hovered
	// cell's centre), so the position comes from the pointer event rather
	// than from the mark's geometry. The plot's box is read from the same
	// rect so `TooltipBox` can tell whether there is room to the right of
	// the cursor or whether it has to flip to the left.
	const trackPointer = useCallback(
		(event: React.PointerEvent<HTMLDivElement>, text: string) => {
			const rect = plotRef.current?.getBoundingClientRect();
			if (!rect) return;
			setHover({
				text,
				x: event.clientX - rect.left,
				y: event.clientY - rect.top,
				containerWidth: rect.width,
				containerHeight: rect.height,
			});
		},
		[],
	);

	return (
		<div className="flex flex-col gap-4 rounded-lg border border-border/8 bg-surface-subtle p-4">
			{/* Title and range/unit on ONE baseline, title leading and the
			    window trailing — the same header shape as the heatmap
			    card below, so the two cards' headers read as a pair.
			    Stacking them cost a row of height that said nothing the
			    title did not already imply. */}
			<div className="flex items-baseline justify-between gap-3">
				<h2 className="min-w-0 truncate font-sans text-sm font-medium text-foreground">
					{t("analytics.activityTitle")}
				</h2>
				<p className="shrink-0 text-xs leading-tight text-muted-foreground">
					{rangeLabel} · {unitLabel}
				</p>
			</div>

			<div
				role="img"
				aria-label={t("analytics.activityChartAria", {
					range: rangeLabel,
					counts: ariaCounts,
				})}
				className="flex flex-col gap-2"
			>
				<div className="flex gap-2">
					{/* Y axis: unique tick labels (max / mid / 0; mid is
						dropped when it would duplicate max). */}
					<div className="flex h-36 w-7 shrink-0 flex-col justify-between pb-0 text-end text-[10px] tabular-nums text-muted-foreground">
						{yTicks.map((tick) => (
							<span key={tick}>{tick}</span>
						))}
					</div>

					{/* Plot with gridlines (one per tick, same layout). Also
					    the tooltip's portal target, so the tooltip's
					    coordinates are plot-relative like the heatmap's. */}
					<div
						ref={plotRef}
						data-slot="activity-plot"
						// Clears on leaving the PLOT, not on leaving a slot:
						// `pointerleave` does not bubble, so sliding from one
						// bar to the next never unmounts the tooltip and never
						// replays its entrance animation.
						onPointerLeave={() => setHover(null)}
						className="relative min-w-0 flex-1"
					>
						<div
							aria-hidden="true"
							className="pointer-events-none absolute inset-0 flex flex-col justify-between"
						>
							{yTicks.map((tick) => (
								<div key={tick} className="border-t border-border/15" />
							))}
						</div>
						<div className="relative flex h-36 items-end gap-1">
							{bars.map((bar) => {
								// Scale to 90% of the plot so the tallest bar stops
								// just short of the max gridline instead of touching it.
								const pct =
									bar.count > 0
										? Math.max(6, Math.round((bar.count / maxCount) * 90))
										: 0;
								// A quiet covered day has nothing to report: no
								// mark, and no tooltip. That is the same silence
								// it had when the count lived in a `title`.
								const hasTooltip = bar.count > 0 || bar.isMissing;
								// The slot's own label is already printed under the
								// bar by the x axis, so repeating it here ("Wed: 3
								// dictations") says the same thing twice — the
								// tooltip carries the count alone. A slot with no
								// dictations reads "0 dictations" too, rather than
								// "no data": the dashed tick already marks the
								// slots the sample does not cover, so the tooltip
								// has one job and one shape.
								const tooltip = tChoice("analytics.heatmap.tooltip", bar.count);
								return (
									<div
										key={bar.key}
										data-slot="activity-bar-slot"
										// The hover target is the whole slot, not the
										// mark: the dashed "no data" tick is 4px tall
										// and a bar-height target would make the count
										// reachable only by aiming at the bar itself.
										// Entering a slot with nothing to report clears
										// the tooltip rather than leaving the previous
										// bar's count on screen under this one.
										onPointerEnter={
											hasTooltip
												? (event) => trackPointer(event, tooltip)
												: () => setHover(null)
										}
										onPointerMove={
											hasTooltip
												? (event) => trackPointer(event, tooltip)
												: undefined
										}
										className="flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-1"
									>
										{/* A quiet day draws NO mark at all: the plot
										    then contains only real activity plus the
										    dashed "no data" ticks, so an empty column
										    reads as absence rather than as a bar of
										    height zero. The fill is a flat `bg-accent`
										    with no alpha step: the bar is a read-out,
										    not a control, and the hover affordance
										    lives on the slot above, not on the mark. */}
										{hasTooltip && (
											<div
												data-slot="activity-bar"
												className={cn(
													"w-full max-w-8 rounded-t-lg transition-all duration-300",
													bar.count > 0 && "bg-accent",
													bar.isMissing &&
														"h-1 border-t border-dashed border-border/8 bg-transparent",
												)}
												style={{
													height: bar.count > 0 ? `${pct}%` : undefined,
												}}
											/>
										)}
									</div>
								);
							})}
						</div>

						{/* The count, only while a slot is hovered. `animate={false}`
						    mirrors the heatmap: the panel is not spring-lagged, it
						    sits exactly where the cursor is, while `entrance` keeps
						    the heatmap's scale/slide-in — which TooltipBox replays
						    when the panel has to flip sides. `panelClassName` is
						    where the box's own border has to go (the `className`
						    prop lands on TooltipBox's transparent wrapper), and
						    8% is the shared panel border. */}
						{hover && (
							<TooltipBox
								animate={false}
								containerHeight={hover.containerHeight}
								containerRef={plotRef}
								containerWidth={hover.containerWidth}
								entrance
								panelClassName="border border-border/8"
								visible
								x={hover.x}
								y={hover.y}
							>
								<div className="px-3 py-2.5 text-start font-medium text-chart-tooltip-foreground text-xs">
									{hover.text}
								</div>
							</TooltipBox>
						)}
					</div>
				</div>

				{/* X axis labels. EVERY bar renders a tick — a hidden one keeps
			    its column, which is what keeps the visible labels aligned
			    under their own bars. `tickIndices` only decides which are
			    printed, so filtering the array here instead would slide
			    every label out from under its bar.
			    Fixed two-line height (C-LIFE-2): hourly ticks ("23") take
			    one line while month-day ticks ("Sep 11") wrap to two in
			    narrow columns — without the reservation the card grows
			    when switching ranges. Single-line labels anchor to the
			    top; the box never moves. */}
				<div data-slot="activity-ticks" className="flex h-8 gap-2">
					<div className="w-7 shrink-0" aria-hidden="true" />
					<div className="flex min-w-0 flex-1 gap-1">
						{bars.map((bar, i) => (
							<div
								key={bar.key}
								data-slot="activity-tick"
								className={cn(
									"min-w-0 flex-1 text-center text-[10px] leading-tight text-muted-foreground",
									!ticks.has(i) && "invisible",
								)}
							>
								{bar.label}
							</div>
						))}
					</div>
				</div>
			</div>
		</div>
	);
}
