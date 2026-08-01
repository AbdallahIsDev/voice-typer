import { Switch as SwitchPrimitive } from "radix-ui";
import type * as React from "react";

import { cn } from "#utils";

function Switch({
	className,
	size = "default",
	...props
}: React.ComponentProps<typeof SwitchPrimitive.Root> & {
	size?: "sm" | "default";
}) {
	// Dev-mode a11y warning: a switch with no accessible name is invisible
	// to screen readers. Surfaces the gap during development only.
	if (
		process.env.NODE_ENV !== "production" &&
		!props["aria-label"] &&
		!props["aria-labelledby"]
	) {
		console.warn("Switch: no `aria-label`/`aria-labelledby`");
	}

	return (
		<SwitchPrimitive.Root
			data-slot="switch"
			data-size={size}
			className={cn(
				"peer group/switch relative inline-flex shrink-0 items-center rounded-full border-2 transition-all outline-hidden after:absolute after:-inset-x-3 after:-inset-y-3 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 data-[size=default]:h-5 data-[size=default]:w-11 data-[size=sm]:h-4 data-[size=sm]:w-7 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 data-checked:border-primary/30 data-checked:bg-primary data-unchecked:border-transparent data-unchecked:bg-input/90 data-disabled:cursor-not-allowed data-disabled:opacity-50 data-disabled:pointer-events-none",
				className,
			)}
			{...props}
		>
			<SwitchPrimitive.Thumb
				data-slot="switch-thumb"
				className="pointer-events-none block rounded-full bg-background shadow-sm ring-0 transition-transform not-dark:bg-clip-padding group-data-[size=default]/switch:h-4 group-data-[size=default]/switch:w-6 group-data-[size=sm]/switch:h-3 group-data-[size=sm]/switch:w-4 data-checked:translate-x-[calc(100%-8px)] rtl:data-checked:-translate-x-[calc(100%-8px)] dark:data-checked:bg-primary-foreground data-unchecked:translate-x-0 dark:data-unchecked:bg-foreground"
			/>
		</SwitchPrimitive.Root>
	);
}

export { Switch };
