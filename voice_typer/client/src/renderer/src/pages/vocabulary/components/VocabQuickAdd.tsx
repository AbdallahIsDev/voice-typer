// Inline "quick add" row — keeps the list visible while adding an
// entry (replaces the disconnected Add-Entry modal). Renders the two
// simplified fields (wrong word/phrase + correct word/phrase) plus
// Save / Cancel. The parent owns all state (useVocabularyQuickAdd);
// this component is purely presentational. The category picker was
// removed with the flat-list redesign — the backend bucket is
// auto-detected on save.
import { Add01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { t } from "@/i18n/i18n";

interface VocabQuickAddProps {
	trigger: string;
	replacement: string;
	/** Inline error message (e.g. "This correction already exists"). */
	error?: string | null;
	onTriggerChange: (v: string) => void;
	onReplacementChange: (v: string) => void;
	onSave: () => void;
	onCancel: () => void;
}

export function VocabQuickAdd({
	trigger,
	replacement,
	error,
	onTriggerChange,
	onReplacementChange,
	onSave,
	onCancel,
}: VocabQuickAddProps) {
	return (
		<div
			data-testid="vocab-quick-add"
			className="grid grid-cols-1 gap-2 border-b border-border/10 bg-(--bg-subtle)/60 px-3.5 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto_auto] sm:items-center"
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
						icon={Add01Icon}
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
			{/* Inline rejection message — shown when the add is blocked
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
