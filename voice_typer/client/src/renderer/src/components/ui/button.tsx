import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";
import type * as React from "react";

import { cn } from "#utils";

const buttonVariants = cva(
	// PERF: enumerated `transition` property list instead of
	// `transition-all`. The Button actually transitions colors
	// (hover/aria-expanded backgrounds, text, border), box-shadow
	// (focus-visible ring), opacity (disabled), and transform
	// (active:translate-y-px) — exactly Tailwind's default `transition`
	// set. `transition-all` would additionally watch every other
	// animatable property (width/height/padding changes from variant
	// swaps), promoting needless main-thread style recalculation.
	"group/button inline-flex shrink-0 items-center justify-center rounded-4xl border border-transparent bg-clip-padding text-sm font-medium leading-[1.3] whitespace-nowrap transition outline-hidden select-none cursor-pointer focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring active:not-aria-[haspopup]:translate-y-px disabled:cursor-not-allowed disabled:pointer-events-none disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
	{
		variants: {
			variant: {
				default: "bg-primary text-primary-foreground hover:bg-primary/80",
				outline:
					"border-border/10 bg-background hover:bg-muted hover:text-foreground aria-expanded:bg-muted aria-expanded:text-foreground dark:bg-transparent dark:hover:bg-input/30",
				secondary:
					"bg-secondary text-secondary-foreground hover:bg-[color-mix(in_oklch,var(--secondary),var(--foreground)_5%)] aria-expanded:bg-secondary aria-expanded:text-secondary-foreground",
				ghost:
					"hover:bg-muted hover:text-foreground aria-expanded:bg-muted aria-expanded:text-foreground dark:hover:bg-muted/50",
				destructive:
					"bg-destructive/10 text-destructive hover:bg-destructive/20 focus-visible:border-destructive/40 focus-visible:ring-destructive/20 dark:bg-destructive/20 dark:hover:bg-destructive/30 dark:focus-visible:ring-destructive/40",
				// warning variant for mid-tier destructive actions
				// (e.g. ConfirmDialog variant="warning" — skip onboarding,
				// discard draft). Less alarming than destructive (amber, not
				// red) but visually distinct from default. Uses the --warning
				// design token from the status-token palette so the tint
				// tracks the active theme (light/dark/custom).
				warning:
					"bg-warning/15 text-warning hover:bg-warning/25 focus-visible:border-warning/40 focus-visible:ring-warning/20",
				link: "text-primary underline-offset-4 hover:underline",
			},
			size: {
				default:
					// CONSISTENT BUTTON SIZING (2026-08-28): every text
					// button shares the same h-fit/w-fit box, px-3 py-1.5
					// padding, and 1.3 line-height — the old fixed
					// h-6/h-8/h-10 heights and px-2/px-2.5/px-4 paddings
					// made buttons of different sizes look inconsistent
					// app-wide. The `size` variants still form a real
					// scale: xs / sm / lg scale padding + font for dense
					// or prominent contexts, and the icon variants use
					// distinct square sizes (24/32/36/40px) so a compact
					// toolbar can request a genuinely smaller button.
					"h-fit w-fit gap-2 px-3 py-1.5",
				xs: "h-fit w-fit gap-1 px-2.5 py-1 text-xs [&_svg:not([class*='size-'])]:size-3",
				sm: "h-fit w-fit gap-1 px-3 py-1.5",
				lg: "h-fit w-fit gap-2 px-4 py-2",
				icon: "size-9",
				"icon-xs": "size-6 [&_svg:not([class*='size-'])]:size-3",
				"icon-sm": "size-8",
				"icon-lg": "size-10",
			},
		},
		defaultVariants: {
			variant: "default",
			size: "default",
		},
	},
);

function Button({
	className,
	variant = "default",
	size = "default",
	asChild = false,
	children,
	...props
}: React.ComponentProps<"button"> &
	VariantProps<typeof buttonVariants> & {
		asChild?: boolean;
	}) {
	const Comp = asChild ? Slot.Root : "button";

	// Dev-mode a11y warning: an interactive control with no accessible name
	// (no text children, no aria-label, no aria-labelledby) is invisible to
	// screen readers. Surfaces the gap during development only.
	if (
		process.env.NODE_ENV !== "production" &&
		!children &&
		!props["aria-label"] &&
		!props["aria-labelledby"]
	) {
		console.warn(
			"[renderer:Button] no `aria-label`/`aria-labelledby` or text content",
		);
	}

	return (
		<Comp
			data-slot="button"
			data-variant={variant}
			data-size={size}
			className={cn(buttonVariants({ variant, size, className }))}
			{...props}
		>
			{children}
		</Comp>
	);
}

export { Button, buttonVariants };
