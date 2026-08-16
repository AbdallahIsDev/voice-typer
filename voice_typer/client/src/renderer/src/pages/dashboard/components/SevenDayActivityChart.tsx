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
	/** All-time total row count (from get_history_count). */
	totalCount: number;
	/** Size of the history sample the bars are computed from. */
	sampleSize: number;
}

/** Show an x tick label every N bars (crowding control for wide ranges). */
function tickEvery(activity: ActivityChartData): number {
	if (activity.kind === "hourly") return 3;
	const span = activity.daySpan;
	if (span <= 7) return 1;
	if (span <= 14) return 2;
	return 5;
}

export function ActivityChart({
	range,
	activity,
	totalCount,
	sampleSize,
}: ActivityChartProps) {
	const { bars, kind } = activity;
	const maxCount = Math.max(1, ...bars.map((b) => b.count));
	const midCount = Math.max(1, Math.round(maxCount / 2));
	const every = tickEvery(activity);

	// Range label for the subtitle + aria-label.
	const rangeLabel = t(`analytics.range.${range}`);
	const unitLabel =
		kind === "hourly" ? t("analytics.byHour") : t("analytics.byDay");

	const ariaCounts = bars.map((b) => `${b.label}: ${b.count}`).join(", ");

	// Footnote when the sample is capped: totals/bars reflect only the
	// most recent `sampleSize` dictations, so days older than the oldest
	// sampled record are unknown (rendered as missing ticks).
	const sampled = range === "all" && totalCount > sampleSize && sampleSize > 0;

	return (
		<div className="rounded-xl border border-border/10 bg-(--bg-subtle) p-4">
			<div className="mb-4 flex items-center justify-between gap-2">
				<div className="space-y-0.5">
					<h2 className="font-sans text-sm font-semibold text-(--text-primary)">
						{t("analytics.activityTitle")}
					</h2>
					<p className="text-xs text-(--text-muted)">
						{rangeLabel} · {unitLabel}
					</p>
				</div>
				<HugeiconsIcon
					icon={Activity03Icon}
					strokeWidth={1.625}
					className="h-4 w-4 text-(--text-muted)"
				/>
			</div>

			<div
				role="img"
				aria-label={t("analytics.activityChartAria", {
					range: rangeLabel,
					counts: ariaCounts,
				})}
			>
				<div className="flex gap-2">
					{/* Y axis: tick labels at max / mid / 0 */}
					<div className="flex h-36 w-7 shrink-0 flex-col justify-between pb-0 text-end text-[10px] tabular-nums text-(--text-muted)">
						<span>{maxCount}</span>
						<span>{midCount}</span>
						<span>0</span>
					</div>

					{/* Plot with gridlines */}
					<div className="relative min-w-0 flex-1">
						<div
							aria-hidden="true"
							className="pointer-events-none absolute inset-0 flex flex-col justify-between"
						>
							<div className="border-t border-border/15" />
							<div className="border-t border-border/15" />
							<div className="border-t border-border/15" />
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
										className="flex h-full min-w-0 flex-1 flex-col items-center justify-end"
									>
										{/* count label above the bar (only when > 0) */}
										<span className="mb-1 text-[10px] leading-none tabular-nums text-(--text-muted)">
											{bar.count > 0 ? bar.count : ""}
										</span>
										<div
											title={tooltip}
											className={cn(
												"w-full max-w-8 transition-all duration-300",
												bar.count > 0 && "bg-accent/90 hover:bg-accent",
												bar.count === 0 &&
													!bar.isMissing &&
													"h-1 rounded-sm bg-border/50",
												bar.isMissing &&
													"h-1 border-t border-dashed border-border/30 bg-transparent",
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
				<div className="mt-1.5 flex gap-2">
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

			{/* Footnote: sampled data / older days omitted */}
			{(sampled || activity.coveredFromKey) && (
				<p className="mt-3 border-t border-border/10 pt-2 text-[11px] text-(--text-muted)">
					{sampled && t("analytics.sampledNote", { count: String(sampleSize) })}
					{sampled && activity.coveredFromKey ? " · " : ""}
					{activity.coveredFromKey &&
						t("analytics.olderDaysOmitted", {
							date: activity.coveredFromKey,
						})}
				</p>
			)}
		</div>
	);
}
