import type * as React from "react";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { HotkeyChips } from "./HotkeyChips";

interface HotkeyTooltipProps {
	/** Label text shown next to the hotkey chips (e.g. "Toggle sidebar"). */
	label: string;
	/**
	 * Pre-formatted hotkey string (e.g. "Ctrl+B") rendered as `Kbd`
	 * chips via {@link HotkeyChips}. Omit to render a label-only
	 * tooltip (no chips).
	 */
	keys?: string;
	/** Preferred tooltip side. Radix flips automatically when there's
	 *  no room. Defaults to "bottom". */
	side?: "top" | "bottom" | "left" | "right";
	/**
	 * The trigger element (a button). Its `aria-label` /
	 * `aria-keyshortcuts` remain the accessibility source of truth —
	 * Radix Tooltip content is excluded from the accessibility tree,
	 * so AT users keep the label text + shortcut announcement exactly
	 * as before (the chips are a visual-only affordance).
	 */
	children: React.ReactElement;
}

/**
 * Tooltip that renders a label plus the hotkey as design-system `Kbd`
 * chips — the same primitive Home's dynamic line and the Help overlay
 * use (`HotkeyChips`). Replaces the native `title` attribute for
 * hotkey tooltips: `title` is plain text and cannot contain chips.
 */
export function HotkeyTooltip({
	label,
	keys,
	side = "bottom",
	children,
}: HotkeyTooltipProps) {
	return (
		<Tooltip>
			<TooltipTrigger asChild>{children}</TooltipTrigger>
			<TooltipContent side={side} align="center">
				<span className="flex items-center gap-1.5 whitespace-nowrap">
					<span>{label}</span>
					{keys !== undefined && <HotkeyChips keys={keys} />}
				</span>
			</TooltipContent>
		</Tooltip>
	);
}
