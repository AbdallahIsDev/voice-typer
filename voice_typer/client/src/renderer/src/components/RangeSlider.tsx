import { cn } from "@/lib/utils";

interface RangeSliderProps {
	value: number;
	min: number;
	max: number;
	step: number;
	onChange: (value: number) => void;
	ariaLabel: string;
	/** Unit suffix displayed after the value (e.g. "ms", "%", "Hz") */
	suffix: string;
	/** Optional wrapper className */
	className?: string;
	/** Disable the slider (greyed out, no interaction) */
	disabled?: boolean;
}

export function RangeSlider({
	value,
	min,
	max,
	step,
	onChange,
	ariaLabel,
	suffix,
	className,
	disabled = false,
}: RangeSliderProps) {
	return (
		<div className={cn("flex items-center gap-2", className)}>
			<input
				type="range"
				min={min}
				max={max}
				step={step}
				value={value}
				onChange={(e) => onChange(Number(e.target.value))}
				className="w-24"
				aria-label={ariaLabel}
				disabled={disabled}
			/>
			<span className="text-sm text-(--text-muted) w-14 tabular-nums">
				{value}
				{suffix}
			</span>
		</div>
	);
}
