// Inline template entry form — the quick-add row above the Templates
// list (XA-5-1). Mirrors ``VocabInlineForm`` from the Vocabulary page so
// both "list" pages share the same create-flow shape: a two-input row
// (trigger + expansion) plus Save / Cancel, with Enter-to-save.
//
// This component is purely presentational — the parent owns all state
// (``useTemplateQuickAdd``). Match mode is intentionally NOT surfaced
// here: the inline flow is for the common case (a fresh trigger/output
// pair, defaulting to "exact" match). Power users who need "contains"
// can use the Edit dialog (which exposes the full match-mode picker).
//
// Differences vs ``VocabInlineForm``:
//   - Field semantics: trigger → expansion (vs trigger → replacement).
//   - Reuses the existing ``templates.triggerPlaceholder`` /
//     ``templates.outputPlaceholder`` / ``templates.triggerPhrase`` /
//     ``templates.outputText`` keys (no new locale keys needed).

import { Add01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { t } from "@/i18n/i18n";

interface TemplateInlineFormProps {
	trigger: string;
	expansion: string;
	/** Inline error message (e.g. duplicate trigger). */
	error?: string | null;
	onTriggerChange: (v: string) => void;
	onExpansionChange: (v: string) => void;
	onSave: () => void;
	onCancel: () => void;
}

export function TemplateInlineForm({
	trigger,
	expansion,
	error,
	onTriggerChange,
	onExpansionChange,
	onSave,
	onCancel,
}: TemplateInlineFormProps) {
	// Enter-to-save: a single form wraps both inputs so pressing Enter
	// in EITHER field submits. The Submit button's ``type="submit"`` is
	// the implicit default; the explicit Cancel button is
	// ``type="button"`` so it doesn't trigger submit.
	const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
		e.preventDefault();
		onSave();
	};
	return (
		<form
			data-testid="template-quick-add"
			onSubmit={handleSubmit}
			className="grid grid-cols-1 gap-2 rounded-xl border border-border/10 bg-(--bg-subtle) px-3.5 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto_auto] sm:items-center"
		>
			<Input
				value={trigger}
				onChange={(e) => onTriggerChange(e.target.value)}
				placeholder={t("templates.triggerPlaceholder")}
				aria-label={t("templates.triggerPhrase")}
				autoFocus
				className="w-full rounded-xl bg-(--bg-subtle) border-border/10"
			/>
			<Input
				value={expansion}
				onChange={(e) => onExpansionChange(e.target.value)}
				placeholder={t("templates.outputPlaceholder")}
				aria-label={t("templates.outputText")}
				className="w-full rounded-xl bg-(--bg-subtle) border-border/10"
			/>
			<div className="flex items-center gap-2">
				<Button
					type="submit"
					variant="default"
					size="sm"
					disabled={!trigger.trim() || !expansion.trim()}
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
				<Button type="button" variant="ghost" size="sm" onClick={onCancel}>
					{t("common.cancel")}
				</Button>
			</div>
			{/* Inline rejection message — shown when the write is blocked
			    (duplicate-trigger pre-check). role="alert" so screen
			    readers announce it. Mirrors VocabInlineForm's error row. */}
			{error && (
				<p
					role="alert"
					data-testid="template-quick-add-error"
					className="col-span-full text-xs font-medium text-destructive"
				>
					{error}
				</p>
			)}
		</form>
	);
}
