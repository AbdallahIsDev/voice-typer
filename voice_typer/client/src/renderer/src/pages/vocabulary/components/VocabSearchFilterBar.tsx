// Search + sort row for the Vocabulary page.
//
// The category filter was removed with the flat-list redesign — search
// now matches against the wrong/correct text only. Only shown when
// there are entries to filter/sort (otherwise the empty-state CTA is
// the only meaningful action) — the parent decides whether to render
// this.

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
}

export function VocabSearchFilterBar({
	searchQuery,
	onSearchChange,
	sortOrder,
	onSortOrderChange,
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
			<Select
				value={sortOrder}
				onValueChange={(v) => onSortOrderChange(v as VocabSortOrder)}
			>
				<SelectTrigger
					size="sm"
					aria-label={t("common.sortAria")}
					className="gap-2 h-9 rounded-xl border-border/10 px-3 text-xs text-(--text-muted) hover:text-(--text-primary)"
				>
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
