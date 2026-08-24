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
	 * When true, suppress the tooltip POPUP only: the `Tooltip` +
	 * `TooltipTrigger` wrappers still render, so the React element
	 * tree around {@link children} stays type-stable and the trigger
	 * never remounts across expanded/collapsed switches.
	 *
	 * This exists for the sidebar: nav buttons are wrapped in this
	 * component in BOTH states, and a conditional unmount of the whole
	 * wrapper would remount the buttons mid-transition, skipping their
	 * CSS label animations. Disabled tooltips keep their accessible
	 * name from the trigger itself (see below), so nothing is lost.
	 *
	 * @default false
	 */
	disabled?: boolean;
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
 *
 * Pass `disabled` to hide the popup while keeping the wrapper (and
 * therefore the trigger element identity) stable — see the prop doc.
 */
export function HotkeyTooltip({
	label,
	keys,
	side = "bottom",
	disabled = false,
	children,
}: HotkeyTooltipProps) {
	return (
		<Tooltip>
			<TooltipTrigger asChild>{children}</TooltipTrigger>
			{!disabled && (
				<TooltipContent side={side} align="center">
					<span className="flex items-center gap-1.5 whitespace-nowrap">
						<span>{label}</span>
						{keys !== undefined && <HotkeyChips keys={keys} />}
					</span>
				</TooltipContent>
			)}
		</Tooltip>
	);
}
