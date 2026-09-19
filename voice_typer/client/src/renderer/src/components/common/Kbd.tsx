import type { ComponentProps, ElementType, ReactNode } from "react";
import { cn } from "@/lib/utils";

export const KBD_CHIP_CLASSES =
	"rounded border border-border/15 bg-(--bg-subtle) px-2 py-1 font-mono text-xs text-(--text-primary)";

interface KbdProps {
	children: ReactNode;
	as?: ElementType;
	/** Optional className override / extension. */
	className?: string;
}

export function Kbd({ children, as: Tag = "kbd", className }: KbdProps) {
	return (
		<Tag
			data-slot="kbd"
			className={cn(
				`${KBD_CHIP_CLASSES} in-data-[slot=tooltip-content]:bg-foreground/10 in-data-[slot=tooltip-content]:text-foreground`,
				className,
			)}
		>
			{children}
		</Tag>
	);
}

export function KbdGroup({ className, ...props }: ComponentProps<"kbd">) {
	return (
		<kbd
			data-slot="kbd-group"
			className={cn("inline-flex items-center gap-1", className)}
			{...props}
		/>
	);
}
