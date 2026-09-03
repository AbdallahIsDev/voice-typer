// Add/Edit template dialog (Modal).
//
// Extracted from the former monolithic ``pages/Templates.tsx``.
// Renders the trigger / output / match-mode fields and the
// Cancel / Save footer.  All state + handlers are passed in from the
// parent (``useTemplateDialog`` owns them) so this component is a
// pure presentational wrapper.
//
// 2026-08-28 UX pass (uniform field system): placeholders visibly
// muted, variable tokens as tappable keycap chips, 24px rhythm between
// field groups.
//
// 2026-09-02 theme pass (native primitives, roomier panel):
//   - The custom ``rounded-lg`` field chrome is GONE — every control
//     now uses the app's native pill language: the shared ``Input``
//     (rounded-xl, bg-input/50, pointer/keyboard focus modality), the
//     new shared ``Textarea`` (same pill + focus contract), and the
//     native ``SelectTrigger`` (rounded-4xl, mirrors the outline
//     Button). A single overlay-wide field shell fought all three
//     primitives and read as off-theme next to every other dialog.
//   - Panel widened 420px → 520px (``size="lg"`` lifts the max-w cap
//     to max-w-xl; ``w-130`` sets the width) — the form reads less
//     cramped at 5 textarea rows.
//   - The unknown-variable alert uses the ``--warning`` theme token
//     (tracks the active theme like the warning Button variant)
//     instead of hardcoded ``amber-500``.
//
// 2026-09-03 info architecture pass + field polish:
//   - Trigger description moved from a body paragraph into an
//     InfoTooltip beside the label — the exact Settings-page pattern.
//   - Output helper split into TWO rows: description paragraph first,
//     then ALL variable chips grouped on their own row (they used to
//     flow inline with the sentence).
//   - Fields (Input + Textarea) now carry per-instance polish on top of
//     the shared primitives: ``rounded-lg`` (matches the lg panel; the
//     default rounded-xl read too round) + ``bg-input/25`` (the 50%
//     wash was too visible on the bg panel). Match mode is the shared
//     two-option ``SegmentedControl`` stacked vertically (label above,
//     control below) — same row layout as the trigger and output fields.
//   - All spacing uses ``flex gap-`` instead of ``space-y-`` / margin
//     utilities: each field group is a ``flex-col gap-2`` container,
//     and the three groups are wrapped in a ``flex-col gap-6`` root.

import { useState } from "react";

import { KBD_CHIP_CLASSES } from "@/components/common/Kbd";
import {
	ConfirmDiscardDialog,
	Modal,
	ModalFooter,
} from "@/components/common/Modal";
import { InfoTooltip } from "@/components/feedback/InfoTooltip";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { Textarea } from "@/components/ui/textarea";
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

	// Unsaved-edits gate: when the user attempts to close via Escape,
	// the overlay, the corner close button, OR the footer Cancel button
	// while the form holds content that differs from the template being
	// edited (or any content for a fresh add), confirm the discard
	// first. Every close path funnels through the same veto — an
	// explicit Cancel click after edits is a silent data-loss path
	// otherwise. Save is the only ungated exit (it commits the edits).
	const [confirmingDiscard, setConfirmingDiscard] = useState(false);
	const hasEdits =
		editingTemplate === null
			? trigger.trim() !== "" || expansion.trim() !== ""
			: trigger !== editingTemplate.trigger ||
				expansion !== editingTemplate.expansion ||
				matchMode !== (editingTemplate.match_mode ?? "exact");

	const handleCloseIntent = (): boolean => {
		if (!hasEdits) return true;
		setConfirmingDiscard(true);
		return false;
	};

	// Footer Cancel: same veto gate as Esc/overlay/corner-X. Only calls
	// onClose when the gate allows the close (clean form, or the user
	// confirmed the discard via ConfirmDiscardDialog's own onDiscard).
	const handleCancel = () => {
		if (handleCloseIntent()) onClose();
	};

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
			onCloseIntent={handleCloseIntent}
			title={
				editingTemplate ? t("templates.editTitle") : t("templates.addTitle")
			}
			// Roomier form panel: size="lg" lifts the dialog max-width cap
			// to max-w-xl on desktop and w-130 sets a 520px panel — the
			// default 420px box cramped the 5-row textarea + chip row.
			size="lg"
			className="w-130"
		>
			<div className="flex flex-col gap-6">
				<div className="flex flex-col gap-2">
					{/* Label + help tooltip — the same pattern as every
					    Settings row (SettingRow renders InfoTooltip beside
					    the label). The trigger description moved OUT of the
					    body into this tooltip. */}
					<div className="flex items-center gap-2">
						<label
							htmlFor="template-trigger"
							className="text-sm font-medium text-(--text-primary)"
						>
							{t("templates.triggerPhrase")}
						</label>
						<InfoTooltip
							text={t("templates.triggerHelp")}
							contextLabel={t("templates.triggerPhrase")}
						/>
					</div>
					<Input
						id="template-trigger"
						value={trigger}
						onChange={onTriggerChange}
						placeholder={t("templates.triggerPlaceholder")}
						className="rounded-lg bg-input/25"
					/>
				</div>

				<div className="flex flex-col gap-2">
					<label
						htmlFor="template-output"
						className="text-sm font-medium text-(--text-primary)"
					>
						{t("templates.outputText")}
					</label>
					<Textarea
						id="template-output"
						value={expansion}
						onChange={onExpansionChange}
						placeholder={t("templates.outputPlaceholder")}
						rows={5}
						className="resize-y rounded-lg bg-input/25"
					/>
					{/* Two rows: the description alone on the first row,
					    ALL variable chips grouped on the second — the chips
					    no longer flow inline with the sentence. */}
					<p className="text-xs text-(--text-muted)">
						{t("templates.outputHelp")}
					</p>
					<div className="flex flex-wrap items-center gap-2">
						{VARIABLES.map((token) => (
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
						<p role="alert" className="text-xs font-medium text-warning">
							{t("templates.unknownVariableWarning", {
								vars: unknownVars.join(", "),
							})}
						</p>
					)}
				</div>

				<div className="flex flex-col gap-2">
					<span className="text-sm font-medium text-(--text-primary)">
						{t("templates.matchMode")}
					</span>
					<SegmentedControl
						options={[
							{ value: "exact", label: t("templates.exactMatch") },
							{ value: "contains", label: t("templates.contains") },
						]}
						value={matchMode}
						onChange={onMatchModeChange}
						ariaLabel={t("templates.matchMode")}
						// Fit the two options instead of stretching across the
						// full dialog width (flex-col parent stretches inline
						// children by default).
						className="self-start"
					/>
				</div>
			</div>

			<ModalFooter>
				<Button variant="ghost" onClick={handleCancel}>
					{t("common.cancel")}
				</Button>
				<Button variant="default" onClick={onSave} disabled={!canSave}>
					{t("common.save")}
				</Button>
			</ModalFooter>
			<ConfirmDiscardDialog
				open={confirmingDiscard}
				onDiscard={() => {
					setConfirmingDiscard(false);
					onClose();
				}}
				onStay={() => setConfirmingDiscard(false)}
			/>
		</Modal>
	);
}
