import { LineIcon, Tick02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Checkbox as CheckboxPrimitive } from "radix-ui";
import type * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Design-system checkbox (shadcn/ui, radix-luma style — Radix Root
 * + hugeicons glyphs). Shared by every list/row that needs bulk
 * selection so header (select-all) and per-row checkboxes look
 * IDENTICAL in every state:
 *   - unchecked: transparent fill + muted border (matches the action
 *     icons' muted tone — no white fill, no bright accent)
 *   - checked: accent fill + white checkmark
 *   - indeterminate (partial selection): accent fill + white dash
 *
 * NOTE: Radix emits `data-state="checked|unchecked|indeterminate"`,
 * NOT `data-checked` — the raw registry output used `data-checked:`
 * classes that never matched, so this file uses `data-[state=...]:`
 * variants.
 */
function Checkbox({
	className,
	...props
}: React.ComponentProps<typeof CheckboxPrimitive.Root>) {
	return (
		<CheckboxPrimitive.Root
			data-slot="checkbox"
			className={cn(
				"relative flex size-4 shrink-0 items-center justify-center rounded-[5px] border border-(--text-muted)/40 bg-transparent transition-colors outline-none after:absolute after:-inset-x-3 after:-inset-y-2 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 data-[state=checked]:border-accent data-[state=checked]:bg-accent data-[state=checked]:text-white data-[state=indeterminate]:border-accent data-[state=indeterminate]:bg-accent data-[state=indeterminate]:text-white dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
				className,
			)}
			{...props}
		>
			<CheckboxPrimitive.Indicator
				data-slot="checkbox-indicator"
				className="grid place-content-center text-current transition-none [&>svg]:size-3.5"
			>
				<HugeiconsIcon
					icon={Tick02Icon}
					strokeWidth={2}
					aria-hidden="true"
					className="data-[state=indeterminate]:hidden"
				/>
				<HugeiconsIcon
					icon={LineIcon}
					strokeWidth={2}
					aria-hidden="true"
					className="hidden data-[state=indeterminate]:block"
				/>
			</CheckboxPrimitive.Indicator>
		</CheckboxPrimitive.Root>
	);
}

export { Checkbox };
