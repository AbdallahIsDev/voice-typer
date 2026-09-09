// CollectionToolbar — the shared toolbar shell (Import / Export / Clear
// All / Sort + the primary Add action) for collection pages.
//
// Extracted from the 1:1 mirror pair VocabToolbar (Vocabulary) /
// TemplateToolbar (Templates): the two components' layout containers,
// button variants, icon choices, and class strings are byte-identical;
// only the i18n keys, the hidden input's `accept` attribute, and three
// small drift points differ. This shell owns the LAYOUT and the visual
// tokens (single-row toolbar, secondary cluster left + primary action
// right via `justify-between`, C-UI-9 Clear All hover treatment,
// C-FILTER-1 SortSelect primitive, C-UI-10 gap-* spacing on parents).
// The domain (page) owns every label: i18n keys are passed as props and
// resolved here through `t()` — the same injection pattern the shared
// `useRowSelection` hook established for the collection-page family.
//
// SINGLE-ROW TOOLBAR: the sort control joins the secondary cluster (it
// is a list-view control, same weight as the other non-primary tools);
// the primary Add action is pushed to the far right by the parent row's
// `justify-between` (with an explicit `w-full` so the row always spans
// the full column width). No divider pipes between buttons — spacing +
// the primary/secondary split carry the grouping.
//
// Full-width note: `justify-between` only distributes space when the
// flex container is WIDER than its children's combined content. The
// toolbar therefore MUST be rendered in a full-width parent (a direct
// child of the page column, NOT inside PageHeading's content-sized,
// shrink-0 action wrapper).
//
// Hidden import input: rendered once and re-used — its `value` is reset
// after each onChange by the import handler (see
// useCollectionImportExport) so re-selecting the same file fires the
// event again.
//
// ── Drift decision points (resolve at MIGRATION time, Wave 5) ──────
// The two pages shipped three toolbar-level drifts; the shell keeps
// BOTH forms possible via optional props, with defaults chosen as
// documented. Wave 5 unifies per page as a deliberate decision:
//
//   1. Add-button aria-label — Vocabulary has none (the visible text
//      label is the accessible name); Templates adds
//      `templates.addNewAria`. Prop `addAriaLabelKey?` — DEFAULT none
//      (Vocabulary form: a visible text label is the correct accessible
//      name; an aria-label that merely repeats it adds nothing).
//   2. Add-button disabled — Vocabulary disables while saving;
//      Templates never disables. Prop `addDisabled?` — DEFAULT false
//      (additive: absent prop = never disabled, both pages expressible).
//   3. Import file `accept` — Vocabulary accepts JSON+CSV, Templates
//      JSON only. REQUIRED prop `importAccept` (no default: the two
//      pages' import parsers genuinely differ in capability — a union
//      default would let Templates users pick CSV files the page cannot
//      parse).
//
// The ROW action-button drift (icon-xs vs icon-sm, title tooltips) is a
// row-renderer decision — rows stay per-domain by design; see
// CollectionListHeader's header note for the column-width invariant
// that constrains that decision.

