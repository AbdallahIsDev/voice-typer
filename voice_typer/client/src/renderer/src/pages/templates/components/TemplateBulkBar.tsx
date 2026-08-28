// Bulk action bar for the templates list — a 1:1 mirror of the
// Vocabulary page's VocabBulkBar (the Templates UI is an exact copy of
// the Vocabulary UI; only the i18n keys differ).
//
// Sticky at the bottom of the viewport. The bar is a DIRECT child of
// the page column (a full-height flex column) and uses ``mt-auto`` to
// push itself to the column's bottom edge, plus ``sticky bottom-4`` to
// pin it 16px above the viewport bottom while scrolling:
//   - Short table: the column is min-h-full (viewport height), so
//     ``mt-auto`` plants the bar at the very bottom of the screen.
//   - Long table: the column outgrows the viewport; ``sticky bottom-4``
//     keeps the bar pinned while the content scrolls underneath.
// Because the bar stays inside the column's flex flow, it stays centered
// on the CONTENT (``mx-auto``) in both sidebar states — it shifts right
// with the content when the sidebar opens, exactly like the table.
//
// Contains the selected count, "Delete selected", "Export selected"
// (JSON/CSV format menu), and an explicit "Deselect all" (X) button —
// clicking the header checkbox again also clears the selection.
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
import type { ExportFormat } from "../../../../../shared/export-format";

interface TemplateBulkBarProps {
	selectedCount: number;
	onDeleteSelected: () => void;
	onExportSelected: (format: ExportFormat) => void | Promise<void>;
	onClearSelection: () => void;
}

export default function TemplateBulkBar({
	selectedCount,
	onDeleteSelected,
	onExportSelected,
	onClearSelection,
}: TemplateBulkBarProps) {
	return (
		<div
			data-testid="template-bulk-bar"
			// Border is exactly 1px — the old `ring-1 ring-foreground/5`
			// stacked a second outline on top of the border and read as a
			// thicker/inconsistent stroke.
			// Background matches the search input / table container
			// surface (--bg-subtle) so the bar reads as part of the same
			// design system.
			className="sticky bottom-4 z-20 mx-auto mt-auto flex w-fit max-w-full flex-wrap items-center gap-2 rounded-2xl border border-border/5 bg-(--bg-subtle) px-3 py-2 shadow-lg"
		>
			<span className="px-1 text-xs font-medium text-(--text-muted)">
				{t("templates.selectedCount", { count: String(selectedCount) })}
			</span>
			<Button
				variant="outline"
				size="sm"
				onClick={onDeleteSelected}
				className="gap-1.5 text-xs text-(--text-muted) hover:text-destructive hover:border-destructive/40"
			>
				<HugeiconsIcon
					icon={Delete01Icon}
					strokeWidth={2}
					aria-hidden="true"
					className="size-4"
				/>
				{t("templates.bulkDelete")}
			</Button>
			<DropdownMenu>
				<DropdownMenuTrigger asChild>
					<Button
						variant="outline"
						size="sm"
						aria-label={t("templates.exportSelected")}
						className="gap-1.5 text-xs text-(--text-muted) hover:text-(--text-primary)"
					>
						<HugeiconsIcon
							icon={Download01Icon}
							strokeWidth={2}
							aria-hidden="true"
							className="size-4"
						/>
						{t("templates.exportSelected")}
					</Button>
				</DropdownMenuTrigger>
				<DropdownMenuContent
					align="end"
					aria-label={t("templates.exportSelected")}
				>
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
				aria-label={t("templates.deselectAll")}
				title={t("templates.deselectAll")}
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
