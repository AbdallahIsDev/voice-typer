// One row of the vocabulary list.
//
// Extracted from the former monolithic ``pages/Vocabulary.tsx``. Each
// row is a controlled component — the parent passes the entry data,
// the category-label map (so the row doesn't re-resolve t() on every
// render), and the edit/delete callbacks.

import { Delete01Icon, PencilEdit02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";

import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";

import type { VocabRow } from "../lib/transform";

interface VocabListRowProps {
	entry: VocabRow;
	categoryLabels: Record<
		string,
		{ label: string; description: string; example: string }
	>;
	onEdit: (entry: VocabRow) => void;
	onDelete: (entry: VocabRow) => void;
}

export function VocabListRow({
	entry,
	categoryLabels,
	onEdit,
	onDelete,
}: VocabListRowProps) {
	// Category badge color: each backend category
	// gets a distinct accent so the user can scan
	// the list by category at a glance.  Palette
	// matches the Templates match-mode badge and
	// the History favorites toggle so the visual
	// language stays consistent.
	const catLabel = categoryLabels[entry.category]?.label ?? entry.category;
	const catBadgeColor =
		entry.category === "misspellings"
			? "bg-rose-400/15 text-rose-700 dark:text-rose-400"
			: entry.category === "phrase_corrections"
				? "bg-amber-400/15 text-amber-700 dark:text-amber-400"
				: entry.category === "extra_word_patterns"
					? "bg-slate-400/15 text-slate-700 dark:text-slate-300"
					: entry.category === "technical_terms"
						? "bg-sky-400/15 text-sky-700 dark:text-sky-400"
						: entry.category === "names"
							? "bg-violet-400/15 text-violet-700 dark:text-violet-400"
							: "bg-emerald-400/15 text-emerald-700 dark:text-emerald-400";
	return (
		<div key={entry._id} className="flex items-start gap-3 px-3.5 py-2.5">
			<div className="min-w-0 flex-1">
				<div className="flex items-center gap-2.5">
					<span className="text-sm dark:font-normal font-medium text-destructive tracking-wider">
						{entry.original}{" "}
					</span>
					<span className="text-sm text-(--text-muted)">→</span>
					<span className="text-sm font-semibold text-(--text-primary)">
						{entry.correction}{" "}
					</span>
					{/* Category badge — surfaces the backend
                        category so the user can see at a
                        glance which bucket each entry belongs
                        to (previously the category was
                        hidden in the dialog only). */}{" "}
					<output
						className={
							"text-[10px] rounded-full px-2 py-0.5 font-medium uppercase tracking-wide " +
							catBadgeColor
						}
						aria-label={t("vocabulary.categoryBadgeAria", {
							category: catLabel,
						})}
					>
						{catLabel}{" "}
					</output>
				</div>
			</div>
			<div className="flex shrink-0 items-center gap-1">
				<Button
					variant="ghost"
					size="icon-xs"
					onClick={() => onEdit(entry)}
					className="text-(--text-muted) hover:text-accent"
					title={t("vocabulary.edit")}
					aria-label={t("vocabulary.editAria", { name: entry.original })}
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
					onClick={() => onDelete(entry)}
					className="text-(--text-muted) hover:text-destructive"
					title={t("common.delete")}
					aria-label={t("vocabulary.deleteAria", { name: entry.original })}
				>
					<HugeiconsIcon
						icon={Delete01Icon}
						strokeWidth={2.5}
						className="h-4 w-4"
					/>
				</Button>
			</div>
		</div>
	);
}
