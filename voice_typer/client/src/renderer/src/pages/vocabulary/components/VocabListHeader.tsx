// Column header row for the vocabulary list.
//
// Sticky on scroll (sticks to the top of the page scroll container),
// with a solid background so rows scroll underneath it. Aligned to the
// same grid as VocabListRow (checkbox | original | corrected | actions)
// so headers line up with cells — NO leading padding on the labels so
// header text and cell text share the exact same horizontal alignment
// (the old ps-0.5 offset the labels 2px right of their columns). On
// narrow widths the "Corrected to" label stacks below "Heard as"
// (col-start-2) exactly like the row's corrected VALUE stacks below
// the original, so header and cells stay aligned in every breakpoint.
//
// sm+ alignment invariant: the ACTIONS column is a FIXED 6.25rem
// (100px — the row's three icon buttons) in BOTH the header and the
// rows. Each row is its own grid container, so an ``auto`` actions
// column would size to that row's content: the header's short
// "Actions" label (~54px) vs the rows' icon buttons (~100px) would
// split the two ``1fr`` columns differently and the "Corrected to"
// cell would start ~23px right of the row values. A fixed column
// makes every row (and the header) split the leftover identically, so
// column 3 starts at the same x everywhere.
// The leading cell hosts a select-all checkbox (indeterminate when
// only some of the visible rows are selected) — the SAME Checkbox
// component the rows use, so the header and per-row checkboxes look
// identical in every state (unchecked, checked, indeterminate dash).
//
// rounded-t-xl: when the header pins to the viewport top on scroll,
// the container's own rounded corners are off-screen above, so the
// header's own top corners must carry the radius (the container's
// overflow-clip handles the at-rest state).

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
			className="sticky top-0 z-10 grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 rounded-t-xl border-b border-border/10 bg-(--bg-subtle)/95 px-3.5 py-2 text-[11px] font-semibold uppercase tracking-wider text-(--text-muted) backdrop-blur-sm sm:grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)_6.25rem]"
		>
			{/* Radix Checkbox takes `checked="indeterminate"` for the
			    partial-selection state (renders the dash). */}
			<Checkbox
				checked={someSelected ? "indeterminate" : allSelected}
				onCheckedChange={() => onSelectAll(visibleIds, !allSelected)}
				aria-label={t("vocabulary.selectAll")}
			/>
			<span>{t("vocabulary.columnOriginal")}</span>
			{/* col-start-2 on mobile mirrors the row layout (corrected
			    value stacks below the original); sm: back to its own
			    column. */}
			<span className="col-start-2 sm:col-start-auto">
				{t("vocabulary.columnCorrected")}
			</span>
			<span className="justify-self-end">{t("vocabulary.columnActions")}</span>
		</div>
	);
}
