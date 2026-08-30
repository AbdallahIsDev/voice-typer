import { useCallback, useEffect, useRef, useState } from "react";
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
	 * next "commit" event (`pointerup` / `blur`).
	 * During a drag the thumb moves (via a local display state) but the
	 * real `onChange` is only invoked when the user releases the pointer
	 * (or Tabs away from the slider after an arrow-key step).  Use this
	 * for settings where each `onChange` triggers an immediate IPC
	 * write — prevents a flood of `set_config` calls during a drag.
	 *
	 * Commit handlers currently bound: `onPointerUp` (covers
	 * mouse/touch/pen release) and `onBlur` (covers keyboard-only
	 * arrow-key steps which never produce a `pointerup`). A
	 * `useEffect` cleanup also commits on unmount if the slider is
	 * torn down mid-drag (e.g. parent navigates away).
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

	const commit = useCallback(() => {
		if (!dirtyRef.current) return;
		dirtyRef.current = false;
		isDraggingRef.current = false;
		if (displayValue !== value) {
			onChange(displayValue);
		}
	}, [displayValue, value, onChange]);

	// if the component unmounts while a deferred drag is in
	// progress (dirtyRef === true), commit the pending value so the
	// parent's `onChange` actually fires. Without this, navigating
	// away mid-drag silently drops the user's last value.
	//
	// We use a ref-to-latest-commit pattern (commitRef) so the
	// unmount cleanup can read the freshest `displayValue` / `value`
	// without re-binding the effect on every render. Re-binding on
	// every render would fire the cleanup on each state change
	// (e.g. mid-drag) and prematurely reset `dirtyRef` / `isDraggingRef`.
	// With the ref pattern, the cleanup is registered once and only
	// runs when the component truly unmounts.
	const commitRef = useRef(commit);
	commitRef.current = commit;
	useEffect(() => {
		return () => {
			if (dirtyRef.current) {
				commitRef.current();
			}
		};
	}, []);

	const renderedValue = deferApply ? displayValue : value;

	const handleValueChange = (vals: number[]) => {
		// noUncheckedIndexedAccess: `vals[0]` is `number | undefined`;
		// the slider always emits at least one value, so guard + early
		// return keeps `setDisplayValue`/`onChange` happy.
		const next = vals[0];
		if (next === undefined) return;
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
			{/* LO-28: visible min endpoint label at the start of the track. */}
			<span
				className="w-12 shrink-0 text-xs tabular-nums text-(--text-muted)"
				aria-hidden="true"
			>
				{min}
				{suffix}
			</span>
			<Slider
				value={[renderedValue]}
				onValueChange={handleValueChange}
				onPointerUp={handleCommit}
				onBlur={handleCommit}
				min={min}
				max={max}
				step={step}
				aria-label={ariaLabel}
				// explicit aria-valuenow / aria-valuemin /
				// aria-valuemax so screen readers always announce the
				// numeric range. Radix Slider usually derives these
				// from its own props, but the shadcn wrapper doesn't
				// forward them — pass them explicitly so the slider
				// has a complete ARIA contract regardless of how the
				// underlying primitive evolves.
				aria-valuenow={renderedValue}
				aria-valuemin={min}
				aria-valuemax={max}
				// LO-23: aria-valuetext is generated on the THUMB (via
				// getThumbAriaValueText) so screen readers announce the
				// "value + unit" readout at the focused thumb, not on the
				// root. The root-level aria-valuetext was dropped — Radix
				// announces the thumb's value, and a root valuetext was
				// either ignored or double-announced.
				getThumbAriaValueText={(value) => `${value}${suffix}`}
				disabled={disabled}
				className="w-24 py-3"
				trackClassName="h-2"
				thumbClassName="w-6 bg-white shadow-md"
			/>
			<span className="w-14 shrink-0 text-end text-sm tabular-nums text-(--text-muted)">
				{renderedValue}
				{suffix}
			</span>
			{/* LO-28: visible max endpoint label at the end of the track. */}
			<span
				className="w-12 shrink-0 text-xs tabular-nums text-(--text-muted)"
				aria-hidden="true"
			>
				{max}
				{suffix}
			</span>
		</div>
	);
}
