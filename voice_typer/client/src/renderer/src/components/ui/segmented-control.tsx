import { cn } from "#utils";

/**
 * A single-select inline "pill" control.  Renders a compact row of buttons
 * where the active option is highlighted with the accent colour.  Designed
 * for 2–3 mutually-exclusive options where a full ``<Select>`` dropdown
 * feels heavy (e.g. Toggle vs Push-to-Talk).
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
}

export function SegmentedControl<T extends string>({
	options,
	value,
	onChange,
	ariaLabel,
	className,
}: SegmentedControlProps<T>) {
	return (
		<fieldset
			aria-label={ariaLabel}
			className={cn(
				"inline-flex items-center gap-0.5 rounded-3xl border border-transparent bg-input/50 p-0.5",
				className,
			)}
		>
			{options.map((opt) => {
				const active = opt.value === value;
				return (
					<button
						key={opt.value}
						type="button"
						aria-pressed={active}
						onClick={() => {
							if (!active) onChange(opt.value);
						}}
						className={cn(
							"rounded-3xl px-3 py-1.5 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40",
							active
								? "bg-primary text-primary-foreground shadow-sm"
								: "text-(--text-muted) hover:text-(--text-primary)",
						)}
					>
						{opt.label}
					</button>
				);
			})}
		</fieldset>
	);
}
