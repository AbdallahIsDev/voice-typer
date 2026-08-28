// Templates page — thin shell.
//
// Split from the former monolithic ``pages/Templates.tsx`` (1069 lines)
// into:
//   - ``./templates/lib/``        — pure helpers (types, storage, transform, sanitize)
//   - ``./templates/hooks/``      — state + handlers (useTemplates, useTemplateDialog, useTemplateImportExport)
//   - ``./templates/components/`` — presentational (TemplateToolbar, TemplateSearchSortBar, TemplateListRow, TemplateDialog)
//
// This file owns ONLY the page layout (loading / load-error / empty /
// list / dialog wiring). All state + business logic lives in the hooks;
// all rendering lives in the components. Behaviour is preserved
// byte-for-byte — this is a pure structural refactor.
import { AlertCircleIcon, File02Icon } from "@hugeicons/core-free-icons";
import { useCallback, useEffect, useRef, useState } from "react";

import ConfirmDialog from "@/components/common/ConfirmDialog";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import PageHeading from "@/components/common/PageHeading";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Spinner } from "@/components/feedback/Spinner";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t, tChoice } from "@/i18n/i18n";

import { TemplateDialog } from "./templates/components/TemplateDialog";
import { TemplateInlineForm } from "./templates/components/TemplateInlineForm";
import { TemplateListRow } from "./templates/components/TemplateListRow";
import { TemplateSortBar } from "./templates/components/TemplateSearchSortBar";
import { TemplateToolbar } from "./templates/components/TemplateToolbar";
import { useTemplateDialog } from "./templates/hooks/useTemplateDialog";
import { useTemplateImportExport } from "./templates/hooks/useTemplateImportExport";
import { useTemplateQuickAdd } from "./templates/hooks/useTemplateQuickAdd";
import { useTemplates } from "./templates/hooks/useTemplates";
import { saveTemplates } from "./templates/lib/storage";
import type { TemplateRow } from "./templates/lib/types";

