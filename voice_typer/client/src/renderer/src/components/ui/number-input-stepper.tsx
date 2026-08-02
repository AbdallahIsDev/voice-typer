/**
 * NumberInputStepper — number input with custom up/down stepper buttons.
 *
 * Replaces the default browser number spinners (ugly white background,
 * cramped arrows) with themed SVG chevron buttons that match the app's
 * design system. The native spinners are hidden via CSS.
 *
 *  (restored): the component re-exports the same parse/range
 * validation API the original `NumberInput` had — `onInvalid` callback
 * fired with `"parse" | "range" | null`, and `aria-invalid` set on the
 * underlying input so screen readers announce the error state and the
 * destructive Tailwind variants in `Input` (aria-invalid:border-
 * destructive, aria-invalid:ring-destructive) light up. The previous
 * refactor (commit 3c2b5d6) replaced `NumberInput` with this component
 * but dropped the API; this restores it without re-implementing the
 * underlying input rendering (still composes `<Input>` for DRY).
 *
 * Usage:
 *   <NumberInputStepper
 *     value={String(value)}
 *     onChange={handler}
 *     onInvalid={(reason) => reason && setError(...)}
 *     min={0}
 *     max={100}
 *     step={1}
 *     className="w-20 text-center"
 *     aria-label="..."
 *   />
 */

import { useCallback, useEffect, useState } from "react";
import { cn } from "#utils";
import { Input } from "@/components/ui/input";
import { t } from "@/i18n/i18n";

export interface NumberInputStepperProps
	extends Omit<
		React.ComponentProps<typeof Input>,
		"type" | "onChange" | "onInvalid"
	> {
	/** Step increment/decrement value (default 1). */
	step?: number;
	/** Minimum value. */
	min?: number;
	/** Maximum value. */
	max?: number;
	onChange?: (e: React.ChangeEvent<HTMLInputElement>) => void;
	/**
	 * : optional callback fired when the user types a value that
	 * cannot be parsed as a number, or that falls outside [min, max].
	 * Use this to surface an inline error message in the parent form.
	 * If omitted, out-of-range values are still clamped silently (legacy
	 * behavior) but no error state is shown.
	 */
	onInvalid?: (reason: "parse" | "range" | null) => void;
	/**
	 *  / : id of the error element this input
	 * describes. When provided, the component forwards it as
	 * aria-errormessage on the underlying <input> so screen
	 * readers announce the error description. Takes precedence
	 * over a directly-passed aria-errormessage prop.
	 */
	errorId?: string;
}

function ArrowUpIcon() {
	return (
		<svg
			width="10"
			height="6"
			viewBox="0 0 10 6"
			fill="none"
			xmlns="http://www.w3.org/2000/svg"
			aria-hidden="true"
		>
			<path
				d="M1 5L5 1L9 5"
				stroke="currentColor"
				strokeWidth="1.5"
				strokeLinecap="round"
				strokeLinejoin="round"
			/>
		</svg>
	);
}

function ArrowDownIcon() {
	return (
		<svg
			width="10"
			height="6"
			viewBox="0 0 10 6"
			fill="none"
			xmlns="http://www.w3.org/2000/svg"
			aria-hidden="true"
		>
			<path
				d="M1 1L5 5L9 1"
				stroke="currentColor"
				strokeWidth="1.5"
				strokeLinecap="round"
				strokeLinejoin="round"
			/>
		</svg>
	);
}

