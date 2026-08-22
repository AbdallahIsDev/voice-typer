// Shared loading spinner.
// Replaces the duplicated `<div className="h-4 w-4 animate-spin rounded-full
// border-2 border-accent border-t-transparent" />` pattern that was
// copy-pasted across 9 pages.
//
// Usage: `<Spinner />` (default 16px), `<Spinner size={24} />` (24px),
// `<Spinner className="border-current" />` (uses current text color).
//
// The previous implementation bucketed ``size`` into a 3-rung
// Tailwind class ladder (``h-4 w-4`` / ``h-5 w-5`` / ``h-6 w-6``) and
// fell back to a dynamic ``h-[${size}px]`` string for any other value.
// Tailwind's JIT only generates classes it can statically see, so the
// dynamic interpolation silently produced NO class and the spinner
// collapsed to 0×0.  We now drive the size from an inline ``style``
// (which always wins regardless of Tailwind's purge step) and merge
// classes with ``cn()`` so consumer overrides like ``border-current``
// properly override the default ``border-accent`` via tailwind-merge.
//
// ``decorative`` prop renders a plain ``<div aria-hidden>``
// (no ``<output>``, no aria-label) for cases where the spinner sits
// inside an already-labeled button/region — avoids the nested live
// region announcing "Loading…" on top of the parent's accessible name.
//
// The DEFAULT root was previously an ``<output>`` element
// (implicit ARIA role of ``status``, which carries an implicit
// ``aria-live="polite"``). That meant every page that rendered a
// Spinner — History, Vocabulary, Templates, Microphone, Models,
// Settings, Onboarding, etc. — caused screen readers to announce
// "Loading" the moment the spinner appeared, even though in those
// contexts the spinner is incidental (not a primary status message).
// The default root is now a ``<span role="img" aria-label=...>`` —
// still has an accessible name (so AT users hear "Loading" when they
// focus it), but does NOT carry an implicit live region. Pages that
// want a status announcement (e.g. ConnectionStatusScreen while the
// backend is starting) wrap the Spinner in their own
// ``<output aria-live="polite">``. The ``decorative`` prop is
// unchanged (still renders ``<div aria-hidden>`` for nested cases
// like the refresh button in LastUpdatedIndicator).

import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

interface SpinnerProps {
	/** Diameter in pixels. Default 16. */
	size?: number;
	/** Additional class names appended to the spinner element. */
	className?: string;
	/**
	 * Optional accessible label override. When provided, this string is
	 * used as the ``aria-label`` instead of the default ``t("a11y.loading")``
	 * AND rendered as VISIBLE text next to the glyph (a labeled spinner
	 * should be readable by sighted users too, not just announced).
	 * Use this when the spinner represents a specific loading context
	 * (e.g. ``t("page.loading")``, ``t("microphone.loading")``).
	 *
	 * Ignored when ``decorative`` is true (decorative spinners are
	 * ``aria-hidden`` and have no accessible name).
	 */
	label?: string;
	/**
	 * When true, render a plain ``<div aria-hidden="true">`` instead of
	 * ``<span role="img" aria-label="Loading">``. Use this when the
	 * spinner is nested inside an element that already provides an
	 * accessible name (e.g. a labeled button) so screen-reader users
	 * don't hear "Loading…" redundantly on top of the parent's label.
	 *
	 * Default: ``false`` (renders
	 * ``<span role="img" aria-label="Loading">`` — a focusable image
	 * with an accessible name, but NO implicit live region).
	 */
	decorative?: boolean;
}

export function Spinner({
	size = 16,
	className,
	label,
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
	// Use a <span role="img"> with an accessible name
	// instead of <output>. The <output> element has an implicit
	// ARIA role of "status" (i.e. aria-live="polite"), which caused
	// every page that rendered a Spinner to announce "Loading" to
	// screen-reader users — even when the spinner was incidental to
	// the page's primary content. <span role="img"> gives the
	// spinner an accessible name (so AT users hear "Loading" when
	// they focus it) without the implicit live region. Pages that
	// need the live-region announcement (e.g. ConnectionStatusScreen)
	// wrap the Spinner in their own <output aria-live="polite">.
	//
	// When a contextual ``label`` is provided it ALSO renders as
	// visible text next to the glyph — "Loading microphones…" reads
	// better than an anonymous spinner for sighted users too. The
	// glyph span stays the FIRST element (the component's root in the
	// DOM) carrying role/aria-label/size/classes, and the label is a
	// sibling so consumer layouts (flex-centered page containers)
	// place them side by side.
	return (
		<>
			<span
				role="img"
				aria-label={label ?? t("a11y.loading")}
				className={resolvedClassName}
				style={resolvedStyle}
			/>
			{label ? (
				<span className="ml-2 text-xs text-(--text-muted)">{label}</span>
			) : null}
		</>
	);
}
