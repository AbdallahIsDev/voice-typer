// Toolbar (Import / Export / Add buttons) for the Vocabulary page.
//
// Extracted from the former monolithic ``pages/Vocabulary.tsx``. The
// hidden ``<input type="file">`` for Import is rendered once here and
// re-used — its ``value`` is reset after each ``onChange`` so re-
// selecting the same file fires the event again (otherwise the OS
// picker suppresses the event if the path is unchanged).

import { Add01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { RefObject } from "react";

import ExportFormatMenu from "@/components/common/ExportFormatMenu";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import type { ExportFormat } from "../../../../../shared/export-format";

interface VocabToolbarProps {
	importInputRef: RefObject<HTMLInputElement | null>;
	onImportClick: () => void;
	onImportFile: (file: File | undefined | null) => void;
	onExport: (format: ExportFormat) => void;
	onAdd: () => void;
	exportDisabled: boolean;
	addDisabled: boolean;
	/** Clear-All affordance (gated by a ConfirmDialog in the page). */
	onClearAll: () => void;
	clearAllDisabled: boolean;
}

export function VocabToolbar({
	importInputRef,
	onImportClick,
	onImportFile,
	onExport,
	onAdd,
	exportDisabled,
	addDisabled,
	onClearAll,
	clearAllDisabled,
}: VocabToolbarProps) {
	return (
		<div className="flex flex-wrap items-center gap-2">
			{/* Hidden file input for the Import button (mirrors
		the Templates pattern). */}
			<input
				ref={importInputRef}
				type="file"
				accept="application/json,.json,.csv,text/csv"
				className="sr-only"
				onChange={(e) => {
					const file = e.target.files?.[0];
					onImportFile(file);
				}}
				aria-hidden="true"
				tabIndex={-1}
			/>
			<Button
				variant="outline"
				size="sm"
				onClick={onImportClick}
				aria-label={t("common.importAria")}
				className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
			>
				{/* Import icon omitted — label is sufficient. */}
				{t("common.import")}
			</Button>
			<ExportFormatMenu onExport={onExport} disabled={exportDisabled} />
			{/* Vertical divider separating the read/import actions from
			    the destructive Clear All affordance — the destructive
			    button gets pushed to the far end of the toolbar so the
			    destructive zone is visually distinct from the create
			    action (mirrors the History action row). ``hidden sm:block``
			    keeps the divider from dangling at the start of a wrapped
			    line when the toolbar wraps on very narrow windows. */}
			<Button
				variant="outline"
				size="sm"
				onClick={onAdd}
				disabled={addDisabled}
				// FIX: muted text/icon by default, white on hover —
				// matches the sibling Export button (also fixed in
				// ExportFormatMenu) and the outline-button style
				// used across other pages.
				className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
			>
				<HugeiconsIcon icon={Add01Icon} strokeWidth={2} className="h-4 w-4" />
				{t("vocabulary.addWord")}
			</Button>
			<div
				aria-hidden="true"
				className="mx-0.5 hidden h-4 w-px bg-border sm:block"
			/>
			<Button
				variant="outline"
				size="sm"
				onClick={onClearAll}
				disabled={clearAllDisabled}
				// Destructive-tinted affordance for the destructive
				// "clear every entry" action (mirrors the
				// Troubleshooting section's destructive styling).
				className="gap-2 text-(--text-muted) hover:text-destructive hover:border-destructive/40"
				aria-label={t("vocabulary.clearAllAria")}
				title={t("vocabulary.clearAllAria")}
			>
				{t("vocabulary.clearAll")}
			</Button>
		</div>
	);
}
