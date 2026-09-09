import type * as React from "react";

import { cn } from "#utils";
import {
	POINTER_FOCUS_CLASSES,
	usePointerFocusModality,
} from "@/hooks/usePointerFocusModality";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
	// Pointer vs keyboard focus modality — the shared C-FOCUS-3 contract
	// (see `hooks/usePointerFocusModality.ts` for the full rationale):
	// text inputs always match `:focus-visible` on click, so the
	// full-opacity ring paints on every mouse click. The modality state
	// machine suppresses it for pointer focus while keyboard/AT
	// navigation keeps the clear ring.
	const { pointerActive, pointerFocusProps } = usePointerFocusModality();

	return (
		<input
			type={type}
			data-slot="input"
			{...pointerFocusProps}
			className={cn(
				"h-8 w-full min-w-0 rounded-xl border border-transparent bg-input/50 px-3 py-2 text-base transition-[color,box-shadow,background-color] outline-hidden file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
				pointerActive
					? POINTER_FOCUS_CLASSES.pointer
					: POINTER_FOCUS_CLASSES.keyboard,
				className,
			)}
			{...props}
		/>
	);
}

export { Input };
