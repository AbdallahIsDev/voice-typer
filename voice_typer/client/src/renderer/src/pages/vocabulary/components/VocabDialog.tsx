// Add/Edit vocabulary dialog (Modal).
//
// Extracted from the former monolithic ``pages/Vocabulary.tsx``.
// Renders the trigger / replacement / category-picker fields and the
// Cancel / Save footer.  All state + handlers are passed in from the
// parent (``useVocabularyDialog`` owns them) so this component is a
// pure presentational wrapper.

import { Modal, ModalFooter } from "@/components/common/Modal";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { t } from "@/i18n/i18n";

import { CATEGORIES } from "../lib/categories";

interface VocabDialogProps {
	open: boolean;
	editingEntry: { original: string; category?: string } | null;
	trigger: string;
	replacement: string;
	category: string;
	onTriggerChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
	onReplacementChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
	onCategoryChange: (value: string) => void;
	onClose: () => void;
	onSave: () => void;
	categoryLabels: Record<
		string,
		{ label: string; description: string; example: string }
	>;
}

export function VocabDialog({
	open,
	editingEntry,
	trigger,
	replacement,
	category,
	onTriggerChange,
	onReplacementChange,
	onCategoryChange,
	onClose,
	onSave,
	categoryLabels,
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

				{/*explicit category picker. */}
				<div>
					<span className="mb-1.5 block text-sm font-medium text-(--text-primary)">
						{t("vocabulary.categoryLabel")}
					</span>
					<Select value={category} onValueChange={onCategoryChange}>
						<SelectTrigger
							className="w-full"
							aria-label={t("vocabulary.categoryAria")}
						>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="auto">
								<span className="flex flex-col">
									<span>{t("vocabulary.category.autoDetect")}</span>
									<span className="text-xs text-(--text-muted)">
										{t("vocabulary.category.autoDetectDesc")}
									</span>
								</span>
							</SelectItem>
							{CATEGORIES.map((cat) => (
								<SelectItem key={cat} value={cat}>
									<span className="flex flex-col">
										<span>{categoryLabels[cat]?.label ?? cat}</span>
										<span className="text-xs text-(--text-muted)">
											{categoryLabels[cat]?.example ?? ""}
										</span>
									</span>
								</SelectItem>
							))}
						</SelectContent>
					</Select>
					{category !== "auto" && categoryLabels[category] && (
						<p className="mt-1.5 text-xs text-(--text-muted)">
							{categoryLabels[category].description}
						</p>
					)}
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
