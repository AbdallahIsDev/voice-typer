import type { IconSvgElement } from "@hugeicons/react";
import { HugeiconsIcon } from "@hugeicons/react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface QuickInfoCardProps {
	icon: IconSvgElement;
	label: string;
	value: ReactNode;
	/**
	 * Quieter styling for system/config info (the "Current Setup"
	 * section) so it doesn't compete with the usage metrics for
	 * attention: smaller text, no accent wash behind the icon.
	 */
	muted?: boolean;
}

export function QuickInfoCard({
	icon,
	label,
	value,
	muted,
}: QuickInfoCardProps) {
	return (
		<div
			className={cn(
				"flex items-center gap-3 rounded-lg border border-border/10 transition-colors duration-200",
				muted
					? "bg-(--bg-subtle)/50 p-3"
					: "bg-(--bg-subtle) p-3.5 hover:border-accent/30",
			)}
		>
			<div
				className={cn(
					"rounded-lg",
					muted ? "bg-accent/5 p-1.5" : "bg-accent/10 p-2",
				)}
			>
				<HugeiconsIcon
					icon={icon}
					strokeWidth={2}
					className={cn(
						"h-4 w-4",
						muted ? "text-(--text-muted)" : "text-accent",
					)}
				/>
			</div>
			<div className="min-w-0">
				<p className="text-[11px] font-medium text-(--text-muted)">{label}</p>
				<p
					className={cn(
						"truncate font-semibold text-(--text-primary)",
						muted ? "text-[13px]" : "text-sm",
					)}
				>
					{value}
				</p>
			</div>
		</div>
	);
}
