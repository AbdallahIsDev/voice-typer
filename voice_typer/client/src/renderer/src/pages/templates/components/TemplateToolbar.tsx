// Toolbar (Import / Export / Clear All / Add / Sort) for the Templates
// page — mirrors the Vocabulary page's VocabToolbar exactly.
//
// Extracted from the former monolithic ``pages/Templates.tsx``. The
// hidden ``<input type="file">`` for Import is rendered once here and
// re-used — its ``value`` is reset after each ``onChange`` so re-
// selecting the same file fires the event again (otherwise the OS
// picker suppresses the event if the path is unchanged).
//
// SINGLE-ROW TOOLBAR (2026-08-28): mirror of the Vocabulary toolbar.
// The sort control previously sat on its own second row, right-aligned,
// with a large empty gap where the search field used to live — an
// orphaned row that read as broken layout. Toolbar best practice (SAP
// Fiori action placement: "actions in toolbars are right-aligned";
// PatternFly: group essential actions in one toolbar with a single
// primary button) is to keep ALL controls in ONE coherent row. So:
// Import / Export / Clear All + the sort Select cluster on the left,
// and "Add Template" (the single primary action, filled accent button)
// pinned to the far right by the parent row's ``justify-between``.
// The LastUpdatedIndicator + manual refresh that used to sit in a
// separate row above the list were removed — the Vocabulary page has
// no such refresh control, and the toolbar is the single header row.
//
// Hierarchy (all four buttons carry an icon + label): Import / Export /
// Clear All are grouped as secondary outline actions in their own flex
// container, with the sort Select joining that secondary cluster (it is
// a list-view control, same weight as the other non-primary tools); Add
// Template sits OUTSIDE that group as a sibling, and the parent row
// uses ``justify-between`` (with an explicit ``w-full`` so the row
// always spans the full column width) so Add Template (the PRIMARY
// action, filled accent button) is pushed to the far right while the
// rest stay grouped together on the left. No divider pipes between
// buttons — spacing + the primary/secondary split carry the grouping.
//
// Full-width note: ``justify-between`` only distributes space when the
// flex container is WIDER than its children's combined content. The
// toolbar therefore MUST be rendered in a full-width parent (it is a
// direct child of the page column, NOT inside PageHeading's
// content-sized, shrink-0 action wrapper).

