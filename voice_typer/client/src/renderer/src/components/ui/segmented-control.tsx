import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "#utils";

/**
 * A single-select inline "pill" control with an animated active indicator.
 * Renders a row of buttons where the active option is highlighted with a
 * sliding accent bar.  Accepts any number of options — works well for 2–3
 * mode toggles (e.g. Toggle vs Push-to-Talk) as well as tab navigation
 * in pages like Settings with 6+ sections.
 *
 * Two variants:
 * - ``"default"`` (``rounded-full`` pill container with a sliding accent
 *   pill indicator).  Used for inline setting options (recording mode,
 *   bubble position, tray click, etc.).
 * - ``"tabs"`` (no container background/border, labels sit flush with
 *   the page edge).  Used for page-level tab switches (Settings tabs).
 *   The indicator becomes a bottom-aligned bar rather than a pill.
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
	/** Optional icon displayed before the label. When only icons are needed,
	 *  set label={""} and provide the icon — the aria-label on the radiogroup
	 *  and title on each option provide screen-reader context. */
	icon?: IconSvgElement;
	/** Optional title attribute shown on hover (tooltip). */
	title?: string;
}
export interface SegmentedControlProps<T extends string> {
	options: SegmentedControlOption<T>[];
	/** Currently-selected value. */
	value: T;
	/** Called with the new value when the user clicks an option. */
	onChange: (value: T) => void;
	/**
	 * ``"default"`` — pill-shaped container with bg/border/rounded.
	 * ``"tabs"`` — no container background or border-radius. Use for
	 * full-width page-level tab bars (e.g. Settings tabs).
	 * @default "default"
	 */
	variant?: "default" | "tabs";
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
	variant = "default",
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
	// MEM-LEAK-FIX: the previous implementation created the ResizeObserver
	// via `useState(() => new ResizeObserver(...))` and only called
	// `resizeObserver.disconnect()` inside the container ref callback when
	// the ref CHANGED. React calls the ref with `null` AFTER effect
	// cleanup on unmount, but the ref-callback's `if (containerRef.current
	// !== el)` guard short-circuits when `el === null` and the ref was
	// already non-null, leaving the observer observing a detached DOM node
	// forever (memory leak). Added an explicit unmount cleanup effect
	// below that disconnects the observer on component teardown.
	const [resizeObserver] = useState(
		() =>
			new ResizeObserver(() => {
				requestAnimationFrame(() => updateIndicator());
			}),
	);

	// Unmount cleanup: disconnect the observer so it stops polling the
	// (potentially detached) DOM node and releases its callback closure.
	useEffect(() => {
		return () => {
			resizeObserver.disconnect();
		};
	}, [resizeObserver]);

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
					"rounded-full border border-border bg-input/50 p-0.75",
				variant === "tabs" && "bg-transparent border-none rounded-none p-1",
				className,
			)}
		>
			{/* Animated indicator pill — slides smoothly between options */}
			{indicatorStyle && (
				<div
					className={cn(
						"pointer-events-none absolute z-0 transition-all duration-200 ease-out",
						variant === "default" &&
							"inset-y-0.75 rounded-full bg-primary shadow-xs",
						variant === "tabs" && "inset-y-1 rounded-md bg-input",
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
						title={opt.title}
						className={cn(
							"relative z-10 cursor-pointer font-normal outline-none transition-colors duration-150",
							"select-none whitespace-nowrap inline-flex items-center justify-center",
							// A11Y-1: visible focus indicator on the wrapping label so keyboard
							// users see which segmented-control option has focus (the inner
							// <input type="radio" class="sr-only"> owns the focus, so we use
							// has-[:focus-visible] to style the parent label).
							"has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring/50 has-[:focus-visible]:outline-none",
							variant === "default" &&
								"rounded-full px-2 py-1 text-[11px] tracking-wider",
							variant === "tabs" &&
								"rounded-none px-3 py-2 text-[13px] font-medium",
							labelClassName,
							active && variant === "tabs" && "text-(--text-primary)",
							active &&
								variant !== "tabs" && [
									"text-primary-foreground",
									activeClassName,
								],
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
						{opt.icon && (
							<HugeiconsIcon
								icon={opt.icon}
								strokeWidth={2}
								className={cn(
									"h-4 w-4 shrink-0",
									active ? "opacity-100" : "opacity-60",
									opt.label && "-ml-0.5 mr-1",
								)}
							/>
						)}
						{opt.label}
					</label>
				);
			})}
		</div>
	);
}
