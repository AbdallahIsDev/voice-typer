// Search + sort row for the Vocabulary page.
//
// The category filter was removed with the flat-list redesign — search
// now matches against the wrong/correct text only. Only shown when
// there are entries to filter/sort (otherwise the empty-state CTA is
// the only meaningful action) — the parent decides whether to render
// this.
//
// The live entry count is folded into the SEARCH PLACEHOLDER
// ("Search N corrections…", updated as entries are added/removed) —
// there is no standalone count element; the floating bulk bar covers
// the "how many are selected" case.

import { Sorting01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";

import { SearchField } from "@/components/common/SearchField";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { t } from "@/i18n/i18n";

import type { VocabSortOrder } from "../lib/sort";

interface VocabSearchFilterBarProps {
	searchQuery: string;
	onSearchChange: (value: string) => void;
	sortOrder: VocabSortOrder;
	onSortOrderChange: (value: VocabSortOrder) => void;
	/** Total entry count — folded into the search placeholder. */
	entryCount: number;
}

export function VocabSearchFilterBar({
	searchQuery,
	onSearchChange,
	sortOrder,
	onSortOrderChange,
	entryCount,
}: VocabSearchFilterBarProps) {
	return (
		<div className="mt-4 flex items-center gap-2">
			<div className="flex-1">
				<SearchField
					value={searchQuery}
					onChange={onSearchChange}
					placeholder={t("vocabulary.searchPlaceholderCount", {
						count: String(entryCount),
					})}
				/>
			</div>
			<Select
				value={sortOrder}
				onValueChange={(v) => onSortOrderChange(v as VocabSortOrder)}
			>
				{/* hideChevron: the trigger already carries the sort glyph — a
				    second chevron on the right read as visually overloaded.
				    Text colour matches the other header buttons: muted at
				    rest, full-white on hover (with the background change). */}
				<SelectTrigger
					size="sm"
					hideChevron
					aria-label={t("common.sortAria")}
					className="text-(--text-muted) transition-[color,box-shadow,background-color] hover:text-(--text-primary)"
				>
					{/* No explicit colour — the glyph inherits currentColor
					    from the trigger, so it follows the muted-at-rest /
					    white-on-hover text pattern automatically. */}
					<HugeiconsIcon
						icon={Sorting01Icon}
						strokeWidth={2}
						aria-hidden="true"
						className="size-4"
					/>
					<SelectValue />
				</SelectTrigger>
				{/* popper + align=start: the dropdown's left edge must line up
				    with the trigger's left edge. The shared default
				    (item-aligned, align=center) centers the list over the
				    trigger, which for a short label like "Newest first"
				    opened the menu visibly RIGHT of the button. Styling
				    matches the search input (same border tint, same subtle
				    surface, same radius) so the popup belongs to the page's
				    design system instead of reading as a separate floating
				    element. */}
				<SelectContent
					position="popper"
					align="start"
					className="rounded-xl border-border/10 bg-(--bg-subtle)"
				>
					<SelectItem value="newest">{t("common.sortNewest")}</SelectItem>
					<SelectItem value="oldest">{t("common.sortOldest")}</SelectItem>
					<SelectItem value="az">{t("common.sortAZ")}</SelectItem>
					<SelectItem value="za">{t("common.sortZA")}</SelectItem>
				</SelectContent>
			</Select>
		</div>
	);
}
