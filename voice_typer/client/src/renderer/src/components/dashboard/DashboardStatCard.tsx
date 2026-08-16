import type { IconSvgElement } from "@hugeicons/react";
import { HugeiconsIcon } from "@hugeicons/react";

import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

export interface StatTrend {
	/** Percentage change vs the previous period (can be 0 = flat). */
	pct: number;
	up: boolean;
}

interface DashboardStatCardProps {
	label: string;
	value: string;
	icon: IconSvgElement;
	sublabel?: string;
	/**
	 * Optional comparison vs the previous period of the same length
	 * (null = no prior period exists, e.g. the All Time total).
	 */
	trend?: StatTrend | null;
	/** Optional explanatory tooltip on the label (e.g. what a metric measures). */
	tooltip?: string;
}

function TrendIndicator({ trend }: { trend: StatTrend }) {
	const { pct, up } = trend;
	// Screen-reader copy + native tooltip for the arrow glyph (aria-hidden).
	const ariaLabel =
		pct === 0
			? t("analytics.trendFlatLabel")
			: up
				? t("analytics.trendUpLabel", { pct: String(pct) })
				: t("analytics.trendDownLabel", { pct: String(pct) });
	const glyph = pct === 0 ? "–" : up ? "▲" : "▼";
	return (
		<span
			title={ariaLabel}
			role="img"
			aria-label={ariaLabel}
			className={cn(
				"inline-flex items-center gap-0.5 text-[11px] font-medium tabular-nums",
				pct === 0 && "text-(--text-muted)",
				pct > 0 && up && "text-emerald-500",
				pct > 0 && !up && "text-destructive",
			)}
		>
			<span aria-hidden="true" className="text-[9px] leading-none">
				{glyph}
			</span>
			{pct > 0 ? `${pct}%` : ""}
		</span>
	);
}

export function DashboardStatCard({
	label,
	value,
	icon,
	sublabel,
	trend,
	tooltip,
}: DashboardStatCardProps) {
	const labelNode = (
		<p className="text-xs text-(--text-muted) leading-tight">{label}</p>
	);
	return (
		<div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-border/10 bg-(--bg-subtle) p-4 text-center transition-all duration-200 hover:-translate-y-0.5 hover:border-accent/30 hover:shadow-sm">
			<div className="rounded-lg bg-accent/10 p-1.5">
				<HugeiconsIcon
					icon={icon}
					strokeWidth={2}
					className="h-4 w-4 text-accent"
				/>
			</div>
			<p className="text-2xl font-bold leading-none tracking-tight tabular-nums text-(--text-primary)">
				{value}
			</p>
			{tooltip ? (
				<Tooltip>
					<TooltipTrigger asChild>{labelNode}</TooltipTrigger>
					<TooltipContent side="bottom">{tooltip}</TooltipContent>
				</Tooltip>
			) : (
				labelNode
			)}
			{sublabel && (
				<p className="text-xs text-(--text-muted) leading-tight">{sublabel}</p>
			)}
			{trend && <TrendIndicator trend={trend} />}
		</div>
	);
}
