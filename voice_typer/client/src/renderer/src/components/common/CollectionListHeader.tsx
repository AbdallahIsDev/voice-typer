// CollectionListHeader — the shared column-header row for collection
// lists (select-all checkbox + primary/secondary/actions columns).
//
// Extracted from the 1:1 mirror pair VocabListHeader (Vocabulary) /
// TemplateListHeader (Templates): the two components are byte-identical
// except for the i18n keys and the `data-testid`. This shell owns the
// grid, the sticky-scroll treatment, and the indeterminate
// select-all-checkbox state machine; the domain injects the label keys
// and the page-unique test id.
//
// Sticky on scroll (sticks to the top of the page scroll container),
// with a solid background so rows scroll underneath it. Aligned to the
// SAME grid as the page's row renderer (checkbox | primary | secondary
// | actions) so headers line up with cells — NO leading padding on the
// labels so header text and cell text share the exact same horizontal
// alignment. On narrow widths the secondary label stacks below the
// primary (col-start-2) exactly like the row's secondary VALUE stacks
// below the primary, so header and cells stay aligned in every
// breakpoint.
//
// sm+ alignment invariant: the ACTIONS column is a FIXED 6.25rem in
// BOTH the header and the rows (each row is its own grid container, so
// an `auto` actions column would size to that row's content — the
// header's short "Actions" label vs the rows' icon buttons would split
// the two `1fr` columns differently). A fixed column makes every row
// (and the header) split the leftover identically, so the secondary
// column starts at the same x everywhere. NOTE for the row renderers
// (which stay per-domain): the row's action-button CLUSTER must fit
// the fixed 100px — Vocabulary's three `icon-sm` buttons and
// Templates' two `icon-xs` buttons both do, but any Wave-5 unification
// of the row button size drift (icon-xs vs icon-sm) must re-check this
// budget.
//
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

export interface CollectionListHeaderProps {
	/** Page-unique `data-testid` (the pages' own suites key their
	 *  header assertions on it, e.g. "vocab-list-header"). */
	testId: string;
	/** Row ids currently rendered by the list (the page's filtered +
	 *  display-capped view). The header's select-all state derives from
	 *  THESE, not from the full data set. */
	visibleIds: string[];
	selectedIds: ReadonlySet<string>;
	/** Select (or clear) a specific set of ids — wired to the selection
	 *  hook's setSelectMany. */
	onSelectAll: (ids: string[], selected: boolean) => void;
	/** i18n key for the select-all checkbox's accessible name. */
	selectAllAriaKey: string;
	/** i18n key for the primary column label (Vocabulary "Heard as",
	 *  Templates "Trigger"). */
	primaryColumnKey: string;
	/** i18n key for the secondary column label (Vocabulary "Corrected
	 *  to", Templates "Output"). */
	secondaryColumnKey: string;
	/** i18n key for the actions column label. */
	actionsColumnKey: string;
}

export function CollectionListHeader({
	testId,
	visibleIds,
	selectedIds,
	onSelectAll,
	selectAllAriaKey,
	primaryColumnKey,
	secondaryColumnKey,
	actionsColumnKey,
}: CollectionListHeaderProps) {
	const allSelected =
		visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id));
	const someSelected =
		!allSelected && visibleIds.some((id) => selectedIds.has(id));

	return (
		<div
			data-testid={testId}
			className="sticky top-0 z-10 grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 rounded-t-xl border-b border-border/5 bg-(--bg-subtle)/95 px-3.5 py-2 text-[11px] font-semibold uppercase tracking-wide text-(--text-muted) backdrop-blur-sm sm:grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)_6.25rem]"
		>
			{/* Radix Checkbox takes `checked="indeterminate"` for the
				partial-selection state (renders the dash). */}
			<Checkbox
				checked={someSelected ? "indeterminate" : allSelected}
				onCheckedChange={() => onSelectAll(visibleIds, !allSelected)}
				aria-label={t(selectAllAriaKey)}
			/>
			<span>{t(primaryColumnKey)}</span>
			{/* col-start-2 on mobile mirrors the row layout (secondary value
				stacks below the primary); sm: back to its own column. */}
			<span className="col-start-2 sm:col-start-auto">
				{t(secondaryColumnKey)}
			</span>
			<span className="justify-self-end">{t(actionsColumnKey)}</span>
		</div>
	);
}
