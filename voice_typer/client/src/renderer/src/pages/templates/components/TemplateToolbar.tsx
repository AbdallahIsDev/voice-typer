// Toolbar (Import / Export / Add buttons) for the Templates page.
//
// Extracted from the former monolithic ``pages/Templates.tsx``. The
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

interface TemplateToolbarProps {
	importInputRef: RefObject<HTMLInputElement | null>;
	onImportClick: () => void;
	onImportFile: (file: File | undefined | null) => void;
	/**
	 * Export callback. The format (ExportFormat — `"json" | "csv"`)
	 * is chosen by the ExportFormatMenu and forwarded here so the
	 * parent can pass it through to the IPC bridge. : previously
	 * the parent's arrow function `() => doExport()` dropped the
	 * format arg, so CSV export silently behaved like JSON export.
	 */
	onExport: (format: ExportFormat) => void | Promise<void>;
	onAdd: () => void;
	exportDisabled: boolean;
	/** Opens the Clear-All confirmation dialog (wipes every template). */
	onClearAll: () => void;
	/** Disables the Clear All button when there is nothing to clear. */
	clearAllDisabled: boolean;
}

export function TemplateToolbar({
	importInputRef,
	onImportClick,
	onImportFile,
	onExport,
	onAdd,
	exportDisabled,
	onClearAll,
	clearAllDisabled,
}: TemplateToolbarProps) {
	return (
		<div className="flex flex-wrap items-center gap-2">
			<input
				ref={importInputRef}
				type="file"
				accept="application/json,.json"
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
				// Surface the expected file format
				// schema on hover so the user knows what shape
				// the import expects without trial-and-error.
				title={t("templates.importFormatHint")}
				className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
			>
				{/* Import icon omitted — label is sufficient. */}
				{t("common.import")}
			</Button>
			<ExportFormatMenu onExport={onExport} disabled={exportDisabled} />
			<Button
				variant="outline"
				size="sm"
				onClick={onAdd}
				aria-label={t("templates.addNewAria")}
				// FIX: muted text/icon by default, white on hover —
				// matches the muted style used by outline buttons
				// elsewhere (History action row, Vocabulary add, etc.).
				className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
			>
				<HugeiconsIcon icon={Add01Icon} strokeWidth={2} className="h-4 w-4" />
				{t("templates.addTemplate")}
			</Button>
			<Button
				variant="outline"
				size="sm"
				onClick={onClearAll}
				disabled={clearAllDisabled}
				aria-label={t("templates.clearAllAria")}
				className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
			>
				{t("templates.clearAll")}
			</Button>
		</div>
	);
}
