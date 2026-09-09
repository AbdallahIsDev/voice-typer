// CollectionBulkBar — the shared floating bulk-action bar for
// collection pages (appears when 1+ rows are selected).
//
// Extracted from the 1:1 mirror pair VocabBulkBar (Vocabulary) /
// TemplateBulkBar (Templates): the two components are byte-identical
// except for the i18n keys and the `data-testid`. This shell owns the
// LAYOUT and visual tokens; the domain injects the label keys and the
// `data-testid` (page-unique, used by the pages' own test suites).
//
// Sticky at the bottom of the viewport. The bar is a DIRECT child of
// the page column (a full-height flex column) and uses `mt-auto` to
// push itself to the column's bottom edge, plus `sticky bottom-4` to
// pin it 16px above the viewport bottom while scrolling:
//   - Short table: the column is min-h-full (viewport height), so
//     `mt-auto` plants the bar at the very bottom of the screen.
//   - Long table: the column outgrows the viewport; `sticky bottom-4`
//     keeps the bar pinned while the content scrolls underneath.
// Because the bar stays inside the column's flex flow, it stays
// centered on the CONTENT (`mx-auto`) in both sidebar states — it
// shifts right with the content when the sidebar opens, exactly like
// the table.
//
// Contains the selected count, "Delete selected", "Export selected"
// (JSON/CSV format menu), and an explicit "Deselect all" (X) button —
// clicking the header checkbox again also clears the selection.
//
// No drift decision points in this pair — the two BulkBars differed
// ONLY in i18n keys and data-testid (verified by diffing both), so the
// shell is fully parameterized with required label keys + `testId`.

import {
	Cancel01Icon,
	Delete01Icon,
	Download01Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";

import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { t } from "@/i18n/i18n";
import type { ExportFormat } from "../../../../shared/export-format";

export interface CollectionBulkBarProps {
	/** Page-unique `data-testid` (the pages' own suites key their
	 *  bulk-bar assertions on it, e.g. "vocab-bulk-bar"). */
	testId: string;
	selectedCount: number;
	/** i18n key for the "N selected" count label. The key is resolved
	 *  with `{ count: String(selectedCount) }` — the locale copy owns
	 *  the plural wording. */
	selectedCountKey: string;
	onDeleteSelected: () => void;
	/** i18n key for the bulk Delete button's visible label. */
	deleteLabelKey: string;
	/** i18n key for the "Export selected" trigger — used for the
	 *  visible label AND its aria-label. */
	exportSelectedKey: string;
	onExportSelected: (format: ExportFormat) => void | Promise<void>;
	/** i18n key for the Deselect-all (X) button — used for its
	 *  aria-label AND its hover `title`. */
	deselectAllKey: string;
	onClearSelection: () => void;
}

export function CollectionBulkBar({
	testId,
	selectedCount,
	selectedCountKey,
	onDeleteSelected,
	deleteLabelKey,
	exportSelectedKey,
	onExportSelected,
	deselectAllKey,
	onClearSelection,
}: CollectionBulkBarProps) {
	return (
		<div
			data-testid={testId}
			// Border is exactly 1px — the old `ring-1 ring-foreground/5`
			// stacked a second outline on top of the border and read as a
			// thicker/inconsistent stroke. Background matches the search
			// input / table container surface (--bg-subtle) so the bar
			// reads as part of the same design system — bg-popover was a
			// separate, floating-element tone that didn't belong to any
			// other surface on the page.
			className="sticky bottom-4 z-20 mx-auto mt-auto flex w-fit max-w-full flex-wrap items-center gap-2 rounded-2xl border border-border/5 bg-(--bg-subtle) px-3 py-2 shadow-lg"
		>
			<span className="px-1 text-xs font-medium text-(--text-muted)">
				{t(selectedCountKey, { count: String(selectedCount) })}
			</span>
			<Button
				variant="outline"
				size="sm"
				onClick={onDeleteSelected}
				className="gap-2 text-xs text-(--text-muted) hover:text-destructive hover:border-destructive/40"
			>
				<HugeiconsIcon
					icon={Delete01Icon}
					strokeWidth={2}
					aria-hidden="true"
					className="size-4"
				/>
				{t(deleteLabelKey)}
			</Button>
			<DropdownMenu>
				<DropdownMenuTrigger asChild>
					<Button
						variant="outline"
						size="sm"
						aria-label={t(exportSelectedKey)}
						className="gap-2 text-xs text-(--text-muted) hover:text-(--text-primary)"
					>
						<HugeiconsIcon
							icon={Download01Icon}
							strokeWidth={2}
							aria-hidden="true"
							className="size-4"
						/>
						{t(exportSelectedKey)}
					</Button>
				</DropdownMenuTrigger>
				<DropdownMenuContent align="end" aria-label={t(exportSelectedKey)}>
					<DropdownMenuItem onSelect={() => onExportSelected("json")}>
						{t("exportFormat.json")}
					</DropdownMenuItem>
					<DropdownMenuItem onSelect={() => onExportSelected("csv")}>
						{t("exportFormat.csv")}
					</DropdownMenuItem>
				</DropdownMenuContent>
			</DropdownMenu>
			<button
				type="button"
				onClick={onClearSelection}
				aria-label={t(deselectAllKey)}
				title={t(deselectAllKey)}
				className="cursor-pointer rounded-lg p-1 text-(--text-muted) transition-colors hover:bg-foreground/10 hover:text-(--text-primary) focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
			>
				<HugeiconsIcon
					icon={Cancel01Icon}
					strokeWidth={2.25}
					aria-hidden="true"
					className="size-4"
				/>
			</button>
		</div>
	);
}
