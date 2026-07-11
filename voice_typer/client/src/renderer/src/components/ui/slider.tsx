import { Slider as SliderPrimitive } from "radix-ui";
import type * as React from "react";

import { cn } from "#utils";

interface SliderProps
	extends React.ComponentProps<typeof SliderPrimitive.Root> {
	/** Additional class name for the track element */
	trackClassName?: string;
	/** Additional class name for the range (filled portion) element */
	rangeClassName?: string;
	/** Additional class name for each thumb element */
	thumbClassName?: string;
}

function Slider({
	className,
	trackClassName,
	rangeClassName,
	thumbClassName,
	...props
}: SliderProps) {
	return (
		<SliderPrimitive.Root
			data-slot="slider"
			className={cn(
				"relative flex w-full touch-none select-none items-center",
				"data-disabled:cursor-not-allowed data-disabled:opacity-50",
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
			{(props.value ?? props.defaultValue ?? [0]).map((v) => (
				<SliderPrimitive.Thumb
					key={v}
					data-slot="slider-thumb"
					className={cn(
						"block size-4 rounded-full border-2 border-primary bg-background shadow-sm ring-0 transition-[box-shadow,transform] hover:scale-110 focus-visible:ring-3 focus-visible:ring-ring/30 focus-visible:outline-none active:scale-105",
						thumbClassName,
					)}
				/>
			))}
		</SliderPrimitive.Root>
	);
}

export { Slider };
