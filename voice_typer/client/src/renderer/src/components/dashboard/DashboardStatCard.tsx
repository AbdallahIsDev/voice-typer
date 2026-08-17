import { CircleQuestionMarkIcon } from "@hugeicons/core-free-icons";
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
	/**
	 * Optional explanatory tooltip shown via a small (?) info icon
	 * next to the label. Only pass this when the tooltip adds
	 * genuinely new information (e.g. a sampling caveat) — a
	 * restatement of the label is noise.
	 */
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
	// Label text — truncated to a single line so a long label can
	// never wrap and break the card's vertical rhythm.
	const labelNode = (
		<p className="min-w-0 max-w-full truncate text-xs leading-tight text-(--text-muted)">
			{label}
		</p>
	);
	return (
		// Informational display card — NOT interactive: no hover
		// lift/border change. Icon rendered at a consistent size with
		// the muted icon tone (same family as action icons), no inner
		// padding container shrinking it. Reduced padding + a medium
		// (not bold) value keep the card airy rather than cramped.
		<div className="flex flex-col items-center justify-center gap-1.5 rounded-xl border border-border/10 bg-(--bg-subtle) p-3 text-center">
			<HugeiconsIcon
				icon={icon}
				strokeWidth={1.75}
				className="h-5 w-5 text-(--text-muted)"
			/>
			<p className="text-2xl font-medium leading-none tracking-tight tabular-nums text-(--text-primary)">
				{value}
			</p>
			{tooltip ? (
				<div className="flex min-w-0 items-center justify-center gap-1">
					{labelNode}
					{/* (?) info icon — the tooltip trigger. A focusable
						button (not the label text) so keyboard users can
						reach it: Radix Tooltip opens on focus, so Tab →
						Enter/Space works without hover. */}
					<Tooltip>
						<TooltipTrigger asChild>
							<button
								type="button"
								aria-label={t("analytics.infoTooltipAria", { label })}
								className="inline-flex size-4 shrink-0 cursor-help items-center justify-center rounded-full text-(--text-muted) outline-hidden transition-colors hover:text-(--text-primary) focus-visible:ring-2 focus-visible:ring-ring"
							>
								<HugeiconsIcon
									icon={CircleQuestionMarkIcon}
									strokeWidth={2}
									className="size-3.5"
								/>
							</button>
						</TooltipTrigger>
						<TooltipContent side="bottom">{tooltip}</TooltipContent>
					</Tooltip>
				</div>
			) : (
				labelNode
			)}
			{sublabel && (
				<p className="max-w-full truncate text-xs leading-tight text-(--text-muted)">
					{sublabel}
				</p>
			)}
			{trend && <TrendIndicator trend={trend} />}
		</div>
	);
}
