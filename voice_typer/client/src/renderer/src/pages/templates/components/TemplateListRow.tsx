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

import { InfoTooltip } from "@/components/feedback/InfoTooltip";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";

import type { TemplateRow } from "../lib/types";

interface TemplateListRowProps {
	row: TemplateRow;
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
	onEdit,
	onDelete,
}: TemplateListRowProps) {
	// Colored match-mode badge: "exact" → neutral/blue,
	// "contains" → amber.  Uses the same color tokens
	// as the History favorites toggle so the palette
	// stays consistent.
	const isContains = row.match_mode === "contains";
	const matchModeLabel = isContains
		? t("templates.matchModeContainsLabel")
		: t("templates.matchModeExactLabel");
	return (
		<li key={row.id} className="flex items-center gap-3 px-3.5 py-2.5">
			<div className="min-w-0 flex-1">
				<p className="text-sm font-semibold text-(--text-primary)">
					{row.trigger}
				</p>
				<div className="mt-0.5 flex items-center gap-3">
					<p className="max-w-75 truncate text-xs text-(--text-muted)">
						{row.expansion}
					</p>
					<output
						className={
							"text-xs rounded-full px-2 py-0.5 font-medium " +
							(isContains
								? "bg-amber-400/15 text-amber-700 dark:text-amber-400"
								: "bg-accent/15 text-accent")
						}
						aria-label={t("templates.matchModeAria", {
							mode: matchModeLabel,
						})}
					>
						{row.variables}v &middot; {matchModeLabel}
					</output>
					<InfoTooltip
						text={
							row.used_variables.length > 0
								? t("templates.variablesTooltip", {
										vars: row.used_variables.join(", "),
									})
								: t("templates.noVariablesTooltip")
						}
					/>
				</div>
			</div>
			<div className="flex shrink-0 items-center gap-0.5">
				<Button
					variant="ghost"
					size="icon-xs"
					onClick={() => onEdit(row)}
					className="text-(--text-muted) hover:text-(--text-secondary)"
					title={t("templates.editTemplate")}
					aria-label={t("templates.editAria", { name: row.trigger })}
				>
					<HugeiconsIcon
						icon={PencilEdit02Icon}
						strokeWidth={2.5}
						className="h-4 w-4"
					/>
				</Button>
				<Button
					variant="ghost"
					size="icon-xs"
					onClick={() => onDelete(row)}
					className="text-(--text-muted) hover:text-destructive"
					title={t("templates.deleteTemplate")}
					aria-label={t("templates.deleteAria", { name: row.trigger })}
				>
					<HugeiconsIcon
						icon={Delete01Icon}
						strokeWidth={2.5}
						className="h-4 w-4"
					/>
				</Button>
			</div>
		</li>
	);
});
