// One row of the templates list.
//
// Extracted from the former monolithic ``pages/Templates.tsx``. Each
// row is a controlled component — the parent passes the row data and
// edit/delete callbacks (so the parent's ``useTemplates`` +
// ``useTemplateDialog`` hooks remain the single source of truth and
// list re-renders don't re-create row handlers).

import { Delete01Icon, PencilEdit02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

import type { TemplateRow } from "../lib/types";

interface TemplateListRowProps {
	row: TemplateRow;
	selected: boolean;
	onToggleSelect: (id: string) => void;
	onEdit: (row: TemplateRow) => void;
	onDelete: (row: TemplateRow) => void;
}

// Wrapped in ``React.memo`` so the row only re-renders when its
// own props change (row reference, or one of the stable useCallback
// handlers).  Mirrors the ``ActivityListRow`` pattern
// (components/dashboard/ActivityList.tsx:74) — the parent (Templates.tsx)
// passes stable ``useCallback`` handlers so a search-box keystroke skips
// every row's render function.
//
// The previous inline ``handleEdit = () => onEdit(row)`` /
// ``handleDelete = () => onDelete(row)`` wrappers have been removed in
// favour of calling ``onEdit(row)`` / ``onDelete(row)`` directly in the
// button ``onClick`` (per the ActivityListRow pattern — the closure is
// per-button-per-render, same cost, but clearer and avoids the extra
// allocation per row).
export const TemplateListRow = memo(function TemplateListRow({
	row,
	selected,
	onToggleSelect,
	onEdit,
	onDelete,
}: TemplateListRowProps) {
	// Colored match-mode label: "exact" — neutral/blue,
	// "contains" — amber.  Uses the same color tokens
	// as the History favorites toggle so the palette
	// stays consistent. Shown NEXT to the trigger value (not in a
	// separate badge in the expansion column).
	const isContains = row.match_mode === "contains";
	const matchModeLabel = isContains
		? t("templates.matchModeContainsLabel")
		: t("templates.matchModeExactLabel");
	// Grid: [checkbox][trigger][expansion][actions] on sm+; on narrow
	// widths the expansion half moves to its own line below the trigger
	// (col 2). The sm+ ACTIONS column is FIXED at 6.25rem (100px — the
	// two icon buttons) so it matches the header's fixed actions column.
	//
	// The row is clickable as a whole (toggle selection) — that's what
	// the hover background implies. Action buttons and the checkbox
	// stop propagation so they don't double-toggle.
	return (
		// The row click is a mouse-only convenience for bulk
		// selection — keyboard/SR users toggle via the nested
		// Checkbox (a real role="checkbox" button). Making the row
		// itself keyboard-activatable would double-toggle with the
		// Checkbox's own handler.
		// biome-ignore lint/a11y/noStaticElementInteractions: the nested Checkbox is the accessible control.
		// biome-ignore lint/a11y/useKeyWithClickEvents: keyboard activation would double-toggle with the nested Checkbox; the Checkbox provides the keyboard path.
		<div
			key={row.id}
			data-testid="template-list-row"
			data-selected={selected ? "true" : "false"}
			onClick={() => onToggleSelect(row.id)}
			className={cn(
				"grid cursor-pointer grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 px-3.5 py-2.5 transition-colors hover:bg-foreground/5 sm:grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)_6.25rem]",
				selected && "bg-accent/10 hover:bg-accent/10",
			)}
		>
			{/* Checkbox (col 1) — bulk selection. Its own click already
			    toggles selection; the onClick stops propagation so the
			    row's click-to-toggle handler doesn't double-toggle. */}
			<Checkbox
				checked={selected}
				onCheckedChange={() => onToggleSelect(row.id)}
				onClick={(e) => e.stopPropagation()}
				aria-label={t("templates.selectEntry", { name: row.trigger })}
				className="self-start pt-0.5 sm:self-center sm:pt-0"
			/>
			{/* Trigger (col 2) — the phrase that fires the template, with
			    the match-mode label right beside it. */}
			<div className="flex min-w-0 flex-col items-start gap-1">
				<span
					title={row.trigger}
					className="min-w-0 truncate text-sm font-semibold text-(--text-primary)"
				>
					{row.trigger}
				</span>
				<output
					className={
						"text-[11px] rounded-full px-2 py-0.5 font-medium " +
						(isContains
							? "bg-amber-400/15 text-amber-700 dark:text-amber-400"
							: "bg-accent/15 text-accent")
					}
					aria-label={t("templates.matchModeAria", {
						mode: matchModeLabel,
					})}
				>
					{matchModeLabel}
				</output>
			</div>
			{/* Expansion (col 3 on sm+; row 2 on mobile) — the body the
			    trigger expands to. */}
			<div className="col-start-2 flex min-w-0 items-center sm:col-start-auto">
				<p className="min-w-0 truncate text-xs text-(--text-muted)">
					{row.expansion}
				</p>
			</div>
			{/* Actions (col 4 on sm+; col 3 on mobile, same row as the
			    checkbox): Delete — Edit — the app-wide action-icon
			    convention puts the edit pencil RIGHTMOST in the group
			    (same rule as the Vocabulary rows) so the edit affordance
			    sits consistently at the far edge of every row across
			    pages. Buttons stop propagation so they don't toggle
			    selection. */}
			<div className="flex shrink-0 items-center justify-self-end gap-0.5">
				<Button
					variant="ghost"
					size="icon-xs"
					onClick={(e) => {
						e.stopPropagation();
						onDelete(row);
					}}
					className="text-(--text-muted) hover:text-destructive"
					title={t("templates.deleteTemplate")}
					aria-label={t("templates.deleteAria", { name: row.trigger })}
				>
					<HugeiconsIcon
						icon={Delete01Icon}
						strokeWidth={2.25}
						className="size-4"
					/>
				</Button>
				<Button
					variant="ghost"
					size="icon-xs"
					onClick={(e) => {
						e.stopPropagation();
						onEdit(row);
					}}
					className="text-(--text-muted) hover:text-(--text-secondary)"
					title={t("templates.editTemplate")}
					aria-label={t("templates.editAria", { name: row.trigger })}
				>
					<HugeiconsIcon
						icon={PencilEdit02Icon}
						strokeWidth={2.25}
						className="size-4"
					/>
				</Button>
			</div>
		</div>
	);
});