import {
	Add01Icon,
	Delete01Icon,
	Upload01Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { RefObject } from "react";

import ExportFormatMenu from "@/components/common/ExportFormatMenu";
import type { SortOrder } from "@/components/common/SortSelect";
import { SortSelect } from "@/components/common/SortSelect";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import type { ExportFormat } from "../../../../shared/export-format";

export interface CollectionToolbarProps {
	/** Ref for the hidden `<input type="file">` (owned by the page's
	 *  import/export hook; the input element itself renders HERE once). */
	importInputRef: RefObject<HTMLInputElement | null>;
	/** OS file-picker filter, e.g. `"application/json,.json"` or
	 *  `"application/json,.json,.csv,text/csv"` — the domain's import
	 *  parser decides which formats are acceptable. See drift note 3. */
	importAccept: string;
	onImportClick: () => void;
	onImportFile: (file: File | undefined | null) => void;
	/** i18n key for the Import button's accessible name (both pages use
	 *  `common.importAria`). */
	importAriaLabelKey: string;
	/** i18n key for the Import button's visible label (both pages use
	 *  `common.import`). */
	importLabelKey: string;
	/** i18n key for the Import button's hover `title` (the expected
	 *  file-format hint — page-specific key). */
	importTitleKey: string;
	/** Export callback. The format (`ExportFormat` — `"json" | "csv"`)
	 *  is chosen by the shared ExportFormatMenu and forwarded here so
	 *  the page can pass it through to the IPC bridge. */
	onExport: (format: ExportFormat) => void | Promise<void>;
	exportDisabled: boolean;
	/** Opens the Clear-All confirmation dialog (the page gates the
	 *  destructive wipe behind its ConfirmDialog). */
	onClearAll: () => void;
	clearAllDisabled: boolean;
	/** i18n key used for BOTH the Clear All button's aria-label and its
	 *  hover `title` (both pages pass the same key for both). */
	clearAllAriaLabelKey: string;
	/** i18n key for the Clear All button's visible label. */
	clearAllLabelKey: string;
	/** i18n key for the primary Add button's accessible name. OPTIONAL —
	 *  drift decision point 1: pass a key only if the page's Add button
	 *  carries a distinguishing aria-label (Templates does; Vocabulary's
	 *  visible label is the accessible name). */
	addAriaLabelKey?: string;
	/** i18n key for the primary Add button's visible label. */
	addLabelKey: string;
	/** Disables the primary Add button (e.g. while a save is in
	 *  flight). OPTIONAL — drift decision point 2: absent = never
	 *  disabled (Templates form); pass `saving` to reproduce the
	 *  Vocabulary form. */
	addDisabled?: boolean;
	onAdd: () => void;
	/** Sort control — part of the toolbar's secondary cluster. Rendered
	 *  only when there ARE entries (sorting an empty list is
	 *  meaningless). The shared SortSelect primitive (C-FILTER-1) is
	 *  the single source of truth for the sort dropdown's visuals. */
	sortOrder: SortOrder;
	onSortOrderChange: (value: SortOrder) => void;
	hasEntries: boolean;
}

export function CollectionToolbar({
	importInputRef,
	importAccept,
	onImportClick,
	onImportFile,
	importAriaLabelKey,
	importLabelKey,
	importTitleKey,
	onExport,
	exportDisabled,
	onClearAll,
	clearAllDisabled,
	clearAllAriaLabelKey,
	clearAllLabelKey,
	addAriaLabelKey,
	addLabelKey,
	addDisabled,
	onAdd,
	sortOrder,
	onSortOrderChange,
	hasEntries,
}: CollectionToolbarProps) {
	return (
		<div className="flex w-full flex-wrap items-center justify-between gap-2">
			{/* Secondary-action group (Import / Export / Clear All + sort
				Select) — one flex container so they stay clustered on the
				left while the primary Add action is pushed to the far
				right by the parent's justify-between. */}
			<div className="flex flex-wrap items-center gap-2">
				{/* Hidden file input for the Import button. */}
				<input
					ref={importInputRef}
					type="file"
					accept={importAccept}
					className="sr-only"
					onChange={(e) => {
						const file = e.target.files?.[0];
						onImportFile(file);
					}}
					aria-hidden="true"
					tabIndex={-1}
				/>
				<Button
					variant="outline"
					size="sm"
					onClick={onImportClick}
					aria-label={t(importAriaLabelKey)}
					// Surface the expected file format schema on hover so
					// the user knows what shape the import expects without
					// trial-and-error.
					title={t(importTitleKey)}
					className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
				>
					<HugeiconsIcon
						icon={Upload01Icon}
						strokeWidth={2}
						aria-hidden="true"
						className="size-4"
					/>
					{t(importLabelKey)}
				</Button>
				<ExportFormatMenu onExport={onExport} disabled={exportDisabled} />
				<Button
					variant="outline"
					size="sm"
					onClick={onClearAll}
					disabled={clearAllDisabled}
					// Destructive affordance for the "clear every entry"
					// action (C-UI-9): at rest muted text/border like
					// Import/Export; on hover the background becomes the
					// solid destructive red used by ConfirmDialog's Clear
					// All (bg-destructive + text-destructive-foreground /
					// white icon) — the same interaction on every Clear
					// All control (Vocabulary, Templates, History) so
					// hover always reads as solid red + white, not a
					// tinted wash. The dark:hover restatement is REQUIRED:
					// the outline variant's dark:hover:bg-input/30
					// out-specifies a plain hover:bg-destructive (Tailwind
					// v4 `&:is(.dark *)`), so dark mode would hover
					// translucent grey, not solid red.
					className="gap-2 text-(--text-muted) hover:border-destructive hover:bg-destructive hover:text-destructive-foreground dark:hover:bg-destructive"
					aria-label={t(clearAllAriaLabelKey)}
					title={t(clearAllAriaLabelKey)}
				>
					<HugeiconsIcon
						icon={Delete01Icon}
						strokeWidth={2}
						aria-hidden="true"
						className="size-4"
					/>
					{t(clearAllLabelKey)}
				</Button>
				{hasEntries && (
					<SortSelect value={sortOrder} onValueChange={onSortOrderChange} />
				)}
			</div>
			{/* Primary action — filled accent button, pushed to the far end
				of the row (justify-between) so it reads as THE action on
				this page, distinct from the Import/Export/Clear All
				cluster on the left. The parent row is `justify-between`,
				so the single primary action needs no auto-margin to reach
				the right edge. */}
			<Button
				variant="default"
				size="sm"
				onClick={onAdd}
				disabled={addDisabled}
				aria-label={addAriaLabelKey ? t(addAriaLabelKey) : undefined}
				className="gap-2"
			>
				<HugeiconsIcon
					icon={Add01Icon}
					strokeWidth={2}
					aria-hidden="true"
					className="size-4"
				/>
				{t(addLabelKey)}
			</Button>
		</div>
	);
}
