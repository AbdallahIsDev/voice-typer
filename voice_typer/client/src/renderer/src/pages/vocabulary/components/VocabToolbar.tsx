// Toolbar (Import / Export / Add / Clear All buttons) for the
// Vocabulary page.
//
// Extracted from the former monolithic ``pages/Vocabulary.tsx``. The
// hidden ``<input type="file">`` for Import is rendered once here and
// re-used — its ``value`` is reset after each ``onChange`` so re-
// selecting the same file fires the event again (otherwise the OS
// picker suppresses the event if the path is unchanged).
//
// Hierarchy (all four buttons carry an icon + label): Import / Export /
// Clear All are grouped as secondary outline actions in their own flex
// container; "Add Word" sits OUTSIDE that group as a sibling, and the
// parent row uses ``justify-between`` (with an explicit ``w-full`` so
// the row always spans the full column width) so Add Word (the PRIMARY
// action, filled accent button) is pushed to the far right while the
// other three stay grouped together on the left. No divider pipes
// between buttons — spacing + the primary/secondary split carry the
// grouping.
//
// Full-width note: ``justify-between`` only distributes space when the
// flex container is WIDER than its children's combined content. The
// toolbar therefore MUST be rendered in a full-width parent (it is a
// direct child of the page column, NOT inside PageHeading's
// content-sized, shrink-0 action wrapper — that wrapper is exactly why
// three earlier attempts never separated the groups).

import {
	Add01Icon,
	Delete01Icon,
	Upload01Icon,
} from "@hugeicons/core-free-icons";
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
		<div className="flex w-full flex-wrap items-center justify-between gap-2">
			{/* Secondary-action group (Import / Export / Clear All) — one
                            flex container so the three stay clustered on the left
                            while the primary Add Word action is pushed to the far
                            right by the parent's justify-between. */}
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
					// Surface the expected file format
					// schema on hover so the user knows what shape
					// the import expects without trial-and-error.
					title={t("vocabulary.importFormatHint")}
					className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
				>
					<HugeiconsIcon
						icon={Upload01Icon}
						strokeWidth={2}
						aria-hidden="true"
						className="size-4"
					/>
					{t("common.import")}
				</Button>
				<ExportFormatMenu onExport={onExport} disabled={exportDisabled} />
				<Button
					variant="outline"
					size="sm"
					onClick={onClearAll}
					disabled={clearAllDisabled}
					// Destructive affordance for the "clear every entry"
					// action. At rest: muted text/border like Import/Export.
					// On hover the BACKGROUND turns red (the color change
					// lives on the fill, not the label) while the icon +
					// text brighten to the primary text colour — matching
					// the muted-at-rest → bright-on-hover pattern of the
					// other header buttons. (Previously the hover turned
					// text + border red on an unchanged background — the
					// inverse of what a destructive hover should read as.)
					className="gap-2 text-(--text-muted) hover:border-destructive hover:bg-destructive hover:text-(--text-primary)"
					aria-label={t("vocabulary.clearAllAria")}
					title={t("vocabulary.clearAllAria")}
				>
					<HugeiconsIcon
						icon={Delete01Icon}
						strokeWidth={2}
						aria-hidden="true"
						className="size-4"
					/>
					{t("vocabulary.clearAll")}
				</Button>
			</div>
			{/* Primary action — filled accent button, pushed to the far end
                            of the row (justify-between) so it reads as THE action on
                            this page, distinct from the Import/Export/Clear All
                            cluster on the left. `ms-auto` is the flexbox auto-margin:
                            it absorbs ALL free space ahead of it, which on a single
                            row is identical to space-between (group flush left, Add
                            Word flush right) AND on a narrow wrapped row keeps Add
                            Word pinned to the right edge instead of falling to the
                            left — auto margins are width-scaling alignment, not a
                            fixed gap, so nothing breaks at different content lengths
                            or window sizes. */}
			<Button
				variant="default"
				size="sm"
				onClick={onAdd}
				disabled={addDisabled}
				className="ms-auto gap-2"
			>
				<HugeiconsIcon
					icon={Add01Icon}
					strokeWidth={2}
					aria-hidden="true"
					className="size-4"
				/>
				{t("vocabulary.addWord")}
			</Button>
		</div>
	);
}
