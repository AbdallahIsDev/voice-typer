import type * as React from "react";
import { useState } from "react";

import { cn } from "#utils";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
	// Pointer vs keyboard focus modality for text inputs.
	// Text inputs always match `:focus-visible` on click (browser heuristic:
	// "a text box needing user input has focus" — MDN :focus-visible), so
	// the full-opacity ring (WCAG 1.4.11 3:1 contract) paints on every
	// mouse click. The standard fix: track the input modality and suppress
	// the ring on pointer (mouse/touch) interaction, keeping only a subtle
	// border tint. Keyboard/AT navigation keeps the clear ring.
	const [pointerActive, setPointerActive] = useState(false);

	return (
		<input
			type={type}
			data-slot="input"
			onPointerDown={() => setPointerActive(true)}
			onKeyDown={(e) => {
				if (e.key === "Tab" || e.key.startsWith("Arrow")) {
					setPointerActive(false);
				}
			}}
			onBlur={() => setPointerActive(false)}
			className={cn(
				"h-8 w-full min-w-0 rounded-xl border border-transparent bg-input/50 px-3 py-2 text-base transition-[color,box-shadow,background-color] outline-hidden file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
				pointerActive
					? // Pointer focus: no heavy ring (the caret marks the active
						// field). A subtle border tint keeps the state legible.
						"focus:border-ring/60 focus-visible:ring-0"
					: // Keyboard/AT focus: the clear full-opacity ring
						// (WCAG 1.4.11 3:1).
						"focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring",
				className,
			)}
			{...props}
		/>
	);
}

export { Input };
