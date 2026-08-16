// Add/Edit vocabulary dialog (Modal).
//
// Extracted from the former monolithic ``pages/Vocabulary.tsx``.
// Renders the trigger / replacement fields and the Cancel / Save
// footer. The category picker was removed when the page became a flat
// two-column list — the entry's backend bucket is preserved
// automatically on edit (see useVocabularyDialog). All state +
// handlers are passed in from the parent so this component is a pure
// presentational wrapper.

import { Modal, ModalFooter } from "@/components/common/Modal";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { t } from "@/i18n/i18n";

interface VocabDialogProps {
	open: boolean;
	editingEntry: { original: string } | null;
	trigger: string;
	replacement: string;
	onTriggerChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
	onReplacementChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
	onClose: () => void;
	onSave: () => void;
}

export function VocabDialog({
	open,
	editingEntry,
	trigger,
	replacement,
	onTriggerChange,
	onReplacementChange,
	onClose,
	onSave,
}: VocabDialogProps) {
	return (
		<Modal
			open={open}
			onClose={onClose}
			title={
				editingEntry
					? t("vocabulary.editEntryTitle")
					: t("vocabulary.addEntryTitle")
			}
			className="w-105"
		>
			<div className="space-y-4">
				<div>
					<label
						htmlFor="vocab-trigger"
						className="mb-1.5 block text-sm font-medium text-(--text-primary)"
					>
						{t("vocabulary.whatYouSay")}
					</label>
					<Input
						id="vocab-trigger"
						value={trigger}
						onChange={onTriggerChange}
						placeholder={t("vocabulary.triggerPlaceholder")}
						className="w-full"
						// autoFocus removed — Radix Dialog handles first-focus automatically (matches the Templates pattern).
					/>
					<p className="mt-1.5 text-xs text-(--text-muted)">
						{t("vocabulary.triggerHelp")}
					</p>
				</div>

				<div>
					<label
						htmlFor="vocab-replacement"
						className="mb-1.5 block text-sm font-medium text-(--text-primary)"
					>
						{t("vocabulary.whatGetsTyped")}
					</label>
					<Input
						id="vocab-replacement"
						value={replacement}
						onChange={onReplacementChange}
						placeholder={t("vocabulary.replacementPlaceholder")}
						className="w-full"
					/>
					<p className="mt-1.5 text-xs text-(--text-muted)">
						{t("vocabulary.replacementHelp")}
					</p>
				</div>
			</div>

			<ModalFooter>
				<Button variant="ghost" onClick={onClose}>
					{t("common.cancel")}
				</Button>
				<Button
					variant="default"
					onClick={onSave}
					disabled={!trigger.trim() || !replacement.trim()}
				>
					{t("common.save")}
				</Button>
			</ModalFooter>
		</Modal>
	);
}
