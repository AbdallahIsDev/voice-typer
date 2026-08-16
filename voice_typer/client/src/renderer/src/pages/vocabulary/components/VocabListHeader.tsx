// Column header row for the vocabulary list.
//
// Sticky on scroll (sticks to the top of the page scroll container),
// with a solid background so rows scroll underneath it. Aligned to the
// same grid as VocabListRow (checkbox | original | corrected | actions)
// so headers line up with cells. The leading cell hosts a select-all
// checkbox (indeterminate when only some of the visible rows are
// selected) — the SAME Checkbox component the rows use, so the header
// and per-row checkboxes look identical in every state (unchecked,
// checked, indeterminate dash).

import { Checkbox } from "@/components/ui/checkbox";
import { t } from "@/i18n/i18n";

interface VocabListHeaderProps {
	visibleIds: string[];
	selectedIds: ReadonlySet<string>;
	onSelectAll: (ids: string[], selected: boolean) => void;
}

export function VocabListHeader({
	visibleIds,
	selectedIds,
	onSelectAll,
}: VocabListHeaderProps) {
	const allSelected =
		visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id));
	const someSelected =
		!allSelected && visibleIds.some((id) => selectedIds.has(id));

	return (
		<div
			data-testid="vocab-list-header"
			className="sticky top-0 z-10 grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 border-b border-border/10 bg-(--bg-subtle)/95 px-3.5 py-2 text-[11px] font-semibold uppercase tracking-wider text-(--text-muted) backdrop-blur-sm sm:grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)_auto]"
		>
			{/* Radix Checkbox takes `checked="indeterminate"` for the
			    partial-selection state (renders the dash). */}
			<Checkbox
				checked={someSelected ? "indeterminate" : allSelected}
				onCheckedChange={() => onSelectAll(visibleIds, !allSelected)}
				aria-label={t("vocabulary.selectAll")}
			/>
			<span className="ps-0.5">{t("vocabulary.columnOriginal")}</span>
			<span className="ps-0.5">{t("vocabulary.columnCorrected")}</span>
			<span className="justify-self-end ps-0.5">
				{t("vocabulary.columnActions")}
			</span>
		</div>
	);
}
