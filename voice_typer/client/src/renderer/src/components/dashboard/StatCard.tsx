import type { IconSvgElement } from "@hugeicons/react";
import { HugeiconsIcon } from "@hugeicons/react";

import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

export interface StatTrend {
	/** Percentage change vs the previous period (can be 0 = flat). */
	pct: number;
	up: boolean;
}

interface StatCardProps {
	label: string;
	value: string;
	icon: IconSvgElement;
	sublabel?: string;
	/**
	 * Optional comparison vs the previous period of the same length
	 * (null = no prior period exists, e.g. the All Time total).
	 */
	trend?: StatTrend | null;
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

export function StatCard({
	label,
	value,
	icon,
	sublabel,
	trend,
}: StatCardProps) {
	return (
		// Informational display card — NOT interactive: no hover
		// lift/border change. Layout: a single top row of icon +
		// label (horizontal, left-aligned — not stacked), then the
		// main number on its own line below. The value is semibold
		// (one step up from the old medium) so it stays airy rather
		// than heavy. The value's `mt-auto` pushes it (and any
		// trailing sublabel/trend) to the bottom of the stretched
		// card — the icon+label row stays pinned at the top with
		// breathing room between it and the number. `min-h-24`
		// guarantees that breathing room even when the row's tallest
		// card is otherwise only as tall as its content.
		<div className="flex min-h-24 flex-col gap-1.5 rounded-xl border border-border/10 bg-(--bg-subtle) p-3">
			{/* Label row — icon on the far left, immediately followed
			    by the card's title. Truncated to a single line so a
			    long label can never wrap and break the card's
			    vertical rhythm. */}
			<div className="flex min-w-0 items-center gap-1.5">
				<HugeiconsIcon
					icon={icon}
					strokeWidth={1.75}
					className="h-5 w-5 shrink-0 text-(--text-muted)"
				/>
				<p className="min-w-0 max-w-full truncate text-xs leading-tight text-(--text-muted)">
					{label}
				</p>
			</div>
			<p className="mt-auto text-2xl font-semibold leading-none tracking-tight tabular-nums text-(--text-primary)">
				{value}
			</p>
			{sublabel && (
				<p className="max-w-full truncate text-xs leading-tight text-(--text-muted)">
					{sublabel}
				</p>
			)}
			{trend && <TrendIndicator trend={trend} />}
		</div>
	);
}