export default function TemplatesPage() {
	const { call } = usePython();
	const { showSnack } = useSnackbar();
	const { agoLabel, markUpdated, refreshing, withRefresh } = useLastUpdated();

	const {
		templates,
		loading,
		loadError,
		templatesRef,
		loadRows,
		instantDeleteTemplate,
		sortOrder,
		setSortOrder,
		filteredSortedTemplates,
	} = useTemplates({ call, showSnack, markUpdated });

	const {
		showDialog,
		editingTemplate,
		trigger,
		expansion,
		matchMode,
		// openAddDialog intentionally NOT destructured — Add-Template now
		// opens the inline quick-add row; only the Edit flow still
		// uses the dialog.
		openEditDialog,
		saveTemplate,
		handleTriggerChange,
		handleExpansionChange,
		handleMatchModeChange,
		handleCloseDialog,
	} = useTemplateDialog({ call, showSnack, templatesRef, loadRows });

	const { importInputRef, doExport, handleImportFile, handleImportClick } =
		useTemplateImportExport({ call, loadRows, templatesRef });

	// Inline quick-add row above the templates list. Mirrors
	// Vocabulary's ``useVocabularyQuickAdd`` so create stays in-place
	// (no modal). Edit still flows through ``useTemplateDialog`` +
	// ``TemplateDialog`` — the inline pattern is intentionally ONLY for
	// add (the common case); power users who need to change match mode
	// use the Edit dialog.
	const quickAdd = useTemplateQuickAdd({
		call,
		showSnack,
		templatesRef,
		loadRows,
	});

	// Soft display cap — the flat list renders at most this many rows
	// until the user clicks "Show more".  Keeps very large template
	// collections from mounting thousands of DOM rows.
	const DISPLAY_CAP = 200;
	const [displayCount, setDisplayCount] = useState(DISPLAY_CAP);

	// ``openEditDialog`` from ``useTemplateDialog`` is a plain
	// function (recreated every render).  Wrap it in a stable
	// ``useCallback`` via the "latest ref" pattern so the memo'd
	// ``TemplateListRow`` sees a referentially-stable ``onEdit`` prop and
	// skips re-rendering on unrelated state changes (e.g. search
	// keystrokes).  Mirrors the ``ActivityListRow`` stable-callback
	// pattern (components/dashboard/ActivityList.tsx:74).
	const openEditDialogRef = useRef(openEditDialog);
	useEffect(() => {
		openEditDialogRef.current = openEditDialog;
	});
	const handleEdit = useCallback((row: TemplateRow) => {
		openEditDialogRef.current(row);
	}, []);

	//: "Clear All" wipes every template — gated by a confirmation
	// dialog (an irreversible, privacy-adjacent action). Clears via the
	// existing persistence path (save an empty list), then reloads so
	// the UI reflects the backend.
	const [showClearAllConfirm, setShowClearAllConfirm] = useState(false);
	const handleClearAllConfirm = useCallback(async () => {
		setShowClearAllConfirm(false);
		try {
			await saveTemplates([], call);
			loadRows();
			showSnack(t("templates.allCleared"), "success");
		} catch (err) {
			console.error("[renderer:Templates] Failed to clear all templates:", err);
			showSnack(t("templates.clearFailed"), "error");
		}
	}, [call, loadRows, showSnack]);

	// F4: manual refresh for the LastUpdatedIndicator. Wraps loadRows in
	// the hook's withRefresh so the refreshing flag clears on error too.
	// The timestamp bump itself lives inside useTemplates' loadRows
	// (markUpdated), so every load path (mount, retry, delete undo,
	// clear-all) updates the indicator.
	const handleRefresh = useCallback(
		() => withRefresh(loadRows),
		[loadRows, withRefresh],
	);

	if (loading) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner label={t("templates.loading")} />
			</div>
		);
	}

	//distinguish "no templates exist" (valid empty array from
	// backend) from "load failed" (backend unreachable or returned
	// malformed data). When the load genuinely failed AND we have no
	// templates to show (including from localStorage fallback), surface
	// a retry EmptyState instead of the "create your first template"
	// empty state — the latter is misleading when the real issue is a
	// backend connectivity problem.
	if (loadError && templates.length === 0) {
		return (
			<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col px-16 pt-28 pb-6">
				<PageHeading
					title={t("templates.title")}
					description={t("templates.description")}
				/>
				<EmptyState
					icon={AlertCircleIcon}
					title={t("templates.loadFailedTitle")}
					description={loadError}
					actionLabel={t("templates.retry")}
					onAction={() => loadRows()}
					variant="error"
				/>
			</div>
		);
	}

	return (
		<>
			<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col px-16 pt-28 pb-6">
				<PageHeading
					title={t("templates.title")}
					description={t("templates.description")}
				>
					<TemplateToolbar
						importInputRef={importInputRef}
						onImportClick={handleImportClick}
						onImportFile={handleImportFile}
						//forward the format chosen by the
						// ExportFormatMenu (json | csv) through to
						// doExport so the IPC bridge receives it.
						// Previously the arrow function
						// `() => doExport()` dropped the format arg,
						// silently making CSV export behave like JSON.
						onExport={(format) => doExport(format)}
						// Add-Template opens the inline quick-add
						// row (mirrors Vocabulary). The Edit dialog is
						// still wired via the row's pencil icon (no
						// regression for edit).
						onAdd={quickAdd.openQuickAdd}
						exportDisabled={templates.length === 0}
						onClearAll={() => setShowClearAllConfirm(true)}
						clearAllDisabled={templates.length === 0}
					/>
				</PageHeading>

				<div className="flex justify-end pb-2">
					<LastUpdatedIndicator
						agoLabel={agoLabel}
						onRefresh={handleRefresh}
						refreshing={refreshing}
					/>
				</div>

				{templates.length > 0 && (
					<TemplateSortBar
						sortOrder={sortOrder}
						onSortOrderChange={setSortOrder}
					/>
				)}

				{/* Inline quick-add row. NOT gated on
                                    templates.length — "Add Template" must work from the
                                    empty state too (mirrors Vocabulary). */}
				{quickAdd.open && (
					<div className="mt-4">
						<TemplateInlineForm
							trigger={quickAdd.trigger}
							expansion={quickAdd.expansion}
							error={quickAdd.error}
							onTriggerChange={quickAdd.setTrigger}
							onExpansionChange={quickAdd.setExpansion}
							onSave={quickAdd.saveQuickAdd}
							onCancel={quickAdd.closeQuickAdd}
						/>
					</div>
				)}

				<div className="mt-4">
					{templates.length === 0 ? (
						<EmptyState
							icon={File02Icon}
							title={t("templates.emptyTitle")}
							description={t("templates.emptyDescription")}
							actionLabel={t("templates.createFirst")}
							onAction={quickAdd.openQuickAdd}
						/>
					) : filteredSortedTemplates.length === 0 ? (
						//search returned no matches — use the dedicated
						// templates.noResults / templates.noResultsDescription
						// keys instead of borrowing history.noResultsDescription
						// (cross-module coupling) and the misleading
						// templates.emptyTitle ("No templates yet").
						<EmptyState
							icon={File02Icon}
							title={t("templates.noResults")}
							description={t("templates.noResultsDescription")}
						/>
					) : (
						<>
							<ul className="overflow-hidden rounded-xl border border-border/10 bg-(--bg-subtle) divide-y divide-border/10">
								{filteredSortedTemplates.slice(0, displayCount).map((row) => (
									<TemplateListRow
										key={row.id}
										row={row}
										onEdit={handleEdit}
										onDelete={instantDeleteTemplate}
									/>
								))}
							</ul>
							{filteredSortedTemplates.length > displayCount && (
								<button
									type="button"
									data-testid="templates-show-more"
									onClick={() => setDisplayCount((c) => c + DISPLAY_CAP)}
									className="mx-auto mt-3 flex items-center gap-1.5 rounded-full border border-border/10 bg-(--bg-subtle) px-4 py-1.5 text-xs font-medium text-accent transition-colors hover:border-accent/40 hover:bg-accent/5 cursor-pointer"
								>
									{t("templates.showMore")}
								</button>
							)}
						</>
					)}
				</div>

				{/* Count footer */}
				{templates.length > 0 && (
					<p className="mt-3 text-xs text-(--text-muted) text-center">
						{tChoice("templates.count", templates.length)}
					</p>
				)}
			</div>

			{/* Add/Edit Dialog — migrated to shared Modal (F-3) */}
			<TemplateDialog
				open={showDialog}
				editingTemplate={editingTemplate}
				trigger={trigger}
				expansion={expansion}
				matchMode={matchMode}
				onTriggerChange={handleTriggerChange}
				onExpansionChange={handleExpansionChange}
				onMatchModeChange={handleMatchModeChange}
				onClose={handleCloseDialog}
				onSave={saveTemplate}
			/>

			{/* Clear All — confirmation gated (mirrors Vocabulary). */}
			<ConfirmDialog
				open={showClearAllConfirm}
				title={t("templates.clearAllTitle")}
				message={t("templates.clearAllMessage")}
				confirmLabel={t("templates.clearAllConfirm")}
				cancelLabel={t("common.cancel")}
				variant="destructive"
				dismissOnBackdrop
				onConfirm={handleClearAllConfirm}
				onCancel={() => setShowClearAllConfirm(false)}
			/>
		</>
	);
}
