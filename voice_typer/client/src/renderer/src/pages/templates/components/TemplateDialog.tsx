// Add/Edit template dialog (Modal).
//
// Extracted from the former monolithic ``pages/Templates.tsx``.
// Renders the trigger / output / match-mode fields and the
// Cancel / Save footer.  All state + handlers are passed in from the
// parent (``useTemplateDialog`` owns them) so this component is a
// pure presentational wrapper.
//
// 2026-08-28 UX pass (uniform field system):
//   - ONE field treatment everywhere: the trigger Input, the output
//     textarea, and the match-mode Select all use the same dark-filled
//     surface (``bg-(--bg-subtle)``) with a 1px ``border-border/5``
//     frame and the same ``rounded-lg`` radius — previously the Input
//     was a filled pill, the textarea a border-only box, and the Select
//     a third style.
//   - Placeholders are visibly muted (``placeholder:text-(--text-muted)``
//     + reduced opacity) so an example like "my email" can't be
//     mistaken for saved data.
//   - The supported variable tokens render as small tappable chips
//     (monospace on a raised surface); clicking one appends the token
//     to the output.
//   - 24px rhythm between field groups, 8px between label/helper/field.
//   - The footer sits behind a subtle top divider.

import { KBD_CHIP_CLASSES } from "@/components/common/Kbd";
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
	onInsertVariable: (token: string) => void;
}

/** Shared field shell — every control in this dialog uses the SAME
 *  surface: dark-filled ``--bg-subtle``, 1px ``border-border/5``
 *  frame, ``rounded-lg``, and the same focus brightening to the accent
 *  (the blue used by the Save button). Previously each control had its
 *  own distinct chrome (Input pill / border-only textarea / separate
 *  Select). */
const FIELD_SHELL =
	"rounded-lg border border-border/5 bg-(--bg-subtle) text-sm text-(--text-primary) placeholder:text-(--text-muted)/40 focus:border-accent focus:outline-none";

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
	onInsertVariable,
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
			<div className="space-y-6">
				<div>
					<label
						htmlFor="template-trigger"
						className="mb-2 block text-sm font-medium text-(--text-primary)"
					>
						{t("templates.triggerPhrase")}
					</label>
					<Input
						id="template-trigger"
						value={trigger}
						onChange={onTriggerChange}
						placeholder={t("templates.triggerPlaceholder")}
						className={cn("w-full", FIELD_SHELL)}
						// autoFocus removed — Radix Dialog handles first-focus automatically
					/>
					<p className="mt-2 text-xs text-(--text-muted)">
						{t("templates.triggerHelp")}
					</p>
				</div>

				<div>
					<label
						htmlFor="template-output"
						className="mb-2 block text-sm font-medium text-(--text-primary)"
					>
						{t("templates.outputText")}
					</label>
					<textarea
						id="template-output"
						value={expansion}
						onChange={onExpansionChange}
						placeholder={t("templates.outputPlaceholder")}
						rows={5}
						className={cn("w-full resize-y px-3 py-2", FIELD_SHELL)}
					/>
					<div className="mt-2 flex flex-wrap items-center gap-1.5">
						<span className="text-xs text-(--text-muted)">
							{t("templates.outputHelp")}
						</span>
						{VARIABLES.map((token) => (
							// Tappable variable chip — same bordered mono chip
							// surface as every keycap in the app (KBD_CHIP_CLASSES),
							// plus hover/focus affordance so it reads as tappable.
							// Clicking inserts the token into the output.
							<button
								key={token}
								type="button"
								onClick={() => onInsertVariable(token)}
								title={t("templates.insertVariable", { token })}
								className={cn(
									KBD_CHIP_CLASSES,
									"cursor-pointer transition-colors hover:border-accent/40 hover:bg-accent/5 hover:text-accent focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none",
								)}
							>
								{token}
							</button>
						))}
					</div>
					{unknownVars.length > 0 && (
						<p role="alert" className="mt-2 text-xs font-medium text-amber-500">
							{t("templates.unknownVariableWarning", {
								vars: unknownVars.join(", "),
							})}
						</p>
					)}
				</div>

				<div>
					<label
						htmlFor="template-match-mode"
						className="mb-2 block text-sm font-medium text-(--text-primary)"
					>
						{t("templates.matchMode")}
					</label>
					<Select value={matchMode} onValueChange={onMatchModeChange}>
						<SelectTrigger
							id="template-match-mode"
							className={cn("w-full", FIELD_SHELL)}
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

			<ModalFooter className="mt-2 border-t border-border/5 pt-4">
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
