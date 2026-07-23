import { useEffect, useRef, useState } from "react";
import { Slider } from "@/components/ui/slider";
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
	/**
	 * When true, the slider's `onChange` callback is deferred to the
	 * next "commit" event (`pointerup` / `mouseup` / `keyup` / `touchend`).
	 * During a drag the thumb moves (via a local display state) but the
	 * real `onChange` is only invoked when the user releases the pointer
	 * (or lifts the key after an arrow-key step).  Use this for settings
	 * where each `onChange` triggers an immediate IPC write — prevents
	 * a flood of `set_config` calls during a drag.
	 *
	 * Task ID 5: added for the text-size slider (each `onChange` would
	 * otherwise fire a separate `updateConfig({ text_size })` IPC call
	 * for every pixel of drag, flooding the backend and re-rendering
	 * the entire UI on every step).
	 */
	deferApply?: boolean;
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
	deferApply = false,
}: RangeSliderProps) {
	// Local "display" value used while `deferApply` is on.  During a drag
	// the shadcn Slider moves the thumb on its own (its own internal state),
	// but our React `value` prop is the *committed* value — so if we don't
	// shadow it with a local state, the thumb snaps back to the last
	// committed value on every re-render.  The local state lets the thumb
	// follow the drag while the real `onChange` is deferred to pointer-up.
	const [displayValue, setDisplayValue] = useState(value);
	const dirtyRef = useRef(false);
	// Track whether a drag is in progress to defer the commit until release.
	const isDraggingRef = useRef(false);

	// Sync local display state when the external committed value changes
	// (e.g. config reload, Ctrl+Plus keyboard shortcut, parent prop update).
	useEffect(() => {
		if (!dirtyRef.current) {
			setDisplayValue(value);
		}
	}, [value]);

	const commit = () => {
		if (!dirtyRef.current) return;
		dirtyRef.current = false;
		isDraggingRef.current = false;
		if (displayValue !== value) {
			onChange(displayValue);
		}
	};

	const renderedValue = deferApply ? displayValue : value;

	const handleValueChange = (vals: number[]) => {
		const next = vals[0];
		if (deferApply) {
			dirtyRef.current = true;
			isDraggingRef.current = true;
			setDisplayValue(next);
		} else {
			onChange(next);
		}
	};

	const handleCommit = () => {
		if (deferApply && isDraggingRef.current) {
			commit();
		}
	};

	return (
		<div className={cn("flex items-center gap-2", className)}>
			<Slider
				value={[renderedValue]}
				onValueChange={handleValueChange}
				onPointerUp={handleCommit}
				onBlur={handleCommit}
				min={min}
				max={max}
				step={step}
				aria-label={ariaLabel}
				// PVT-A11Y: aria-valuetext gives screen-reader users the same
				// "value + unit" readout that sighted users see next to the
				// thumb. Without it, SR users only hear the raw number with
				// no unit context (e.g. "50" instead of "50ms").
				aria-valuetext={`${renderedValue}${suffix}`}
				disabled={disabled}
				className="w-24 py-3"
				trackClassName="h-2"
				thumbClassName="w-6 bg-background shadow-md border-0"
			/>
			<span className="text-sm text-(--text-muted) w-14 tabular-nums">
				{renderedValue}
				{suffix}
			</span>
		</div>
	);
}
