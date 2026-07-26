/**
 * NumberInputStepper — number input with custom up/down stepper buttons.
 *
 * Replaces the default browser number spinners (ugly white background,
 * cramped arrows) with themed SVG chevron buttons that match the app's
 * design system. The native spinners are hidden via CSS.
 *
 * UX-029 (restored): the component re-exports the same parse/range
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
	 * UX-029: optional callback fired when the user types a value that
	 * cannot be parsed as a number, or that falls outside [min, max].
	 * Use this to surface an inline error message in the parent form.
	 * If omitted, out-of-range values are still clamped silently (legacy
	 * behavior) but no error state is shown.
	 */
	onInvalid?: (reason: "parse" | "range" | null) => void;
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
	value,
	...props
}: NumberInputStepperProps) {
	// UX-029: track whether the current value is out-of-range so we can
	// set aria-invalid and visually mark the input. Without this the
	// user has no idea their input was rejected — values were silently
	// clamped and the input looked normal.
	const [isInvalid, setIsInvalid] = useState(false);

	// UX-029: re-validate whenever value/min/max change. This catches
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
		const syntheticEvent = {
			target: { value: String(next) },
		} as React.ChangeEvent<HTMLInputElement>;
		onChange?.(syntheticEvent);
	}, [value, step, min, onChange]);

	const isAtMin = min !== undefined && Number(value) <= min;
	const isAtMax = max !== undefined && Number(value) >= max;

	// PVT-020: keyboard accessibility. The stepper buttons are focusable
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
				const syntheticEvent = {
					target: { value: String(max) },
				} as React.ChangeEvent<HTMLInputElement>;
				onChange?.(syntheticEvent);
			} else if (e.key === "End" && min !== undefined) {
				e.preventDefault();
				const syntheticEvent = {
					target: { value: String(min) },
				} as React.ChangeEvent<HTMLInputElement>;
				onChange?.(syntheticEvent);
			}
		},
		[handleStepUp, handleStepDown, isAtMax, isAtMin, max, min, onChange],
	);

	return (
		<div className="group relative overflow-hidden rounded-3xl">
			<Input
				type="number"
				value={value}
				onChange={onChange}
				onKeyDown={handleKeyDown}
				// UX-029: set aria-invalid so screen readers announce the
				// error state, and so the destructive styling in the Input
				// className (aria-invalid:border-destructive
				// aria-invalid:ring-destructive) is applied.
				aria-invalid={isInvalid || undefined}
				className={cn(
					"pe-8 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-inner-spin-button]:m-0",
					className,
				)}
				min={min}
				max={max}
				step={step}
				{...props}
			/>
			<div
				className={cn(
					"absolute inset-e-1 top-0 flex h-full w-8 flex-col",
					"opacity-0 pointer-events-none transition-opacity duration-200",
					"group-hover:opacity-100 group-hover:pointer-events-auto",
					// PVT-020: reveal steppers whenever the input OR a stepper
					// has focus, so keyboard users can see the control they're
					// about to activate (the steppers are tabIndex={0} below).
					"group-focus-within:opacity-100 group-focus-within:pointer-events-auto",
				)}
			>
				<button
					type="button"
					tabIndex={0}
					disabled={isAtMax}
					onClick={handleStepUp}
					aria-label={t("a11y.increase")}
					className={cn(
						"flex h-1/2 items-center justify-center text-(--text-muted) transition-colors",
						"hover:text-(--text-primary)",
						"focus-visible:outline-hidden focus-visible:ring-3 focus-visible:ring-ring/30",
						"disabled:opacity-50 disabled:cursor-not-allowed",
					)}
				>
					<ArrowUpIcon />
				</button>
				<button
					type="button"
					tabIndex={0}
					disabled={isAtMin}
					onClick={handleStepDown}
					aria-label={t("a11y.decrease")}
					className={cn(
						"flex h-1/2 items-center justify-center text-(--text-muted) transition-colors",
						"hover:text-(--text-primary)",
						"focus-visible:outline-hidden focus-visible:ring-3 focus-visible:ring-ring/30",
						"disabled:opacity-50 disabled:cursor-not-allowed",
					)}
				>
					<ArrowDownIcon />
				</button>
			</div>
		</div>
	);
}

export { NumberInputStepper };
