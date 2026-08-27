import { Cancel01Icon, Search01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useState } from "react";
import { Input } from "@/components/ui/input";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

interface SearchFieldProps {
	value: string;
	onChange: (value: string) => void;
	placeholder?: string;
	/**
	 * Accessible label for the search input. Falls back to
	 * ``t("common.search")`` so the field is always announced as a
	 * search field by screen readers even when no explicit label is
	 * supplied. Callers SHOULD pass a context-specific label such as
	 * ``t("settings.searchPlaceholder")`` (the existing translation
	 * "Search settings…") to disambiguate multiple SearchFields on
	 * different pages.
	 */
	ariaLabel?: string;
	/** Extra classes merged onto the inner ``<Input>`` — lets callers
	 *  compact the field (e.g. the title-bar global search). */
	className?: string;
	/** Ref forwarded to the inner ``<Input>``, used by the title bar
	 *  to programmatically focus the global search (Ctrl+K). */
	inputRef?: React.RefObject<HTMLInputElement | null>;
}

export function SearchField({
	value,
	onChange,
	placeholder = t("common.search"),
	ariaLabel,
	className,
	inputRef,
}: SearchFieldProps) {
	// Always provide an accessible name. The magnifier icon is
	// decorative (the placeholder + aria-label convey the same meaning),
	// so it is marked aria-hidden below. Screen readers therefore
	// announce e.g. "Search settings, edit field" rather than the
	// generic "edit field" — a WCAG 2.1 SC 1.3.1 / 4.1.2 requirement.
	const resolvedAriaLabel = ariaLabel ?? t("common.search");

	// Pointer vs keyboard focus modality. Text inputs match the
	// `:focus-visible` pseudo-class on BOTH click and keyboard (browsers
	// deliberately indicate focus on text boxes "needing user input" —
	// MDN :focus-visible), so the full-opacity focus ring (WCAG 1.4.11
	// 3:1 contract, see focus-ring-contrast.test.tsx) paints on every
	// mouse click too — a heavy saturated halo around a small field.
	// The standard fix is modality tracking: a pointer interaction
	// (mouse/touch) suppresses the ring to a subtle border tint (the
	// caret already shows the field is active), while keyboard/AT
	// navigation keeps the clear ring. A subsequent Tab/arrow key resets
	// the modality so the next keyboard focus shows the ring again.
	const [pointerActive, setPointerActive] = useState(false);

	const handleChange = (e: React.ChangeEvent<HTMLInputElement>) =>
		onChange(e.target.value);

	const handleClear = () => onChange("");

	return (
		// Wrap the field in a `role="search"` landmark so SR users
		// can navigate to it via the "search" landmark shortcut. biome
		// suggests the `<search>` element instead; we keep `<div role="search">`
		// for explicit compatibility with older AT that doesn't recognize `<search>`.
		// biome-ignore lint/a11y/useSemanticElements: see comment above — keeping role="search" for AT compatibility.
		<div role="search" className="relative">
			<HugeiconsIcon
				icon={Search01Icon}
				strokeWidth={1.625}
				// decorative — the input's aria-label
				// provides the accessible name.
				aria-hidden="true"
				className="absolute inset-s-3 top-1/2 -translate-y-1/2 h-4 w-4 text-(--text-muted) pointer-events-none"
			/>
			<Input
				ref={inputRef}
				value={value}
				onChange={handleChange}
				placeholder={placeholder}
				aria-label={resolvedAriaLabel}
				onPointerDown={() => setPointerActive(true)}
				onKeyDown={(e) => {
					// A keyboard navigation key means the user reached
					// this field with the keyboard — restore the ring.
					if (e.key === "Tab" || e.key.startsWith("Arrow")) {
						setPointerActive(false);
					}
				}}
				onBlur={() => setPointerActive(false)}
				// Muted 10% frame (same as every other border in the app) —
				// a bare `border-border` would override the Input's
				// transparent border with a full-opacity line.
				className={cn(
					"ps-9 pe-9 rounded-xl bg-(--bg-subtle) border-border/10",
					pointerActive
						? // Pointer focus: no ring (the caret marks the active
							// field). A subtle border tint keeps the state
							// legible without the saturated halo.
							"focus:border-ring/60 focus-visible:ring-0"
						: // Keyboard/AT focus: the clear full-opacity ring
							// (WCAG 1.4.11 3:1) at a lighter 2px width.
							"focus-visible:ring-2 focus-visible:ring-ring",
					className,
				)}
			/>
			{value && (
				<button
					type="button"
					onClick={handleClear}
					aria-label={t("a11y.clearSearch")}
					className="absolute inset-e-3 top-1/2 -translate-y-1/2 text-(--text-muted) hover:text-(--text-primary) focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
				>
					<HugeiconsIcon
						icon={Cancel01Icon}
						strokeWidth={1.625}
						aria-hidden="true"
						className="h-4 w-4"
					/>
				</button>
			)}
		</div>
	);
}
