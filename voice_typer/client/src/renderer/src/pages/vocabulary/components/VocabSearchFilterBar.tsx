// Search + sort row for the Vocabulary page.
//
// The category filter was removed with the flat-list redesign — search
// now matches against the wrong/correct text only. Only shown when
// there are entries to filter/sort (otherwise the empty-state CTA is
// the only meaningful action) — the parent decides whether to render
// this.
//
// The live entry count ("N corrections", or "N of M corrections" while
// filtering) lives HERE in the search/filter row instead of on its own
// full-width line — a small muted label between the search field and
// the sort control.

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
	/** Pre-localized entry count label (e.g. "36 corrections"). */
	countLabel: string;
}

export function VocabSearchFilterBar({
	searchQuery,
	onSearchChange,
	sortOrder,
	onSortOrderChange,
	countLabel,
}: VocabSearchFilterBarProps) {
	return (
		<div className="mt-4 flex items-center gap-2">
			<div className="flex-1">
				<SearchField
					value={searchQuery}
					onChange={onSearchChange}
					placeholder={t("vocabulary.searchPlaceholder")}
				/>
			</div>
			<span
				data-testid="vocab-entry-count"
				className="shrink-0 text-xs font-medium text-(--text-muted)"
			>
				{countLabel}
			</span>
			<Select
				value={sortOrder}
				onValueChange={(v) => onSortOrderChange(v as VocabSortOrder)}
			>
				<SelectTrigger size="sm" aria-label={t("common.sortAria")}>
					<HugeiconsIcon
						icon={Sorting01Icon}
						strokeWidth={2}
						aria-hidden="true"
						className="size-4"
					/>
					<SelectValue />
				</SelectTrigger>
				<SelectContent>
					<SelectItem value="newest">{t("common.sortNewest")}</SelectItem>
					<SelectItem value="oldest">{t("common.sortOldest")}</SelectItem>
					<SelectItem value="az">{t("common.sortAZ")}</SelectItem>
					<SelectItem value="za">{t("common.sortZA")}</SelectItem>
				</SelectContent>
			</Select>
		</div>
	);
}
