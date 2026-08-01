import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { cn } from "#utils";
import { getLocale, isRtlLocale } from "@/i18n/i18n";

/**
 * ARIA contract for `variant="tabs"`:
 *
 * When using `variant="tabs"`, the parent MUST wrap each corresponding panel
 * in a `<div role="tabpanel" aria-labelledby={tabId} id={panelId}>`. The
 * `SegmentedControl` itself renders the `role="tablist"` container and each
 * option as a `role="tab"` with roving `tabIndex` (active=0, inactive=-1)
 * plus `aria-selected`. ArrowLeft / ArrowRight move focus between tabs.
 *
 * Failure to provide matching `role="tabpanel"` siblings breaks the WAI-ARIA
 * Tabs pattern (https://www.w3.org/WAI/ARIA/apg/patterns/tabpanel/) and
 * screen-reader users will not be able to associate tabs with their content.
 */

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
	/** Array of options to render. */
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
	/**
	 * Optional function returning the DOM id for the ``role="tab"``
	 * button corresponding to the given value. When provided, each
	 * tab button emits ``id={getTabId(opt.value)}`` and the matching
	 * ``role="tabpanel"`` sibling SHOULD set ``aria-labelledby`` to
	 * the same string. When omitted, an id is auto-derived from
	 * ``useId()`` so the contract still holds (but the parent must
	 * pass ``getPanelId`` to wire up the panel side).
	 *
	 * Only used by ``variant="tabs"``.
	 */
	getTabId?: (value: T) => string;
	/**
	 * Optional function returning the DOM id of the ``role="tabpanel"``
	 * element that corresponds to the given tab value. When provided,
	 * each tab button emits ``aria-controls={getPanelId(opt.value)}``
	 * so assistive tech can jump from tab → panel. When omitted, an id
	 * is auto-derived from ``useId()`` so ``aria-controls`` is always
	 * present (the parent SHOULD still wrap panel content in a real
	 * ``role="tabpanel"`` element with that id).
	 *
	 * Only used by ``variant="tabs"``.
	 */
	getPanelId?: (value: T) => string;
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
	getTabId,
	getPanelId,
}: SegmentedControlProps<T>) {
	const isTabs = variant === "tabs";

	// Dev-mode a11y warnings: a radiogroup/tablist with no accessible name
	// and an icon-only option without a title are invisible to screen
	// readers. Surfaces the gaps during development only.
	if (process.env.NODE_ENV !== "production") {
		if (!ariaLabel) {
			console.warn("SegmentedControl: `ariaLabel` is missing");
		}
		for (const opt of options) {
			if (!opt.label && !opt.title) {
				console.warn("SegmentedControl: icon-only option missing `title`");
				break;
			}
		}
	}

	const containerRef = useRef<HTMLDivElement>(null);
	//stable base id for the tablist. Used to derive per-tab and
	// per-panel ids when the caller doesn't pass getTabId / getPanelId,
	// so the WAI-ARIA Tabs contract (id on tab + aria-controls on tab
	// → matching id on tabpanel + aria-labelledby back) always holds.
	const baseId = useId();
	const resolveTabId = useCallback(
		(val: T) => (getTabId ? getTabId(val) : `${baseId}-tab-${val}`),
		[baseId, getTabId],
	);
	const resolvePanelId = useCallback(
		(val: T) => (getPanelId ? getPanelId(val) : `${baseId}-panel-${val}`),
		[baseId, getPanelId],
	);
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
			// `border border-border` (1 px), we must subtract the left border
			// width so the indicator pill aligns pixel-perfectly with the label.
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

	// A11Y-6: tabs variant uses the WAI-ARIA Tabs pattern with roving
	// tabindex (only the active tab is in the page tab order). ArrowLeft /
	// ArrowRight move focus between tabs and select the newly-focused tab
	// (activation follows focus — "automatic" activation model).
	//
	//ArrowRight/ArrowLeft direction is now RTL-aware. In an RTL
	// locale (Arabic), the visual order of tabs is mirrored, so the
	// "forward" direction (next tab) is to the LEFT instead of the RIGHT.
	// We XOR the key with the current locale's RTL flag so the same key
	// always moves the user toward the next visual tab:
	//   - LTR: ArrowRight → +1 (next), ArrowLeft → -1 (prev)
	//   - RTL: ArrowRight → -1 (prev), ArrowLeft → +1 (next)
	const handleTabsKeyDown = useCallback(
		(e: React.KeyboardEvent<HTMLDivElement>) => {
			if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
			e.preventDefault();
			const currentIndex = options.findIndex((o) => o.value === value);
			if (currentIndex === -1) return;
			const rtl = isRtlLocale(getLocale());
			const dir = (e.key === "ArrowRight") !== rtl ? 1 : -1;
			const nextIndex = (currentIndex + dir + options.length) % options.length;
			const next = options[nextIndex];
			if (!next) return;
			onChange(next.value);
			// Move focus to the newly-selected tab after the re-render.
			requestAnimationFrame(() => {
				labelRefs.current.get(next.value)?.focus();
			});
		},
		[onChange, options, value],
	);

	return (
		// A11Y-6: tabs variant renders role="tablist" + arrow-key
		// navigation; default variant keeps the radiogroup pattern.
		// biome-ignore lint/a11y/noStaticElementInteractions: role is always set (ternary below returns "tablist" or "radiogroup"), making this an interactive container. biome's static analysis can't follow the ternary.
		// biome-ignore lint/a11y/useAriaPropsSupportedByRole: aria-label is supported by both "tablist" and "radiogroup" roles. biome's static analysis can't follow the ternary role.
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
			role={isTabs ? "tablist" : "radiogroup"}
			aria-label={ariaLabel}
			onKeyDown={isTabs ? handleTabsKeyDown : undefined}
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
				// A11Y-6: for the tabs variant we render role="tab" with
				// roving tabIndex (active=0, inactive=-1) and aria-selected.
				// For the default variant we keep role="radio" via the
				// sr-only <input type="radio"> + parent <label>.
				if (isTabs) {
					return (
						<button
							type="button"
							key={opt.value}
							ref={
								getLabelRef(opt.value) as React.RefCallback<HTMLButtonElement>
							}
							title={opt.title}
							role="tab"
							//icon-only options get an explicit accessible name
							// (title attribute alone is unreliable in JAWS).
							aria-label={opt.title ?? opt.label}
							//WAI-ARIA Tabs contract — each tab needs a stable
							// id (so the panel can aria-labelledby it) and aria-controls
							// pointing at the matching panel id (so screen readers can
							// jump from tab → panel). Both are derived from getTabId /
							// getPanelId when the caller supplies them, otherwise from
							// useId() so the attributes are always present.
							id={resolveTabId(opt.value)}
							aria-controls={resolvePanelId(opt.value)}
							tabIndex={active ? 0 : -1}
							aria-selected={active}
							onClick={handleRadioChange}
							className={cn(
								"relative z-10 cursor-pointer font-normal outline-hidden transition-colors duration-150",
								"select-none whitespace-nowrap inline-flex items-center justify-center",
								// A11Y-1: visible focus indicator for keyboard users.
								"focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-hidden",
								"rounded-none px-3 py-2 text-[13px] font-medium",
								labelClassName,
								active && "text-(--text-primary)",
								!active && "text-(--text-muted) hover:text-(--text-primary)",
							)}
						>
							{opt.icon && (
								<HugeiconsIcon
									icon={opt.icon}
									strokeWidth={2}
									className={cn(
										"h-4 w-4 shrink-0",
										active ? "opacity-100" : "opacity-60",
										opt.label && "-ms-0.5 me-1",
									)}
								/>
							)}
							{opt.label}
						</button>
					);
				}
				return (
					<label
						key={opt.value}
						ref={getLabelRef(opt.value)}
						title={opt.title}
						className={cn(
							"relative z-10 cursor-pointer font-normal outline-hidden transition-colors duration-150",
							"select-none whitespace-nowrap inline-flex items-center justify-center",
							// A11Y-1: visible focus indicator on the wrapping label so keyboard
							// users see which segmented-control option has focus (the inner
							// <input type="radio" class="sr-only"> owns the focus, so we use
							// has-[:focus-visible] to style the parent label).
							"has-focus-visible:ring-3 has-focus-visible:ring-ring has-focus-visible:outline-hidden",
							variant === "default" &&
								"rounded-full px-2 py-1 text-[11px] tracking-wider",
							labelClassName,
							active && ["text-primary-foreground", activeClassName],
							!active && "text-(--text-muted) hover:text-(--text-primary)",
						)}
					>
						<input
							type="radio"
							// Stable useId-derived name so radio inputs within the
							// same control toggle as a group without the legacy
							// collision-prone literal "segmented-control".
							name={
								ariaLabel ||
								`segmented-control-${baseId.replace(/[^a-zA-Z0-9]/g, "")}`
							}
							checked={active}
							onChange={handleRadioChange}
							//explicit accessible name so icon-only options
							// (label === "") are announced via title.
							aria-label={opt.title ?? opt.label}
							className="sr-only"
						/>
						{opt.icon && (
							<HugeiconsIcon
								icon={opt.icon}
								strokeWidth={2}
								className={cn(
									"h-4 w-4 shrink-0",
									active ? "opacity-100" : "opacity-60",
									opt.label && "-ms-0.5 me-1",
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
