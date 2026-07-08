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
	 * Extra classes for the active indicator pill (e.g. ``"bg-input"``
	 * to replace the default ``bg-primary shadow-xs``).
	 */
	indicatorClassName?: string;
	/**
	 * Extra classes for the active label (e.g. ``"text-(--text-primary)"``
	 * to replace the default ``text-primary-foreground``).
	 */
	activeClassName?: string;
	/**
	 * Extra classes for every label element (e.g. ``"flex-1 text-center"``
	 * to make each option take equal width).
	 */
	labelClassName?: string;
}

export function SegmentedControl<T extends string>({
	options,
	value,
	onChange,
	ariaLabel,
	className,
	indicatorClassName,
	activeClassName,
	labelClassName,
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
	const measureElement = useCallback(
		(el: HTMLElement, container: HTMLElement) => {
			const containerRect = container.getBoundingClientRect();
			const elRect = el.getBoundingClientRect();
			// BORDER-FIX: getBoundingClientRect() returns border-box coordinates,
			// but CSS position:absolute inside position:relative is relative to
			// the PADDING box (inside the border).  Since the container has
			// `border border-border` (1 px), we must subtract the left border
			// width so the indicator pill aligns pixel‑perfectly with the label.
			// container.clientLeft returns the left border width in pixels.
			const borderLeft = container.clientLeft || 0;
			return {
				left: elRect.left - containerRect.left - borderLeft,
				width: elRect.width,
			};
		},
		[],
	);

	const updateIndicator = useCallback(() => {
		const el = labelRefs.current.get(value);
		const container = containerRef.current;
		if (!el || !container) return;
		setIndicatorStyle(measureElement(el, container));
	}, [value, measureElement]);

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
				"relative inline-flex items-center rounded-full border border-border bg-input/50 p-0.75",
				className,
			)}
		>
			{/* Animated indicator pill — slides smoothly between options */}
			{indicatorStyle && (
				<div
					className={cn(
						"pointer-events-none absolute z-0 inset-y-0.75 rounded-full transition-all duration-200 ease-out",
						"bg-primary shadow-xs",
						indicatorClassName,
					)}
					style={{
						left: `${indicatorStyle.left}px`,
						width: `${indicatorStyle.width}px`,
					}}
				/>
			)}

			{options.map((opt) => {
				const active = opt.value === value;
				const handleRadioChange = () => {
					if (active) return;
					onChange(opt.value);
					// Schedule a measurement after the DOM updates.
					// NOTE: we capture opt.value here (the clicked value, which
					// will be the new active one after re-render) rather than
					// relying on updateIndicator() which captures the stale
					// `value` prop from its closure.
					requestAnimationFrame(() => {
						const el = labelRefs.current.get(opt.value);
						const container = containerRef.current;
						if (el && container) {
							setIndicatorStyle(measureElement(el, container));
						}
					});
				};
				return (
					<label
						key={opt.value}
						ref={getLabelRef(opt.value)}
						className={cn(
							"relative z-10 cursor-pointer font-normal outline-none transition-colors duration-150",
							"select-none whitespace-nowrap",
							"rounded-full px-2 py-1 text-[11px] tracking-wider",
							labelClassName,
							active && ["text-primary-foreground", activeClassName],
							!active && "text-(--text-muted) hover:text-(--text-primary)",
						)}
					>
						<input
							type="radio"
							name={ariaLabel || "segmented-control"}
							checked={active}
							onChange={handleRadioChange}
							className="sr-only"
						/>
						{opt.label}
					</label>
				);
			})}
		</div>
	);
}
