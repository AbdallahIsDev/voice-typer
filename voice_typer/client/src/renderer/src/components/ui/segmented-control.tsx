import { useCallback, useRef, useState } from "react";
import { cn } from "#utils";

/**
 * A single-select inline "pill" control with an animated active indicator.
 * Renders a row of buttons where the active option is highlighted with a
 * sliding accent bar.  Accepts any number of options — works well for 2–3
 * mode toggles (e.g. Toggle vs Push-to-Talk) as well as tab navigation
 * in pages like Settings with 6+ sections.
 *
 * The active indicator slides smoothly between options using CSS transforms
 * with a measured width/left approach for pixel-perfect alignment.
 *
 * Accessibility: the container is a ``role="radiogroup"`` and each option
 * is a ``role="radio"`` with ``aria-checked`` reflecting the active state.
 * Keyboard users tab between options and press Enter / Space to select
 * (the underlying ``<button>`` element handles this natively).
 */
export interface SegmentedControlOption<T extends string> {
	/** Stored value (e.g. ``"toggle"``). */
	value: T;
	/** Visible label. */
	label: string;
}

export interface SegmentedControlProps<T extends string> {
	options: SegmentedControlOption<T>[];
	/** Currently-selected value. */
	value: T;
	/** Called with the new value when the user clicks an option. */
	onChange: (value: T) => void;
	/** Optional ``aria-label`` for the radiogroup container. */
	ariaLabel?: string;
	/** Optional wrapper className. */
	className?: string;
	/**
	 * Visual variant.
	 * - ``"default"`` — compact inline pill (``h-8``, ``text-xs``, border).
	 * - ``"tabs"`` — larger navigation tabs (``h-10``, ``text-sm``, more padding).
	 * @default "default"
	 */
	variant?: "default" | "tabs";
}

export function SegmentedControl<T extends string>({
	options,
	value,
	onChange,
	ariaLabel,
	className,
	variant = "default",
}: SegmentedControlProps<T>) {
	const containerRef = useRef<HTMLDivElement>(null);
	// Store refs for each option label so we can measure their position.
	const labelRefs = useRef<Map<string, HTMLElement>>(new Map());
	// Track the measured position of the active indicator.
	const [indicatorStyle, setIndicatorStyle] = useState<{
		left: number;
		width: number;
	} | null>(null);

	const getLabelRef = useCallback(
		(optValue: T) => (el: HTMLElement | null) => {
			if (el) {
				labelRefs.current.set(optValue, el);
			} else {
				labelRefs.current.delete(optValue);
			}
		},
		[],
	);

	// Re-measure when the active value or the container size changes.
	const updateIndicator = useCallback(() => {
		const el = labelRefs.current.get(value);
		const container = containerRef.current;
		if (!el || !container) return;
		const containerRect = container.getBoundingClientRect();
		const elRect = el.getBoundingClientRect();
		setIndicatorStyle({
			left: elRect.left - containerRect.left,
			width: elRect.width,
		});
	}, [value]);

	// Use a ResizeObserver so the indicator repositions on container resize.
	const [resizeObserver] = useState(
		() =>
			new ResizeObserver(() => {
				requestAnimationFrame(() => updateIndicator());
			}),
	);

	return (
		<div
			ref={(el) => {
				if (containerRef.current !== el) {
					resizeObserver.disconnect();
					containerRef.current = el;
					if (el) {
						resizeObserver.observe(el);
						// Measure on initial mount.
						requestAnimationFrame(() => updateIndicator());
					}
				}
			}}
			role="radiogroup"
			aria-label={ariaLabel}
			className={cn(
				"relative inline-flex items-center",
				variant === "default" &&
					"rounded-xl border border-border/50 bg-input/50 p-0.5 h-8",
				variant === "tabs" && "rounded-lg bg-(--bg) p-1 h-10",
				className,
			)}
		>
			{/* Animated indicator pill — slides smoothly between options */}
			{indicatorStyle && (
				<div
					className={cn(
						"pointer-events-none absolute z-0 bg-primary shadow-xs transition-all duration-200 ease-out",
						variant === "default" && "inset-y-0.5 rounded-lg",
						variant === "tabs" && "inset-y-1 rounded-md",
					)}
					style={{
						left: `${indicatorStyle.left}px`,
						width: `${indicatorStyle.width}px`,
					}}
				/>
			)}

			{options.map((opt) => {
				const active = opt.value === value;
				return (
					<label
						key={opt.value}
						ref={getLabelRef(opt.value)}
						className={cn(
							"relative z-10 cursor-pointer font-medium outline-none transition-colors duration-150 focus-within:ring-2 focus-within:ring-ring/40",
							"select-none whitespace-nowrap",
							variant === "default" && "rounded-lg px-3 py-1 text-xs",
							variant === "tabs" && "rounded-md px-4 py-1.5 text-sm",
							active
								? "text-primary-foreground"
								: "text-(--text-muted) hover:text-(--text-primary)",
						)}
					>
						<input
							type="radio"
							name={ariaLabel || "segmented-control"}
							checked={active}
							onChange={() => {
								if (!active) {
									onChange(opt.value);
									// Schedule a measurement after the DOM updates.
									requestAnimationFrame(() => updateIndicator());
								}
							}}
							className="sr-only"
						/>
						{opt.label}
					</label>
				);
			})}
		</div>
	);
}
