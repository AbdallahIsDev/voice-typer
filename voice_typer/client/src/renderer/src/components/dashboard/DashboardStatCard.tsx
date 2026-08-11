import type { IconSvgElement } from "@hugeicons/react";
import { HugeiconsIcon } from "@hugeicons/react";

interface DashboardStatCardProps {
	label: string;
	value: string;
	icon: IconSvgElement;
	sublabel?: string;
}

export function DashboardStatCard({
	label,
	value,
	icon,
	sublabel,
}: DashboardStatCardProps) {
	return (
		<div className="rounded-xl border border-border bg-(--bg-subtle) p-5 flex flex-col items-center justify-center gap-2.5 text-center transition-all duration-200 hover:-translate-y-0.5 hover:border-accent/30 hover:shadow-sm">
			{/* Icon chip mirrors the QuickInfoCard treatment so the two
			    card families share a visual language: soft accent
			    wash behind the glyph instead of a bare icon. */}
			<div className="rounded-lg bg-accent/10 p-2">
				<HugeiconsIcon
					icon={icon}
					strokeWidth={2}
					className="h-4 w-4 text-accent"
				/>
			</div>
			<p className="text-2xl font-bold text-(--text-primary) leading-none tracking-tight tabular-nums">
				{value}
			</p>
			<p className="text-xs text-(--text-muted)">{label}</p>
			{sublabel && <p className="text-xs text-(--text-muted)">{sublabel}</p>}
		</div>
	);
}
