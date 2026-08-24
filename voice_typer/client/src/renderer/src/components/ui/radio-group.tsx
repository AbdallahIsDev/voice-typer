import { RadioGroup as RadioGroupPrimitive } from "radix-ui";
import type * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Design-system radio group (shadcn/ui, radix-luma style — Radix Root +
 * Item/Indicator, mirroring `checkbox.tsx`). The selected state is a
 * filled inner dot on the accent fill (`--accent` maps to
 * `var(--primary)` in every theme), matching the checkbox's checked
 * treatment so the two controls read as siblings.
 *
 * NOTE: Radix emits `data-state="checked|unchecked"` (NOT
 * `data-checked`) — same convention as checkbox.tsx.
 */
function RadioGroup({
	className,
	...props
}: React.ComponentProps<typeof RadioGroupPrimitive.Root>) {
	return (
		<RadioGroupPrimitive.Root
			data-slot="radio-group"
			className={cn("grid gap-3", className)}
			{...props}
		/>
	);
}

function RadioGroupItem({
	className,
	...props
}: React.ComponentProps<typeof RadioGroupPrimitive.Item>) {
	return (
		<RadioGroupPrimitive.Item
			data-slot="radio-group-item"
			className={cn(
				"relative flex size-4 shrink-0 items-center justify-center rounded-full border border-(--text-muted)/40 bg-transparent transition-colors outline-none after:absolute after:-inset-x-3 after:-inset-y-2 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 data-[state=checked]:border-accent dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
				className,
			)}
			{...props}
		>
			<RadioGroupPrimitive.Indicator
				data-slot="radio-group-indicator"
				className="grid place-content-center text-current transition-none"
			>
				<span
					data-slot="radio-group-indicator-dot"
					aria-hidden="true"
					className="block size-2 rounded-full bg-accent"
				/>
			</RadioGroupPrimitive.Indicator>
		</RadioGroupPrimitive.Item>
	);
}

export { RadioGroup, RadioGroupItem };
