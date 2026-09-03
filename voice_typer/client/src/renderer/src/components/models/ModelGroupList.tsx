/**
 * ModelGroupList — shared list/group primitives for the Models page.
 *
 * (UI/UX overhaul 2026-08-20): the Local Models tab and the Cloud
 * Models tab previously rendered two completely different visual
 * patterns (collapsible accordion groups vs. flat always-expanded
 * cards). Both tabs now compose the SAME primitives from this file:
 *
 *   • `ModelGroupAccordion` / `ModelGroupItem` / `ModelGroupTrigger` —
 *     the collapsible group shell (header with brand logo + name, the
 *     plus/minus expand glyph from `ui/accordion.tsx`, hover
 *     affordance on the header row ONLY).
 *   • `ModelVariantRow` — a single model/plan list row (name + status
 *     badges on the left, metadata line below, actions on the right).
 *     The row itself is NOT clickable — only the action button is —
 *     so it carries no hover affordance (see point 10 of the
 *     overhaul).
 *   • `MetadataPair` — a label+value pair ("VRAM: ~512 MB", "WER:
 *     2.0%"): the LABEL renders in muted/secondary text, followed by a
 *     colon + the VALUE in primary text — visually "a named metric
 *     with a measured value".
 *   • `MetadataTag` — a standalone descriptive tag ("Multilingual",
 *     "Fast Speed") rendered as a neutral pill/badge chip, visually
 *     distinct from the label+value pairs.
 *
 * Future style updates to the group/list pattern happen HERE (one
 * place) and propagate to both tabs.
 */

import type { ComponentProps, ReactNode } from "react";
import {
	Accordion,
	AccordionContent,
	AccordionItem,
	AccordionTrigger,
} from "@/components/ui/accordion";
import { cn } from "@/lib/utils";

// ── Group shell ───────────────────────────────────────────────────────

/**
 * The accordion container shared by Local Models + Cloud Models.
 * Matches the pre-overhaul Local Models treatment
 * (rounded-xl border bg-subtle) so both tabs read as the same
 * surface.
 */
export function ModelGroupAccordion({
	className,
	...props
}: ComponentProps<typeof Accordion>) {
	return (
		<Accordion
			className={cn(
				"rounded-xl border border-border/5 bg-(--bg-subtle)",
				className,
			)}
			{...props}
		/>
	);
}

export function ModelGroupItem({
	className,
	...props
}: ComponentProps<typeof AccordionItem>) {
	return (
		<AccordionItem
			className={cn("border-border/5 data-open:bg-transparent", className)}
			{...props}
		/>
	);
}

/**
 * Group header row (brand logo + name). Clickable (expands/collapses),
 * so it carries a hover affordance. The plus/minus glyph comes from
 * `ui/accordion.tsx`; Radix supplies `aria-expanded` + `aria-controls`
 * on the trigger and the icons are aria-hidden.
 */
export function ModelGroupTrigger({
	className,
	children,
	...props
}: ComponentProps<typeof AccordionTrigger>) {
	return (
		<AccordionTrigger
			className={cn(
				"gap-2 px-4 py-2 text-sm font-semibold text-(--text-primary) hover:no-underline hover:bg-foreground/5 data-open:bg-transparent",
				className,
			)}
			{...props}
		>
			{children}
		</AccordionTrigger>
	);
}

/** The expanded-group content wrapper: rows separated by hairline dividers. */
export function ModelGroupContent({
	className,
	children,
	...props
}: ComponentProps<typeof AccordionContent>) {
	return (
		<AccordionContent
			className={cn("px-0 pb-0 divide-y divide-border/5", className)}
			{...props}
		>
			{children}
		</AccordionContent>
	);
}

// ── Single model/plan row ─────────────────────────────────────────────

export interface ModelVariantRowProps {
	/** Row heading (the model/plan display name). Rendered as an <h4>. */
	name: string;
	/** Optional badges rendered inline next to the heading. */
	headingExtra?: ReactNode;
	/** Metadata line: `MetadataPair` / `MetadataTag` children. */
	meta?: ReactNode;
	/** Right-side actions (e.g. `ModelCardActions`, a Configure button). */
	actions?: ReactNode;
}

/**
 * A single list row for one model version / cloud plan. The row is NOT
 * independently clickable — only `actions` is — so it renders no hover
 * affordance on the row itself.
 */
export function ModelVariantRow({
	name,
	headingExtra,
	meta,
	actions,
}: ModelVariantRowProps) {
	return (
		<div className="flex items-center gap-3 px-4 py-2">
			<div className="flex min-w-0 flex-1 flex-col gap-1">
				<div className="flex items-center gap-2">
					<h4
						className="truncate text-sm font-semibold text-(--text-primary)"
						title={name}
					>
						{name}
					</h4>
					{headingExtra}
				</div>
				{meta && (
					<div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
						{meta}
					</div>
				)}
			</div>
			{actions && (
				<div className="flex shrink-0 items-center gap-2">{actions}</div>
			)}
		</div>
	);
}

// ── Metadata primitives (point 6) ─────────────────────────────────────

/**
 * Label+value pair: the LABEL word renders muted/secondary, then a
 * colon, then the VALUE in the primary text color — visually a
 * "named metric with a measured value" (e.g. "VRAM: ~512 MB",
 * "WER: 2.0%").
 */
export function MetadataPair({
	label,
	value,
}: {
	label: string;
	value: string;
}) {
	return (
		<span className="inline-flex items-baseline gap-1">
			<span className="text-(--text-muted)">{label}</span>
			<span className="text-(--text-primary)">: {value}</span>
		</span>
	);
}

/**
 * Standalone descriptive tag ("Multilingual", "Fast Speed", "Cloud")
 * rendered as a small neutral pill — visually distinct from
 * label+value pairs so users can immediately tell "a characteristic"
 * from "a measured value".
 */
export function MetadataTag({
	children,
	className,
}: {
	children: ReactNode;
	className?: string;
}) {
	return (
		<span
			className={cn(
				"inline-flex items-center rounded-full border border-border/5 bg-foreground/5 px-2 py-0.5 text-[11px] font-medium leading-4 text-(--text-muted)",
				className,
			)}
		>
			{children}
		</span>
	);
}