function NumberInputStepper({
	className,
	step = 1,
	min,
	max,
	onChange,
	onInvalid,
	errorId,
	value,
	"aria-errormessage": ariaErrormessageProp,
	...props
}: NumberInputStepperProps) {
	//track whether the current value is out-of-range so we can
	// set aria-invalid and visually mark the input. Without this the
	// user has no idea their input was rejected — values were silently
	// clamped and the input looked normal.
	const [isInvalid, setIsInvalid] = useState(false);

	//re-validate whenever value/min/max change. This catches
	// both user input (value changes) and programmatic updates (e.g.
	// a config preset that lowers max below the current value).
	useEffect(() => {
		if (value === undefined || value === null || value === "") {
			setIsInvalid(false);
			onInvalid?.(null);
			return;
		}
		const num = Number(value);
		if (!Number.isFinite(num)) {
			setIsInvalid(true);
			onInvalid?.("parse");
			return;
		}
		if (min !== undefined && num < min) {
			setIsInvalid(true);
			onInvalid?.("range");
			return;
		}
		if (max !== undefined && num > max) {
			setIsInvalid(true);
			onInvalid?.("range");
			return;
		}
		setIsInvalid(false);
		onInvalid?.(null);
	}, [value, min, max, onInvalid]);

	const handleStepUp = useCallback(() => {
		const current = Number(value) || 0;
		const next = current + step;
		if (max !== undefined && next > max) return;
		// Echo the new value into the aria-live region so screen-reader
		// users hear the result of the step without re-reading the input.
		setLiveValue(String(next));
		// Create a synthetic change event
		const syntheticEvent = {
			target: { value: String(next) },
		} as React.ChangeEvent<HTMLInputElement>;
		onChange?.(syntheticEvent);
	}, [value, step, max, onChange]);

	const handleStepDown = useCallback(() => {
		const current = Number(value) || 0;
		const next = current - step;
		if (min !== undefined && next < min) return;
		setLiveValue(String(next));
		const syntheticEvent = {
			target: { value: String(next) },
		} as React.ChangeEvent<HTMLInputElement>;
		onChange?.(syntheticEvent);
	}, [value, step, min, onChange]);

	const isAtMin = min !== undefined && Number(value) <= min;
	const isAtMax = max !== undefined && Number(value) >= max;

	//keyboard accessibility. The stepper buttons are focusable
	// (tabIndex={0} below) AND the input itself responds to ArrowUp / ArrowDown
	// so keyboard-only users can increment / decrement without leaving the
	// text field. Home/End jump to max/min for parity with native number inputs.
	const handleKeyDown = useCallback(
		(e: React.KeyboardEvent<HTMLInputElement>) => {
			if (e.key === "ArrowUp") {
				e.preventDefault();
				if (!isAtMax) handleStepUp();
			} else if (e.key === "ArrowDown") {
				e.preventDefault();
				if (!isAtMin) handleStepDown();
			} else if (e.key === "Home" && max !== undefined) {
				e.preventDefault();
				setLiveValue(String(max));
				const syntheticEvent = {
					target: { value: String(max) },
				} as React.ChangeEvent<HTMLInputElement>;
				onChange?.(syntheticEvent);
			} else if (e.key === "End" && min !== undefined) {
				e.preventDefault();
				setLiveValue(String(min));
				const syntheticEvent = {
					target: { value: String(min) },
				} as React.ChangeEvent<HTMLInputElement>;
				onChange?.(syntheticEvent);
			}
		},
		[handleStepUp, handleStepDown, isAtMax, isAtMin, max, min, onChange],
	);

	//errorId takes precedence over a directly-passed aria-errormessage.
	// (The prop is destructured out of ...props so the explicit attribute
	// below is not clobbered by the later {...props} spread.)
	const effectiveAriaErrorMessage = errorId ?? ariaErrormessageProp;

	//visually-hidden aria-live region that announces
	// the new value after each step so screen-reader users
	// know the result of their action without needing to
	// read the input value aloud themselves. Starts empty and
	// is only mutated when a stepper actually fires.
	const [liveValue, setLiveValue] = useState("");

	return (
		<div className="group relative overflow-hidden rounded-3xl">
			<Input
				type="number"
				value={value}
				onChange={onChange}
				onKeyDown={handleKeyDown}
				//set aria-invalid so screen readers announce the
				// error state, and so the destructive styling in the Input
				// className (aria-invalid:border-destructive
				// aria-invalid:ring-destructive) is applied.
				aria-invalid={isInvalid || undefined}
				aria-errormessage={effectiveAriaErrorMessage}
				className={cn(
					"pe-8 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-inner-spin-button]:m-0",
					className,
				)}
				min={min}
				max={max}
				step={step}
				{...props}
			/>
			<span aria-live="polite" aria-atomic="true" className="sr-only">
				{liveValue}
			</span>
			<div
				className={cn(
					"absolute inset-e-1 top-0 flex h-full w-8 flex-col",
					"opacity-0 pointer-events-none transition-opacity duration-200",
					"group-hover:opacity-100 group-hover:pointer-events-auto",
					//reveal steppers whenever the input OR a stepper
					// has focus, so keyboard users can see the control they're
					// about to activate (the steppers are tabIndex={0} when not at
					// boundary, tabIndex={-1} when at boundary).
					"group-focus-within:opacity-100 group-focus-within:pointer-events-auto",
				)}
			>
				<button
					type="button"
					// At-boundary steppers use ``aria-disabled`` instead of the native
					// ``disabled`` attribute so they stay perceptible to screen
					// readers (which would otherwise skip disabled buttons entirely)
					// and remain activatable for the visual focus ring. ``tabIndex={-1}``
					// removes them from the tab order so keyboard users aren't trapped
					// on a no-op control. The onClick guard below preserves the
					// "doesn't activate at boundary" behaviour previously enforced by
					// the native disabled attribute.
					tabIndex={isAtMax ? -1 : 0}
					aria-disabled={isAtMax ? "true" : undefined}
					onClick={() => {
						if (isAtMax) return;
						handleStepUp();
					}}
					aria-label={t("a11y.increase")}
					className={cn(
						"flex h-1/2 items-center justify-center text-(--text-muted) transition-colors",
						"hover:text-(--text-primary)",
						"focus-visible:outline-hidden focus-visible:ring-3 focus-visible:ring-ring",
						// Mirror the previous disabled visual treatment via
						// aria-disabled (Tailwind 4 ships the ``aria-disabled:``
						// variant out of the box).
						"aria-disabled:opacity-50 aria-disabled:cursor-not-allowed",
					)}
				>
					<ArrowUpIcon />
				</button>
				<button
					type="button"
					tabIndex={isAtMin ? -1 : 0}
					aria-disabled={isAtMin ? "true" : undefined}
					onClick={() => {
						if (isAtMin) return;
						handleStepDown();
					}}
					aria-label={t("a11y.decrease")}
					className={cn(
						"flex h-1/2 items-center justify-center text-(--text-muted) transition-colors",
						"hover:text-(--text-primary)",
						"focus-visible:outline-hidden focus-visible:ring-3 focus-visible:ring-ring",
						"aria-disabled:opacity-50 aria-disabled:cursor-not-allowed",
					)}
				>
					<ArrowDownIcon />
				</button>
			</div>
		</div>
	);
}

export { NumberInputStepper };
