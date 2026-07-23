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
//
// XA-8-L6: ``decorative`` prop renders a plain ``<div aria-hidden>``
// (no ``<output>``, no aria-label) for cases where the spinner sits
// inside an already-labeled button/region — avoids the nested live
// region announcing "Loading…" on top of the parent's accessible name.

import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

interface SpinnerProps {
	/** Diameter in pixels. Default 16. */
	size?: number;
	/** Additional class names appended to the spinner div. */
	className?: string;
	/**
	 * When true, render a plain ``<div aria-hidden="true">`` instead of
	 * ``<output aria-label="Loading">``. Use this when the spinner is
	 * nested inside an element that already provides an accessible
	 * name (e.g. a labeled button) so screen-reader users don't hear
	 * "Loading…" redundantly on top of the parent's label.
	 *
	 * Default: ``false`` (renders ``<output aria-label="Loading">`` —
	 * an aria-live region that announces the loading state).
	 */
	decorative?: boolean;
}

export function Spinner({
	size = 16,
	className,
	decorative = false,
}: SpinnerProps) {
	const resolvedClassName = cn(
		"animate-spin rounded-full border-2 border-accent border-t-transparent",
		className,
	);
	const resolvedStyle = { width: size, height: size };
	if (decorative) {
		return (
			<div
				aria-hidden="true"
				className={resolvedClassName}
				style={resolvedStyle}
			/>
		);
	}
	return (
		<output
			aria-label={t("a11y.loading")}
			className={resolvedClassName}
			style={resolvedStyle}
		/>
	);
}
