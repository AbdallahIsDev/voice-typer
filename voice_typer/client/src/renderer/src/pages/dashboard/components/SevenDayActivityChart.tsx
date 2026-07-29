// DR-10: 7-day activity bar chart extracted from `pages/Dashboard.tsx`
// (lines ~599-670 of the pre-split file).
//
// Pure presentational component — receives the pre-computed
// `dailyActivity` array (built by `computeDailyActivity` in
// `../lib/streaks`) and renders the chart card. The chart is
// informational, not interactive: BG-3 wrapped each bar in a <button>
// previously which produced 7 dead-end tab stops and an SR
// announcement of "button, button, ..." — now the entire chart is
// exposed to AT as a single role="img" with a descriptive aria-label,
// and each bar is a non-interactive <div> with a `title` tooltip for
// sighted mouse users.

import { Activity03Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { t } from "@/i18n/i18n";

import { barHeight } from "../lib/format";
import type { DashboardData } from "../lib/streaks";

export interface SevenDayActivityChartProps {
	data: DashboardData;
}

export function SevenDayActivityChart({ data }: SevenDayActivityChartProps) {
	const d = data;
	const maxCount = Math.max(1, ...d.dailyActivity.map((a) => a.count));

	return (
		<div className="rounded-xl border border-border bg-(--bg-subtle) p-5">
			<div className="flex items-center justify-between mb-5">
				<div className="space-y-0.5">
					<h2 className="font-sans text-sm font-semibold text-(--text-primary)">
						{t("analytics.sevenDayActivity")}
					</h2>
					<p className="text-xs text-(--text-muted)">
						{t("analytics.transcriptionsPerDay")}
					</p>
				</div>
				<HugeiconsIcon
					icon={Activity03Icon}
					strokeWidth={1.625}
					className="h-4 w-4 text-(--text-muted)"
				/>
			</div>
			<div
				className="flex items-end justify-between gap-2 h-20"
				role="img"
				aria-label={t("analytics.sevenDayActivityChartAria", {
					counts: d.dailyActivity
						.map((a) => `${a.label}: ${a.count}`)
						.join(", "),
				})}
			>
				{d.dailyActivity.map((day) => {
					// BG-3: the 7-day chart is informational, not
					// interactive. Wrapping each bar in a <button>
					// produced 7 dead-end tab stops and an SR
					// announcement of "button, button, ..." with no
					// chart context. We now expose the entire chart
					// to AT as a single role="img" with a
					// descriptive aria-label (set on the container
					// above) and render each bar as a non-interactive
					// <div> with a title attribute for the mouse
					// hover tooltip (title is not announced by SRs
					// but is available to sighted mouse users).
					// Bumped bar opacity from /60 to /80 for
					// WCAG 1.4.11 contrast against adjacent
					// backgrounds.
					return (
						<div
							key={day.date}
							className="flex flex-1 flex-col items-center gap-2"
						>
							<span className="text-xs text-(--text-muted) font-medium tabular-nums">
								{day.count}
							</span>
							<div
								title={
									day.count === 1
										? t("analytics.dayCountTooltipSingular", {
												label: day.label,
												count: String(day.count),
											})
										: t("analytics.dayCountTooltipPlural", {
												label: day.label,
												count: String(day.count),
											})
								}
								className="w-full max-w-10 rounded-sm bg-accent/80 transition-all duration-300"
								style={{ height: `${barHeight(day.count, maxCount)}px` }}
							/>
							<span className="text-[11px] text-(--text-muted)">
								{day.dayName}
							</span>
						</div>
					);
				})}
			</div>
		</div>
	);
}
