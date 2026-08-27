// Add/Edit template dialog (Modal).
//
// Extracted from the former monolithic ``pages/Templates.tsx``.
// Renders the trigger / output / match-mode fields and the
// Cancel / Save footer.  All state + handlers are passed in from the
// parent (``useTemplateDialog`` owns them) so this component is a
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
import { cn } from "@/lib/utils";

import type { TemplateRow } from "../lib/types";
import { VARIABLES } from "../lib/types";

interface TemplateDialogProps {
	open: boolean;
	editingTemplate: TemplateRow | null;
	trigger: string;
	expansion: string;
	matchMode: "exact" | "contains";
	onTriggerChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
	onExpansionChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
	onMatchModeChange: (v: string) => void;
	onClose: () => void;
	onSave: () => void;
}

export function TemplateDialog({
	open,
	editingTemplate,
	trigger,
	expansion,
	matchMode,
	onTriggerChange,
	onExpansionChange,
	onMatchModeChange,
	onClose,
	onSave,
}: TemplateDialogProps) {
	// The Save button is disabled until BOTH fields have
	// non-whitespace content — mirrors the sibling VocabDialog pattern
	// so the user sees the disabled affordance up-front instead of
	// clicking an enabled button and getting a transient warning toast.
	const canSave = trigger.trim() !== "" && expansion.trim() !== "";

	// Surface unknown template-variable tokens (e.g. {date}).
	// The substitution layer (templates/lib/transform.ts) silently drops
	// unknown tokens — this warning tells the user why {date} would be
	// emitted verbatim. Only the 4 supported tokens are treated as known.
	const unknownVars = Array.from(
		new Set(
			(expansion.match(/\{[^}]+\}/g) ?? []).filter(
				(token) => !(VARIABLES as readonly string[]).includes(token),
			),
		),
	);
	return (
		<Modal
			open={open}
			onClose={onClose}
			title={
				editingTemplate ? t("templates.editTitle") : t("templates.addTitle")
			}
			className="w-105"
		>
			<div className="space-y-4">
				<div>
					<label
						htmlFor="template-trigger"
						className="mb-1.5 block text-sm font-medium text-(--text-primary)"
					>
						{t("templates.triggerPhrase")}
					</label>
					<Input
						id="template-trigger"
						value={trigger}
						onChange={onTriggerChange}
						placeholder={t("templates.triggerPlaceholder")}
						className="w-full"
						// autoFocus removed — Radix Dialog handles first-focus automatically
					/>
					<p className="mt-1.5 text-xs text-(--text-muted)">
						{t("templates.triggerHelp")}
					</p>
				</div>

				<div>
					<label
						htmlFor="template-output"
						className="mb-1.5 block text-sm font-medium text-(--text-primary)"
					>
						{t("templates.outputText")}
					</label>
					<textarea
						id="template-output"
						value={expansion}
						onChange={onExpansionChange}
						placeholder={t("templates.outputPlaceholder")}
						rows={5}
						className={cn(
							"w-full resize-y rounded-lg border border-border/10",
							"bg-transparent px-3 py-2 text-sm text-(--text-primary)",
							"placeholder:text-(--text-muted)",
							"focus:border-accent focus:outline-none",
						)}
					/>
					<p className="mt-1.5 text-xs text-(--text-muted)">
						{t("templates.outputHelp")}
						<code className="mx-1 rounded bg-(--bg-subtle) px-1">{`{today}`}</code>
						<code className="mx-1 rounded bg-(--bg-subtle) px-1">{`{now}`}</code>
						<code className="mx-1 rounded bg-(--bg-subtle) px-1">{`{clipboard}`}</code>
						<code className="mx-1 rounded bg-(--bg-subtle) px-1">{`{username}`}</code>
					</p>
					{unknownVars.length > 0 && (
						<p
							role="alert"
							className="mt-1.5 text-xs font-medium text-amber-500"
						>
							{t("templates.unknownVariableWarning", {
								vars: unknownVars.join(", "),
							})}
						</p>
					)}
				</div>

				<div>
					<span className="mb-1.5 block text-sm font-medium text-(--text-primary)">
						{t("templates.matchMode")}
					</span>
					<Select value={matchMode} onValueChange={onMatchModeChange}>
						<SelectTrigger
							className="w-full"
							aria-label={t("templates.matchMode")}
						>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="exact">{t("templates.exactMatch")}</SelectItem>
							<SelectItem value="contains">
								{t("templates.contains")}
							</SelectItem>
						</SelectContent>
					</Select>
				</div>
			</div>

			<ModalFooter>
				<Button variant="ghost" onClick={onClose}>
					{t("common.cancel")}
				</Button>
				<Button variant="default" onClick={onSave} disabled={!canSave}>
					{t("common.save")}
				</Button>
			</ModalFooter>
		</Modal>
	);
}
