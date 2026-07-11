/**
 * NumberInputStepper — number input with custom up/down stepper buttons.
 *
 * Replaces the default browser number spinners (ugly white background,
 * cramped arrows) with themed SVG chevron buttons that match the app's
 * design system. The native spinners are hidden via CSS.
 *
 * Usage:
 *   <NumberInputStepper
 *     value={String(value)}
 *     onChange={handler}
 *     min={0}
 *     max={100}
 *     step={1}
 *     className="w-20 text-center"
 *     aria-label="..."
 *     aria-invalid={isInvalid || undefined}
 *   />
 */

import { useCallback } from "react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

interface NumberInputStepperProps
	extends Omit<React.ComponentProps<typeof Input>, "type"> {
	/** Step increment/decrement value (default 1). */
	step?: number;
	/** Minimum value. */
	min?: number;
	/** Maximum value. */
	max?: number;
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
	value,
	...props
}: NumberInputStepperProps) {
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

	return (
		<div className="group relative overflow-hidden rounded-3xl">
			<Input
				type="number"
				value={value}
				onChange={onChange}
				className={cn(
					"pr-8 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-inner-spin-button]:m-0",
					className,
				)}
				{...props}
			/>
			<div
				className={cn(
					"absolute right-1 top-0 flex h-full w-8 flex-col",
					"opacity-0 pointer-events-none transition-opacity duration-200",
					"group-hover:opacity-100 group-hover:pointer-events-auto",
				)}
			>
				<button
					type="button"
					tabIndex={-1}
					disabled={isAtMax}
					onClick={handleStepUp}
					aria-hidden="true"
					className={cn(
						"flex h-1/2 items-center justify-center text-(--text-muted) transition-colors",
						"hover:text-(--text-primary)",
						"disabled:opacity-30 disabled:cursor-not-allowed",
					)}
				>
					<ArrowUpIcon />
				</button>
				<button
					type="button"
					tabIndex={-1}
					disabled={isAtMin}
					onClick={handleStepDown}
					aria-hidden="true"
					className={cn(
						"flex h-1/2 items-center justify-center text-(--text-muted) transition-colors",
						"hover:text-(--text-primary)",
						"disabled:opacity-30 disabled:cursor-not-allowed",
					)}
				>
					<ArrowDownIcon />
				</button>
			</div>
		</div>
	);
}

export { NumberInputStepper };
