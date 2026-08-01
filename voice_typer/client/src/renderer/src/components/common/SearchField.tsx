import { Cancel01Icon, Search01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Input } from "@/components/ui/input";
import { t } from "@/i18n/i18n";

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
}

export function SearchField({
	value,
	onChange,
	placeholder = t("common.search"),
	ariaLabel,
}: SearchFieldProps) {
	// Always provide an accessible name. The magnifier icon is
	// decorative (the placeholder + aria-label convey the same meaning),
	// so it is marked aria-hidden below. Screen readers therefore
	// announce e.g. "Search settings, edit field" rather than the
	// generic "edit field" — a WCAG 2.1 SC 1.3.1 / 4.1.2 requirement.
	const resolvedAriaLabel = ariaLabel ?? t("common.search");

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
				value={value}
				onChange={handleChange}
				placeholder={placeholder}
				aria-label={resolvedAriaLabel}
				className="ps-9 pe-9 rounded-xl bg-(--bg-subtle) border-border"
			/>
			{value && (
				<button
					type="button"
					onClick={handleClear}
					aria-label={t("a11y.clearSearch")}
					className="absolute inset-e-3 top-1/2 -translate-y-1/2 text-(--text-muted) hover:text-(--text-primary) focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
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
