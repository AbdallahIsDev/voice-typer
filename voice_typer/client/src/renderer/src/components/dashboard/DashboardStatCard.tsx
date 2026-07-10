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
		<div className="card-hover rounded-xl border border-border bg-(--bg-subtle) p-5 flex flex-col items-center justify-center gap-2 text-center">
			<HugeiconsIcon
				icon={icon}
				strokeWidth={2}
				className="h-4 w-4 text-accent"
			/>
			<p className="text-2xl font-bold text-(--text-primary) leading-none tracking-tight">
				{value}
			</p>
			<p className="text-xs text-(--text-muted)">{label}</p>
			{sublabel && (
				<p className="text-[10px] text-(--text-muted) opacity-60">{sublabel}</p>
			)}
		</div>
	);
}
