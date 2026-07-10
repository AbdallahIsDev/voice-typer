// UX-021: shared loading spinner.
// Replaces the duplicated `<div className="h-4 w-4 animate-spin rounded-full
// border-2 border-accent border-t-transparent" />` pattern that was
// copy-pasted across 9 pages.
//
// Usage: `<Spinner />` (default 16px), `<Spinner size={24} />` (24px),
// `<Spinner className="border-current" />` (uses current text color).

import { t } from "@/i18n/i18n";

interface SpinnerProps {
	/** Diameter in pixels. Default 16. */
	size?: number;
	/** Additional class names appended to the spinner div. */
	className?: string;
}

export function Spinner({ size = 16, className = "" }: SpinnerProps) {
	const sizeClass =
		size <= 16
			? "h-4 w-4"
			: size <= 20
				? "h-5 w-5"
				: size <= 24
					? "h-6 w-6"
					: `h-[${size}px] w-[${size}px]`;
	return (
		<output
			aria-label={t("a11y.loading")}
			className={`${sizeClass} animate-spin rounded-full border-2 border-accent border-t-transparent ${className}`}
		/>
	);
}
