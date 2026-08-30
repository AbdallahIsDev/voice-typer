// Shared SortSelect — single source of truth for the "Sort by" dropdown
// used on History, Vocabulary, and Templates pages.
//
// Previously Vocabulary and Templates each had an identical inline
// <Select> block (SelectTrigger size="sm" hideChevron + muted text +
// popper content) while History used a diverged variant (no hideChevron,
// no muted tint, no popper alignment, default bg-popover). This component
// consolidates dimensions, border, typography, icon, spacing,
// hover/focus, and interaction so the three pages stay visually identical.
// Any future sort UI must reuse this component — do not create a
// page-specific Select duplicate.
//
// Design tokens (mirrors the app's outline Button / search input):
// - Trigger: rounded-4xl border-border/5 bg-background text-sm
//   (via SelectTrigger base) + muted text at rest
//   (text-(--text-muted)) with hover:text-(--text-primary) and
//   transition-[color,box-shadow,background-color]; Sorting01Icon
//   inherits currentColor so it follows the muted→bright hover.
//   hideChevron hides the generic chevron because the sort glyph
//   already communicates the control.
// - Content: position="popper" align="start" + rounded-xl
//   border-border/5 bg-(--bg-subtle) so the popup belongs to the
//   page's subtle surface instead of the generic popover ring.
//
// The SortOrder union matches the three pages' existing
// VocabSortOrder / TemplateSortOrder / HistorySortOrder types
// (all "newest" | "oldest" | "az" | "za") — keep the per-page aliases
// for backwards compat, but this is the canonical type.

import { Sorting01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";

import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { t } from "@/i18n/i18n";

export type SortOrder = "newest" | "oldest" | "az" | "za";

interface SortSelectProps {
	value: SortOrder;
	onValueChange: (value: SortOrder) => void;
}

export function SortSelect({ value, onValueChange }: SortSelectProps) {
	return (
		<Select value={value} onValueChange={(v) => onValueChange(v as SortOrder)}>
			{/* hideChevron: the trigger already carries the sort glyph — a
			    second chevron on the right is visually overloaded (Vocab/
			    Templates established this; History previously missed it). */}
			<SelectTrigger
				size="sm"
				hideChevron
				aria-label={t("common.sortAria")}
				className="text-(--text-muted) transition-[color,box-shadow,background-color] hover:text-(--text-primary)"
			>
				<HugeiconsIcon
					icon={Sorting01Icon}
					strokeWidth={2}
					aria-hidden="true"
					className="size-4"
				/>
				<SelectValue />
			</SelectTrigger>
			{/* popper + align=start: dropdown's left edge lines up with
			    trigger's left edge (default item-aligned/center opened
			    visibly right of short labels like "Newest first").
			    Styling matches search input (same border tint, subtle
			    surface, radius) so popup belongs to page design system. */}
			<SelectContent
				position="popper"
				align="start"
				className="rounded-xl border border-border/5 bg-(--bg-subtle)"
			>
				<SelectItem value="newest">{t("common.sortNewest")}</SelectItem>
				<SelectItem value="oldest">{t("common.sortOldest")}</SelectItem>
				<SelectItem value="az">{t("common.sortAZ")}</SelectItem>
				<SelectItem value="za">{t("common.sortZA")}</SelectItem>
			</SelectContent>
		</Select>
	);
}
