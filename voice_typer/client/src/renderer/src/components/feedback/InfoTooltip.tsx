// src/renderer/src/components/InfoTooltip.tsx

import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { t } from "@/i18n/i18n";

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
	 * PVT-054 (sub-agent 21): existing callers (SettingRow,
	 * Templates.tsx) do not pass `contextLabel`, so their behaviour
	 * is unchanged. New callers SHOULD pass it to disambiguate.
	 */
	contextLabel?: string;
}

export function InfoTooltip({ text, contextLabel }: InfoTooltipProps) {
	// PVT-054 (sub-agent 21): when contextLabel is provided, compose
	// a specific aria-label so multiple InfoTooltips on the same
	// page are distinguishable for screen-reader users. Fall back to
	// the generic "More info" label when contextLabel is absent.
	const ariaLabel = contextLabel
		? t("a11y.moreInfoAbout", { label: contextLabel })
		: t("a11y.moreInfo");
	// NF-R15-5: use a real <button> so keyboard + screen-reader users can
	// focus the tooltip trigger via Tab. Radix Tooltip opens on focus by
	// default, so no custom keydown handler is needed — Enter/Space on a
	// native button is a no-op here (the tooltip is already open from
	// focus). Visual styling is overridden so the button looks identical to
	// the previous <span> (no native button border/background).
	return (
		<TooltipProvider delayDuration={200}>
			<Tooltip>
				<TooltipTrigger asChild>
					<button
						type="button"
						className="inline-flex size-4 items-center justify-center rounded-full text-(--text-muted) shrink-0 appearance-none border-0 bg-transparent p-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 cursor-help"
						aria-label={ariaLabel}
					>
						<svg
							width="12"
							height="12"
							viewBox="0 0 16 16"
							fill="none"
							xmlns="http://www.w3.org/2000/svg"
							aria-hidden="true"
						>
							<title>{ariaLabel}</title>
							<circle
								cx="8"
								cy="8"
								r="6.5"
								stroke="currentColor"
								strokeWidth="1.5"
							/>
							<path
								d="M6.4 6C6.4 4.8 7.2 4.4 8 4.4C8.8 4.4 9.6 4.8 9.6 6C9.6 7.2 8.8 7.6 8.4 8C8.2 8.4 8 8.8 8 9.2"
								stroke="currentColor"
								strokeWidth="1.5"
								strokeLinecap="round"
								strokeLinejoin="round"
							/>
							<circle cx="8" cy="11.2" r="0.6" fill="currentColor" />
						</svg>
					</button>
				</TooltipTrigger>
				<TooltipContent side="top" align="center" className="max-w-64">
					{text}
				</TooltipContent>
			</Tooltip>
		</TooltipProvider>
	);
}
