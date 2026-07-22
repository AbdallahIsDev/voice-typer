// UX-021: shared loading spinner.
// Replaces the duplicated `<div className="h-4 w-4 animate-spin rounded-full
// border-2 border-accent border-t-transparent" />` pattern that was
// copy-pasted across 9 pages.
//
// Usage: `<Spinner />` (default 16px), `<Spinner size={24} />` (24px),
// `<Spinner className="border-current" />` (uses current text color).
//
// PVT-025: the previous implementation bucketed ``size`` into a 3-rung
// Tailwind class ladder (``h-4 w-4`` / ``h-5 w-5`` / ``h-6 w-6``) and
// fell back to a dynamic ``h-[${size}px]`` string for any other value.
// Tailwind's JIT only generates classes it can statically see, so the
// dynamic interpolation silently produced NO class and the spinner
// collapsed to 0×0.  We now drive the size from an inline ``style``
// (which always wins regardless of Tailwind's purge step) and merge
// classes with ``cn()`` so consumer overrides like ``border-current``
// properly override the default ``border-accent`` via tailwind-merge.

import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

interface SpinnerProps {
	/** Diameter in pixels. Default 16. */
	size?: number;
	/** Additional class names appended to the spinner div. */
	className?: string;
}

export function Spinner({ size = 16, className }: SpinnerProps) {
	return (
		<output
			aria-label={t("a11y.loading")}
			className={cn(
				"animate-spin rounded-full border-2 border-accent border-t-transparent",
				className,
			)}
			style={{ width: size, height: size }}
		/>
	);
}
