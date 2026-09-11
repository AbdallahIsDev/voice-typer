import { Slider as SliderPrimitive } from "radix-ui";
import type * as React from "react";

import { cn } from "#utils";

export interface SliderProps
	extends React.ComponentProps<typeof SliderPrimitive.Root> {
	/** Additional class name for the track element */
	trackClassName?: string;
	/** Additional class name for the range (filled portion) element */
	rangeClassName?: string;
	/** Additional class name for each thumb element */
	thumbClassName?: string;
	/**
	 * Labels for each thumb, forwarded as aria-label so
	 * screen readers announce the thumb's purpose. Takes
	 * precedence over a global aria-label when present.
	 */
	thumbLabels?: string[];
	/**
	 * Callback for generating the aria-valuetext on each thumb.
	 * Receives the thumb's current numeric value and its index and
	 * should return a human-readable string (e.g. "3 decibels").
	 * Screen readers announce this at the focused thumb, which is
	 * the correct ARIA surface for slider value readouts.
	 */
	getThumbAriaValueText?: (value: number, index: number) => string;
}

function Slider({
	className,
	trackClassName,
	rangeClassName,
	thumbClassName,
	thumbLabels,
	getThumbAriaValueText,
	// Naming attributes are extracted (NOT spread onto the Root). The
	// Radix Slider root renders a role-less span: aria-label on a
	// role-less element is prohibited (axe `aria-prohibited-attr`),
	// because a generic container cannot be named. The THUMB
	// (role="slider") is the ARIA surface screen readers actually read,
	// so the name is forwarded there (fallback below) instead of the
	// root DOM. aria-labelledby is extracted for the same reason.
	"aria-label": rootAriaLabel,
	"aria-labelledby": rootAriaLabelledBy,
	...props
}: SliderProps) {
	const thumbValues = props.value ?? props.defaultValue ?? [0];
	const thumbCount = thumbValues.length;
	// Dev-mode a11y warning: a slider with no accessible name (no
	// aria-label, no aria-labelledby) and no per-thumb labels is invisible
	// to screen readers. Surfaces the gap during development only.
	if (
		process.env.NODE_ENV !== "production" &&
		!rootAriaLabel &&
		!rootAriaLabelledBy &&
		!thumbLabels
	) {
		console.warn(
			"[renderer:Slider] no `aria-label`/`aria-labelledby`/`thumbLabels`",
		);
	}
	return (
		<SliderPrimitive.Root
			data-slot="slider"
			className={cn(
				"relative flex w-full touch-none select-none items-center",
				"data-disabled:cursor-not-allowed data-disabled:opacity-50 data-disabled:pointer-events-none",
				className,
			)}
			{...props}
		>
			<SliderPrimitive.Track
				data-slot="slider-track"
				className={cn(
					"relative h-1.5 w-full grow overflow-hidden rounded-full bg-input/90",
					trackClassName,
				)}
			>
				<SliderPrimitive.Range
					data-slot="slider-range"
					className={cn("absolute h-full bg-primary", rangeClassName)}
				/>
			</SliderPrimitive.Track>
			{Array.from({ length: thumbCount }, (_, i) => (
				<SliderPrimitive.Thumb
					//restored biome-ignore, the rule fires under `preset: "recommended"`. Slider thumbs have a fixed count (one per value in props.value / props.defaultValue) and never reorder; the array index is the canonical stable key for radix-ui SliderThumb.
					// biome-ignore lint/suspicious/noArrayIndexKey: slider thumbs have a fixed count (one per value in props.value / props.defaultValue) and never reorder; the array index is the canonical stable key for radix-ui SliderThumb rendering.
					key={`thumb-${i}`}
					data-slot="slider-thumb"
					// Per-thumb aria-label. ``thumbLabels`` takes precedence
					// (multi-thumb sliders need distinct names like "Minimum" /
					// "Maximum"). When ``thumbLabels`` is absent, fall back to
					// the caller's root-level ``aria-label``/``aria-labelledby``
					// so a single-thumb slider that only sets ``aria-label``
					// still exposes an accessible name on the focusable
					// thumb. The naming attributes are intentionally NOT
					// rendered on the root span (see the extraction above) —
					// the thumb (role="slider") is the only ARIA surface.
					aria-label={thumbLabels?.[i] ?? rootAriaLabel}
					aria-labelledby={thumbLabels ? undefined : rootAriaLabelledBy}
					aria-valuetext={
						getThumbAriaValueText
							? getThumbAriaValueText(thumbValues[i] ?? 0, i)
							: undefined
					}
					className={cn(
						"block size-4 rounded-full bg-white border border-border/5 shadow-sm ring-0 transition-[box-shadow,transform] hover:scale-110 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-hidden active:scale-105",
						thumbClassName,
					)}
				/>
			))}
		</SliderPrimitive.Root>
	);
}

export { Slider };
