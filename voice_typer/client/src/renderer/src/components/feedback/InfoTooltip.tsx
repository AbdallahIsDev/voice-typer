// src/renderer/src/components/InfoTooltip.tsx

import {
	type KeyboardEvent as ReactKeyboardEvent,
	type MouseEvent as ReactMouseEvent,
	useMemo,
} from "react";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { getLocale, t, useT } from "@/i18n/i18n";
import { cn, focusRing } from "@/lib/utils";

interface InfoTooltipProps {
	/** Tooltip body text shown on hover/focus. */
	text: string;
	/**
	 * Optional label describing the field this tooltip belongs to
	 * (e.g. "VAD aggressiveness", "Noise gate threshold"). When
	 * provided, the trigger button's accessible name is composed as
	 * `t("a11y.moreInfoAbout", { label: contextLabel })` — e.g.
	 * "More info about VAD aggressiveness" — so screen-reader users
	 * hear the field name when tabbing through multiple InfoTooltips
	 * on the same page. When omitted, falls back to the generic
	 * `t("a11y.moreInfo")` ("More info") used historically.
	 *
	 * Existing callers (SettingRow,
	 * Templates.tsx) do not pass `contextLabel`, so their behaviour
	 * is unchanged. New callers SHOULD pass it to disambiguate.
	 */
	/**
	 * `"button"` (default) renders the trigger as a real `<button>` —
	 * the correct element everywhere the tooltip is NOT nested inside
	 * another interactive control.
	 *
	 * `"inline"` renders a focusable `<span tabIndex={0}>` instead, for
	 * the one layout a button cannot serve: sitting INSIDE another
	 * button (e.g. beside the label of an AccordionTrigger). Nested
	 * `<button>` is invalid DOM (React validateDOMNesting) and its
	 * activation would toggle the ancestor. The inline span carries the
	 * same aria-label, opens the tooltip on focus (Radix Tooltip opens
	 * on focus regardless of element type — radix-ui blesses exactly
	 * this span-inside-accordion-trigger composition), deliberately
	 * claims NO role (a role=button inside a button would announce
	 * nested interactive semantics), and stops click/keydown
	 * propagation so activating it never toggles the ancestor control.
	 */
	contextLabel?: string;
	triggerAs?: "button" | "inline";
}

export function InfoTooltip({
	text,
	contextLabel,
	triggerAs = "button",
}: InfoTooltipProps) {
	// Subscribe to locale changes so the aria-label re-resolves on
	// language switch (no page reload), and memoize the resolved
	// string keyed on the locale so a plain re-render (e.g. a Settings
	// section re-rendering with identical props) performs ZERO t()
	// calls — the memoisation contract.
	useT();
	const locale = getLocale();
	// When contextLabel is provided, compose
	// a specific aria-label so multiple InfoTooltips on the same
	// page are distinguishable for screen-reader users. Fall back to
	// the generic "More info" label when contextLabel is absent.
	// Resolve the label with the locale as an EXPLICIT argument so the
	// memo dependency is genuine — the label is a function of
	// (contextLabel, locale), and keying on the locale value is what
	// makes the memo recompute on language switch.
	const ariaLabel = useMemo(() => {
		// The resolved string is locale-dependent (t() reads the active
		// locale internally). Keying on `locale` makes the memo
		// recompute on language switch; referencing it here keeps the
		// dependency explicit for the exhaustive-deps linter.
		const _ = locale;
		void _;
		return contextLabel
			? t("a11y.moreInfoAbout", { label: contextLabel })
			: t("a11y.moreInfo");
	}, [contextLabel, locale]);
	// Default (`triggerAs="button"`): a real <button> so keyboard +
	// screen-reader users can focus the tooltip trigger via Tab. Radix
	// Tooltip opens on focus by default, so no custom keydown handler is
	// needed — Enter/Space on a native button is a no-op here (the
	// tooltip is already open from focus). Visual styling is overridden
	// so the button looks identical to a plain <span> (no native button
	// border/background).
	//
	// `triggerAs="inline"`: a focusable <span> for the one layout a
	// button cannot serve — inside another button (accordion trigger).
	// Same classes/aria-label/svg; NO role (nested interactive semantics
	// would confuse SRs); click + keydown propagation is stopped so
	// activating the span never toggles the ancestor control.
	//
	// Use the shared focusRing (ring-3 / ring-ring/30) for parity
	// with the design-system Button instead of the bespoke
	// ring-2 / ring-ring/50 (thinner + more opaque than the rest of the
	// app's focus rings).
	const triggerProps =
		triggerAs === "inline"
			? {
					tabIndex: 0 as const,
					onClick: (event: ReactMouseEvent) => event.stopPropagation(),
					onKeyDown: (event: ReactKeyboardEvent) => {
						// Keep Enter/Space/arrows from leaking to the wrapping
						// accordion trigger (Radix roving-focus handler).
						event.stopPropagation();
					},
				}
			: {};
	const helpGlyph = (
		<svg
			width="12"
			height="12"
			viewBox="0 0 16 16"
			fill="none"
			xmlns="http://www.w3.org/2000/svg"
			aria-hidden="true"
		>
			<circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.5" />
			<path
				d="M6.4 6C6.4 4.8 7.2 4.4 8 4.4C8.8 4.4 9.6 4.8 9.6 6C9.6 7.2 8.8 7.6 8.4 8C8.2 8.4 8 8.8 8 9.2"
				stroke="currentColor"
				strokeWidth="1.5"
				strokeLinecap="round"
				strokeLinejoin="round"
			/>
			<circle cx="8" cy="11.2" r="0.6" fill="currentColor" />
		</svg>
	);
	const triggerClassName = cn(
		"inline-flex size-4 items-center justify-center rounded-full text-(--text-muted) shrink-0 appearance-none border-0 bg-transparent p-0 cursor-help",
		focusRing,
	);
	return (
		<Tooltip>
			<TooltipTrigger asChild>
				{triggerAs === "inline" ? (
					// biome-ignore lint/a11y/useAriaPropsSupportedByRole: the aria-label IS the accessible name — the span deliberately claims no role inside the ancestor button (nested interactive semantics would confuse SRs).
					<span
						{...triggerProps}
						className={triggerClassName}
						aria-label={ariaLabel}
					>
						{helpGlyph}
					</span>
				) : (
					<button
						type="button"
						className={triggerClassName}
						aria-label={ariaLabel}
					>
						{helpGlyph}
					</button>
				)}
			</TooltipTrigger>
			<TooltipContent side="top" align="center" className="max-w-64">
				{text}
			</TooltipContent>
		</Tooltip>
	);
}
