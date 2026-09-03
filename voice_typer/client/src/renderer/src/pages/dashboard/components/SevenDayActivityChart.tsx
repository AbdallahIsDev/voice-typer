// Range-aware activity chart for the Dashboard.
//
// Replaces the old 7-day-only bar strip with a proper chart: a y-axis
// with tick labels + horizontal gridlines, bars scaled to the max value
// in the range, the count above each bar, a hover tooltip per bar
// (tChoice, locale-aware plurals), and a clear visual distinction
// between:
//   - a zero-activity slot (solid muted baseline tick), and
//   - a NO-DATA slot (dashed tick) — a future hour on the "Today" view,
//     or a day OLDER than the oldest record in the history sample
//     (the sample simply doesn't reach back that far).
//
// Accessibility (preserved contract): the whole chart is exposed to AT
// as a single role="img" with a descriptive aria-label (no dead-end tab
// stops); each bar is a non-interactive <div> with a title tooltip for
// sighted mouse users.

import { Activity03Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { t, tChoice } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

import type { ActivityChartData, RangeId } from "../lib/streaks";

export interface ActivityChartProps {
	range: RangeId;
	activity: ActivityChartData;
}

/** Show an x tick label every N bars (crowding control for wide ranges). */
function tickEvery(activity: ActivityChartData): number {
	if (activity.kind === "hourly") return 3;
	const span = activity.daySpan;
	if (span <= 7) return 1;
	if (span <= 14) return 2;
	return 5;
}

export function ActivityChart({ range, activity }: ActivityChartProps) {
	const { bars, kind } = activity;
	const maxCount = Math.max(1, ...bars.map((b) => b.count));
	// Y-axis ticks at max / mid / 0. When the max is 1, the mid tick
	// would duplicate it (the "1" printed twice at two heights bug) —
	// drop the mid tick so every label is unique. maxCount=2 →
	// [2, 1, 0], still unique.
	const midCount = maxCount > 1 ? Math.max(1, Math.round(maxCount / 2)) : 0;
	const yTicks = maxCount > 1 ? [maxCount, midCount, 0] : [1, 0];
	const every = tickEvery(activity);

	// Range label for the subtitle + aria-label.
	const rangeLabel = t(`analytics.range.${range}`);
	const unitLabel =
		kind === "hourly" ? t("analytics.byHour") : t("analytics.byDay");

	const ariaCounts = bars.map((b) => `${b.label}: ${b.count}`).join(", ");

	return (
		<div className="flex flex-col gap-4 rounded-xl border border-border/5 bg-(--bg-subtle) p-4">
			<div className="flex items-center gap-2.5">
				{/* Icon grouped directly left of the title (was stranded
				in the top-right corner). */}
				<HugeiconsIcon
					icon={Activity03Icon}
					// Stroke tuned for the larger render size: at h-9 w-9
					// (36px) the stat-card 1.75 would paint ~2.6px lines,
					// heavier than every other icon on the page. strokeWidth
					// 1 renders ~1.5px on screen — the same visual weight as
					// the h-5 w-5 stat-card icons (1.75 × 20/24 ≈ 1.46px).
					strokeWidth={1}
					// Sized to roughly match the stacked title+subtitle block
					// beside it (was h-4 w-4, disproportionately tiny next
					// to a two-line text block).
					className="h-8 w-8 shrink-0 text-(--text-muted)"
				/>
				<div className="flex flex-col gap-0.5">
					<h2 className="font-sans text-sm font-semibold text-(--text-primary)">
						{t("analytics.activityTitle")}
					</h2>
					<p className="text-xs text-(--text-muted)">
						{rangeLabel} · {unitLabel}
					</p>
				</div>
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
					<div className="flex h-36 w-7 shrink-0 flex-col justify-between pb-0 text-end text-[10px] tabular-nums text-(--text-muted)">
						{yTicks.map((tick) => (
							<span key={tick}>{tick}</span>
						))}
					</div>

					{/* Plot with gridlines (one per tick, same layout) */}
					<div className="relative min-w-0 flex-1">
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
								// Scale to 90% of the plot so the count label above the
								// tallest bar stays INSIDE the plot (below the max
								// gridline) instead of overflowing into the axis.
								const pct =
									bar.count > 0
										? Math.max(6, Math.round((bar.count / maxCount) * 90))
										: 0;
								const tooltip = bar.isMissing
									? t("analytics.noDataBar", { label: bar.label })
									: tChoice("analytics.dayCountTooltip", bar.count, {
											label: bar.label,
										});
								return (
									<div
										key={bar.key}
										className="flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-1"
									>
										{/* count label above the bar (only when > 0) */}
										<span className="text-[10px] leading-none tabular-nums text-(--text-muted)">
											{bar.count > 0 ? bar.count : ""}
										</span>
										<div
											title={tooltip}
											className={cn(
												// ~4px top corners (this theme's --radius-sm resolves
												// to 6px — use an explicit value for the requested
												// small rounding).
												"w-full max-w-8 rounded-t-[4px] transition-all duration-300",
												bar.count > 0 && "bg-accent/90 hover:bg-accent",
												bar.count === 0 &&
													!bar.isMissing &&
													"h-1 rounded-sm bg-border/50",
												bar.isMissing &&
													"h-1 border-t border-dashed border-border/5 bg-transparent",
											)}
											style={{ height: bar.count > 0 ? `${pct}%` : undefined }}
										/>
									</div>
								);
							})}
						</div>
					</div>
				</div>

				{/* X axis labels (aligned under the plot, skipping for crowding) */}
				<div className="flex gap-2">
					<div className="w-7 shrink-0" aria-hidden="true" />
					<div className="flex min-w-0 flex-1 gap-1">
						{bars.map((bar, i) => (
							<div
								key={bar.key}
								className={cn(
									"min-w-0 flex-1 text-center text-[10px] text-(--text-muted)",
									i % every !== 0 && "invisible",
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
