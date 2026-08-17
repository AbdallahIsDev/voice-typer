// Inline vocabulary entry form — the SINGLE add/edit pattern for the
// page (the edit dialog modal was removed so create and modify use the
// same inline row treatment, keeping the list visible while editing).
//
// Renders the two simplified fields (wrong word/phrase + correct
// word/phrase) plus Save / Cancel. The parent owns all state
// (useVocabularyQuickAdd for create, useVocabularyEdit for edit);
// this component is purely presentational. The category picker was
// removed with the flat-list redesign — the backend bucket is
// auto-detected on save (and preserved on edit).
//
// Used in two places:
//   - the quick-add row above the table (withBottomBorder, add icon)
//   - the in-place edit row replacing the row being edited
//     (no bottom border — the list's divide-y draws the separators —
//     pencil icon, distinct testid)
import { Add01Icon } from "@hugeicons/core-free-icons";
import type { IconSvgElement } from "@hugeicons/react";
import { HugeiconsIcon } from "@hugeicons/react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

interface VocabInlineFormProps {
	trigger: string;
	replacement: string;
	/** Inline error message (e.g. "This correction already exists"). */
	error?: string | null;
	onTriggerChange: (v: string) => void;
	onReplacementChange: (v: string) => void;
	onSave: () => void;
	onCancel: () => void;
	/** test id — "vocab-quick-add" (add row) vs "vocab-edit-row" (edit row). */
	testId?: string;
	/** Leading icon on the Save button — Add for create, pencil for edit. */
	submitIcon?: IconSvgElement;
	/** Bottom border — the quick-add row above the table keeps its card
	 * look; the in-list edit row drops it (the list's divide-y owns the
	 * separators there). */
	withBottomBorder?: boolean;
}

export function VocabInlineForm({
	trigger,
	replacement,
	error,
	onTriggerChange,
	onReplacementChange,
	onSave,
	onCancel,
	testId = "vocab-quick-add",
	submitIcon = Add01Icon,
	withBottomBorder = true,
}: VocabInlineFormProps) {
	return (
		<div
			data-testid={testId}
			className={cn(
				"grid grid-cols-1 gap-2 bg-(--bg-subtle) px-3.5 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto_auto] sm:items-center",
				// Standalone quick-add row (above the table): full card
				// treatment — same border, radius, and surface as the
				// table container so the form reads as part of the same
				// design system (the old form only had a bottom border
				// and a translucent background that matched nothing).
				// The in-list edit row (withBottomBorder=false) is
				// already framed by the table card, so it keeps the
				// matching surface without a second border box.
				withBottomBorder && "rounded-xl border border-border/10",
			)}
		>
			<Input
				value={trigger}
				onChange={(e) => onTriggerChange(e.target.value)}
				placeholder={t("vocabulary.triggerPlaceholder")}
				aria-label={t("vocabulary.whatYouSay")}
				className="w-full rounded-xl bg-(--bg-subtle) border-border/10"
			/>
			<Input
				value={replacement}
				onChange={(e) => onReplacementChange(e.target.value)}
				placeholder={t("vocabulary.replacementPlaceholder")}
				aria-label={t("vocabulary.whatGetsTyped")}
				className="w-full rounded-xl bg-(--bg-subtle) border-border/10"
			/>
			<div className="flex items-center gap-2">
				<Button
					variant="default"
					size="sm"
					onClick={onSave}
					disabled={!trigger.trim() || !replacement.trim()}
					className="gap-1.5"
				>
					<HugeiconsIcon
						icon={submitIcon}
						strokeWidth={2}
						aria-hidden="true"
						className="size-4"
					/>
					{t("common.save")}
				</Button>
				<Button variant="ghost" size="sm" onClick={onCancel}>
					{t("common.cancel")}
				</Button>
			</div>
			{/* Inline rejection message — shown when the write is blocked
			    (frontend pre-check or authoritative backend
			    client.duplicate_entry rejection). role="alert" so
			    screen readers announce it. */}
			{error && (
				<p
					role="alert"
					data-testid="vocab-quick-add-error"
					className="col-span-full text-xs font-medium text-destructive"
				>
					{error}
				</p>
			)}
		</div>
	);
}
