// Search + category filter + sort row for the Vocabulary page.
//
// Extracted from the former monolithic ``pages/Vocabulary.tsx``. Only
// shown when there are entries to filter/sort (otherwise the empty-
// state CTA is the only meaningful action) — the parent decides whether
// to render this.

import { SearchField } from "@/components/common/SearchField";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { t } from "@/i18n/i18n";

import { CATEGORIES } from "../lib/categories";
import type { VocabSortOrder } from "../lib/sort";

interface VocabSearchFilterBarProps {
	searchQuery: string;
	onSearchChange: (value: string) => void;
	categoryFilter: string;
	onCategoryFilterChange: (value: string) => void;
	sortOrder: VocabSortOrder;
	onSortOrderChange: (value: VocabSortOrder) => void;
	categoryLabels: Record<
		string,
		{ label: string; description: string; example: string }
	>;
}

export function VocabSearchFilterBar({
	searchQuery,
	onSearchChange,
	categoryFilter,
	onCategoryFilterChange,
	sortOrder,
	onSortOrderChange,
	categoryLabels,
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
			<Select value={categoryFilter} onValueChange={onCategoryFilterChange}>
				<SelectTrigger
					size="sm"
					aria-label={t("vocabulary.filterByCategoryAria")}
					className="gap-2 h-9 rounded-xl border-border px-3 text-xs text-(--text-muted) hover:text-(--text-primary)"
				>
					<SelectValue />
				</SelectTrigger>
				<SelectContent>
					<SelectItem value="all">{t("vocabulary.allCategories")}</SelectItem>
					{CATEGORIES.map((cat) => (
						<SelectItem key={cat} value={cat}>
							{categoryLabels[cat]?.label ?? cat}
						</SelectItem>
					))}
				</SelectContent>
			</Select>
			<Select
				value={sortOrder}
				onValueChange={(v) => onSortOrderChange(v as VocabSortOrder)}
			>
				<SelectTrigger
					size="sm"
					aria-label={t("common.sortAria")}
					className="gap-2 h-9 rounded-xl border-border px-3 text-xs text-(--text-muted) hover:text-(--text-primary)"
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
