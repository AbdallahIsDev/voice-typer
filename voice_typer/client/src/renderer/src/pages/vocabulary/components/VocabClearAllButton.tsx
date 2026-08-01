// "Clear All" button + ConfirmDialog for the Vocabulary toolbar.
//
// Self-contained: owns the confirm-open state and renders the
// ConfirmDialog internally so the parent (Vocabulary.tsx) doesn't have
// to. The parent only needs to pass an ``onClearAll`` callback (which
// typically calls ``useVocabulary.clearAllEntries``).
//
// Why a separate component (vs. inlining the ConfirmDialog in
// Vocabulary.tsx): the page-level "instant delete + Undo" path
// intentionally doesn't use a ConfirmDialog — the Undo toast is the
// safety net. Keeping the Clear All's ConfirmDialog in its own
// component makes it obvious that the confirm gate applies ONLY to
// the bulk "clear everything" action, not to per-row deletes.
//
// Mirrors the History page's Clear All pattern (button tinted
// destructive at rest, intensifies on hover) so the visual language
// stays consistent across pages.

import { Delete01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useState } from "react";

import ConfirmDialog from "@/components/common/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";

interface VocabClearAllButtonProps {
	/** Invoked when the user confirms the clear-all action. */
	onClearAll: () => void;
	/** Disabled when there are no entries to clear. */
	disabled?: boolean;
}

export function VocabClearAllButton({
	onClearAll,
	disabled = false,
}: VocabClearAllButtonProps) {
	const [showConfirm, setShowConfirm] = useState(false);

	const handleConfirm = () => {
		setShowConfirm(false);
		onClearAll();
	};

	return (
		<>
			<Button
				variant="outline"
				size="sm"
				onClick={() => setShowConfirm(true)}
				disabled={disabled}
				aria-label={t("vocabulary.clearAllAria")}
				// Destructive action (deletes ALL entries after a
				// ConfirmDialog gate) — permanently tint the button with
				// the destructive design token at rest (text + border)
				// and intensify on hover, matching the History page's
				// Clear All button. The disabled state still uses
				// disabled:opacity-50 from the Button base styles.
				className="gap-2 border-destructive/40 text-destructive/80 hover:text-destructive hover:border-destructive hover:bg-destructive/5"
			>
				<HugeiconsIcon
					icon={Delete01Icon}
					strokeWidth={2}
					className="h-4 w-4"
				/>
				{t("vocabulary.clearAll")}
			</Button>
			<ConfirmDialog
				open={showConfirm}
				title={t("vocabulary.clearAllTitle")}
				message={t("vocabulary.clearAllMessage")}
				confirmLabel={t("vocabulary.clearAllConfirm")}
				onConfirm={handleConfirm}
				onCancel={() => setShowConfirm(false)}
			/>
		</>
	);
}