import {
	Add01Icon,
	Delete01Icon,
	Sorting01Icon,
	Upload01Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { RefObject } from "react";

import ExportFormatMenu from "@/components/common/ExportFormatMenu";
import { Button } from "@/components/ui/button";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { t } from "@/i18n/i18n";
import type { ExportFormat } from "../../../../../shared/export-format";

import type { TemplateSortOrder } from "../lib/types";

interface TemplateToolbarProps {
	importInputRef: RefObject<HTMLInputElement | null>;
	onImportClick: () => void;
	onImportFile: (file: File | undefined | null) => void;
	/**
	 * Export callback. The format (ExportFormat — `"json" | "csv"`)
	 * is chosen by the ExportFormatMenu and forwarded here so the
	 * parent can pass it through to the IPC bridge. : previously
	 * the parent's arrow function `() => doExport()` dropped the
	 * format arg, so CSV export silently behaved like JSON export.
	 */
	onExport: (format: ExportFormat) => void | Promise<void>;
	onAdd: () => void;
	exportDisabled: boolean;
	/** Opens the Clear-All confirmation dialog (wipes every template). */
	onClearAll: () => void;
	/** Disables the Clear All button when there is nothing to clear. */
	clearAllDisabled: boolean;
	/** Sort control — part of the toolbar's secondary cluster. Rendered
	 *  only when there ARE entries (sorting an empty list is meaningless). */
	sortOrder: TemplateSortOrder;
	onSortOrderChange: (value: TemplateSortOrder) => void;
	hasEntries: boolean;
}

export function TemplateToolbar({
	importInputRef,
	onImportClick,
	onImportFile,
	onExport,
	onAdd,
	exportDisabled,
	onClearAll,
	clearAllDisabled,
	sortOrder,
	onSortOrderChange,
	hasEntries,
}: TemplateToolbarProps) {
	return (
		<div className="flex w-full flex-wrap items-center justify-between gap-2">
			{/* Secondary-action group (Import / Export / Clear All + sort
                            Select) — one flex container so they stay clustered on the
                            left while the primary Add Template action is pushed to the
                            far right by the parent's justify-between. */}
			<div className="flex flex-wrap items-center gap-2">
				{/* Hidden file input for the Import button (mirrors
                                the Vocabulary pattern). */}
				<input
					ref={importInputRef}
					type="file"
					accept="application/json,.json"
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
					aria-label={t("common.importAria")}
					// Surface the expected file format
					// schema on hover so the user knows what shape
					// the import expects without trial-and-error.
					title={t("templates.importFormatHint")}
					className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
				>
					<HugeiconsIcon
						icon={Upload01Icon}
						strokeWidth={2}
						aria-hidden="true"
						className="size-4"
					/>
					{t("common.import")}
				</Button>
				<ExportFormatMenu onExport={onExport} disabled={exportDisabled} />
				<Button
					variant="outline"
					size="sm"
					onClick={onClearAll}
					disabled={clearAllDisabled}
					// Destructive affordance for the "clear every template"
					// action. At rest: muted text/border like Import/Export.
					// On hover the BACKGROUND turns red (the color change
					// lives on the fill, not the label) while the icon +
					// text brighten to the primary text colour — matching
					// the muted-at-rest → bright-on-hover pattern of the
					// other header buttons (mirrors VocabToolbar).
					className="gap-2 text-(--text-muted) hover:border-destructive hover:bg-destructive hover:text-(--text-primary)"
					aria-label={t("templates.clearAllAria")}
					title={t("templates.clearAllAria")}
				>
					<HugeiconsIcon
						icon={Delete01Icon}
						strokeWidth={2}
						aria-hidden="true"
						className="size-4"
					/>
					{t("templates.clearAll")}
				</Button>
				{/* Sort Select — a list-view control that belongs with the
                                    other secondary tools (not on a lone orphaned row).
                                    Only rendered when there are entries to sort. */}
				{hasEntries && (
					<Select
						value={sortOrder}
						onValueChange={(v) => onSortOrderChange(v as TemplateSortOrder)}
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
							className="rounded-xl border border-border/5 bg-(--bg-subtle)"
						>
							<SelectItem value="newest">{t("common.sortNewest")}</SelectItem>
							<SelectItem value="oldest">{t("common.sortOldest")}</SelectItem>
							<SelectItem value="az">{t("common.sortAZ")}</SelectItem>
							<SelectItem value="za">{t("common.sortZA")}</SelectItem>
						</SelectContent>
					</Select>
				)}
			</div>
			{/* Primary action — filled accent button, pushed to the far end
                            of the row (justify-between) so it reads as THE action on
                            this page, distinct from the Import/Export/Clear All
                            cluster on the left. `ms-auto` is the flexbox auto-margin:
                            it absorbs ALL free space ahead of it, which on a single
                            row is identical to space-between (group flush left, Add
                            Template flush right) AND on a narrow wrapped row keeps Add
                            Template pinned to the right edge instead of falling to the
                            left — auto margins are width-scaling alignment, not a
                            fixed gap, so nothing breaks at different content lengths
                            or window sizes. */}
			<Button
				variant="default"
				size="sm"
				onClick={onAdd}
				aria-label={t("templates.addNewAria")}
				className="ms-auto gap-2"
			>
				<HugeiconsIcon
					icon={Add01Icon}
					strokeWidth={2}
					aria-hidden="true"
					className="size-4"
				/>
				{t("templates.addTemplate")}
			</Button>
		</div>
	);
}
