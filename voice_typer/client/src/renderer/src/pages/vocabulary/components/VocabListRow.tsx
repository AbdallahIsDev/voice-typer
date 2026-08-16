// One row of the vocabulary list — a clean two-column pairing.
//
// Simplified for scannability:
//   - leading checkbox (bulk selection)
//   - the wrong→correct pairing as two connected text spans with an
//     arrow ("this becomes that")
//   - direct Edit + Delete icon buttons on the right (larger touch
//     target, hover states, tooltips, aria-labels) — no overflow menu
//   - responsive: on narrow widths the corrected half stacks below the
//     original (connector arrow stays visible) instead of overflowing
//
// The row is memoized — the parent passes stable useCallback handlers
// so a search keystroke (which re-renders the page but changes no row
// props) skips every row's render.
import {
	ArrowRight01Icon,
	Delete01Icon,
	PencilEdit02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo } from "react";
import { Button } from "@/components/ui/button";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

import type { VocabRow } from "../lib/transform";

interface VocabListRowProps {
	entry: VocabRow;
	selected: boolean;
	onToggleSelect: (id: string) => void;
	onEdit: (entry: VocabRow) => void;
	onDelete: (entry: VocabRow) => void;
}

export const VocabListRow = memo(function VocabListRow({
	entry,
	selected,
	onToggleSelect,
	onEdit,
	onDelete,
}: VocabListRowProps) {
	// Grid: [checkbox][original][corrected][actions] on sm+; on narrow
	// widths the corrected half moves to its own line below the
	// original (col 2), keeping the connector arrow visible.
	return (
		<div
			key={entry._id}
			data-testid="vocab-list-row"
			data-selected={selected ? "true" : "false"}
			className={cn(
				"grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 px-3.5 py-2.5 transition-colors hover:bg-foreground/5 sm:grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)_auto]",
				selected && "bg-accent/10 hover:bg-accent/10",
			)}
		>
			{/* Checkbox (col 1) — bulk selection. */}
			<label className="flex cursor-pointer items-center self-start pt-0.5 sm:self-center sm:pt-0">
				<input
					type="checkbox"
					checked={selected}
					onChange={() => onToggleSelect(entry._id)}
					aria-label={t("vocabulary.selectEntry", { name: entry.original })}
					className="size-4 shrink-0 cursor-pointer accent-[color-mix(in_oklab,var(--accent)_60%,transparent)] focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
				/>
			</label>

			{/* Original (col 2) — what the recognizer mishears, styled
			    red to signal "incorrect". */}
			<span
				title={entry.original}
				className="min-w-0 truncate text-sm font-medium text-destructive tracking-wider"
			>
				{entry.original}
			</span>

			{/* Corrected (col 3 on sm+; row 2 on mobile) — arrow + text,
			    styled bold/primary to signal "correct". */}
			<span className="col-start-2 flex min-w-0 items-center gap-1.5 sm:col-start-auto">
				<HugeiconsIcon
					icon={ArrowRight01Icon}
					strokeWidth={2.25}
					aria-hidden="true"
					className="size-3.5 shrink-0 text-(--text-muted)"
				/>
				<span
					title={entry.correction}
					className="min-w-0 truncate text-sm font-semibold text-(--text-primary)"
				>
					{entry.correction}
				</span>
			</span>

			{/* Direct Edit + Delete actions (col 4 on sm+; col 3 on
			    mobile, same row as the checkbox). */}
			<div className="flex items-center justify-self-end gap-0.5">
				<Tooltip>
					<TooltipTrigger asChild>
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label={t("vocabulary.editAria", { name: entry.original })}
							title={t("vocabulary.edit")}
							onClick={() => onEdit(entry)}
							className="text-(--text-muted) transition-colors hover:bg-foreground/10 hover:text-(--text-primary)"
						>
							<HugeiconsIcon
								icon={PencilEdit02Icon}
								strokeWidth={2.25}
								aria-hidden="true"
								className="size-4"
							/>
						</Button>
					</TooltipTrigger>
					<TooltipContent side="left">{t("vocabulary.edit")}</TooltipContent>
				</Tooltip>
				<Tooltip>
					<TooltipTrigger asChild>
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label={t("vocabulary.deleteAria", { name: entry.original })}
							title={t("common.delete")}
							onClick={() => onDelete(entry)}
							className="text-(--text-muted) transition-colors hover:bg-destructive/10 hover:text-destructive"
						>
							<HugeiconsIcon
								icon={Delete01Icon}
								strokeWidth={2.25}
								aria-hidden="true"
								className="size-4"
							/>
						</Button>
					</TooltipTrigger>
					<TooltipContent side="left">{t("common.delete")}</TooltipContent>
				</Tooltip>
			</div>
		</div>
	);
});
