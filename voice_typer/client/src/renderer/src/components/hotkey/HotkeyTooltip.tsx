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
	keys?: string;
	/** Preferred tooltip side. Radix flips automatically when there's
	 *  no room. Defaults to "bottom". */
	side?: "top" | "bottom" | "left" | "right";
	disabled?: boolean;
	children: React.ReactElement;
}

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
					<span className="flex items-center gap-2 whitespace-nowrap">
						<span>{label}</span>
						{keys !== undefined && <HotkeyChips keys={keys} />}
					</span>
				</TooltipContent>
			)}
		</Tooltip>
	);
}
