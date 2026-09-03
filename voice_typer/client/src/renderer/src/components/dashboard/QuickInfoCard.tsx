import type { IconSvgElement } from "@hugeicons/react";
import { HugeiconsIcon } from "@hugeicons/react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface QuickInfoCardProps {
	icon: IconSvgElement;
	label: string;
	value: ReactNode;
	/**
	 * Optional secondary line under the value (e.g. the corrections
	 * rate) — rendered with the same muted style as the label.
	 */
	sublabel?: ReactNode;
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
	sublabel,
	muted,
}: QuickInfoCardProps) {
	return (
		// Informational card — no hover interaction. Icon rendered at a
		// consistent size with the muted icon tone (same family as the
		// stat-card icons), no inner padding container shrinking it.
		// The text block stretches to the card height (`items-stretch`)
		// with the value's `mt-auto` pushing it to the bottom — same
		// top-pinned label / bottom-pushed number rhythm as the top-row
		// stat cards.
		<div
			className={cn(
				"flex items-stretch gap-3 rounded-xl border border-border/5",
				muted ? "bg-(--bg-subtle)/50 p-3" : "bg-(--bg-subtle) p-4",
			)}
		>
			<HugeiconsIcon
				icon={icon}
				strokeWidth={1.75}
				className="h-5 w-5 shrink-0 text-(--text-muted)"
			/>
			<div className="flex min-w-0 flex-col">
				<p className="text-[11px] font-medium text-(--text-muted)">{label}</p>
				<p
					className={cn(
						"mt-auto truncate font-semibold text-(--text-primary)",
						muted ? "text-[13px]" : "text-sm",
					)}
				>
					{value}
				</p>
				{sublabel && (
					<p className="truncate text-[11px] text-(--text-muted)">{sublabel}</p>
				)}
			</div>
		</div>
	);
}
