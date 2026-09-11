// Templates page, thin shell.
//
// Split from the former monolithic ``pages/Templates.tsx`` (1069 lines)
// into:
//   - ``./templates/lib/``       , pure helpers (types, storage, transform, sanitize)
//   - ``./templates/hooks/``     , state + handlers (useTemplates, useTemplateDialog, useTemplateImportExport)
//   - ``./templates/components/``, presentational (TemplateListRow, TemplateDialog)
//
// The Toolbar / BulkBar / ListHeader render through the SHARED
// collection-page family (components/common/Collection*.tsx), the page
// injects its i18n keys + drift-decision props (the replacement for the
// former per-page TemplateToolbar / TemplateBulkBar / TemplateListHeader
// mirrors, which were byte-identical except for those keys).
//
// This file owns ONLY the page layout (loading / load-error / empty /
// list / dialog wiring). All state + business logic lives in the hooks;
// all rendering lives in the components. Behaviour is preserved
// byte-for-byte, this is a pure structural refactor.
import { AlertCircleIcon, File02Icon } from "@hugeicons/core-free-icons";
import { useCallback, useEffect, useRef, useState } from "react";

import { CollectionBulkBar } from "@/components/common/CollectionBulkBar";
import { CollectionListHeader } from "@/components/common/CollectionListHeader";
import { CollectionToolbar } from "@/components/common/CollectionToolbar";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import PageHeading from "@/components/common/PageHeading";
import { EmptyState } from "@/components/feedback/EmptyState";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { TemplateDialog } from "./templates/components/TemplateDialog";
import { TemplateListRow } from "./templates/components/TemplateListRow";
import { TemplatesSkeleton } from "./templates/components/TemplatesSkeleton";
import { useTemplateDialog } from "./templates/hooks/useTemplateDialog";
import { useTemplateImportExport } from "./templates/hooks/useTemplateImportExport";
import { useTemplateSelection } from "./templates/hooks/useTemplateSelection";
import { useTemplates } from "./templates/hooks/useTemplates";
import { saveTemplates } from "./templates/lib/storage";
import { rowsToTemplates } from "./templates/lib/transform";
import type { TemplateRow } from "./templates/lib/types";

export default function TemplatesPage() {
	const { call } = usePython();
	const { showSnack } = useSnackbar();

	const {
		templates,
		loading,
		loadError,
		templatesRef,
		loadRows,
		instantDeleteTemplate,
		setTemplates,
		sortOrder,
		setSortOrder,
		filteredSortedTemplates,
	} = useTemplates({ call, showSnack });

	const {
		showDialog,
		editingTemplate,
		trigger,
		expansion,
		matchMode,
		openAddDialog,
		openEditDialog,
		saveTemplate,
		handleTriggerChange,
		handleExpansionChange,
		handleMatchModeChange,
		handleCloseDialog,
		insertVariable,
	} = useTemplateDialog({ call, showSnack, templatesRef, loadRows });

	const { importInputRef, doExport, handleImportFile, handleImportClick } =
		useTemplateImportExport({ call, loadRows, templatesRef });

	// Bulk selection + bulk delete (mirrors the Vocabulary page's
	// useVocabularySelection, same floating-bulk-bar UI).
	const selection = useTemplateSelection({
		templates,
		setTemplates,
		templatesRef,
		saveTemplatesList: async (updated) => {
			await saveTemplates(rowsToTemplates(updated), call);
			await loadRows();
		},
		showSnack,
	});

	// Soft display cap, the flat list renders at most this many rows
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

	//: "Clear All" wipes every template, gated by a confirmation
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

	if (loading) {
		return <TemplatesSkeleton />;
	}

	//distinguish "no templates exist" (valid empty array from
	// backend) from "load failed" (backend unreachable or returned
	// malformed data). When the load genuinely failed AND we have no
	// templates to show (including from localStorage fallback), surface
	// a retry EmptyState instead of the "create your first template"
	// empty state, the latter is misleading when the real issue is a
	// backend connectivity problem.
	if (loadError && templates.length === 0) {
		return (
			<div className="relative mx-auto flex min-h-full w-full max-w-4xl flex-col gap-6 px-16 pt-28 pb-6">
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
			<div className="relative mx-auto flex min-h-full w-full max-w-4xl flex-col gap-6 px-16 pt-28 pb-6">
				{/* Heading, then the toolbar on its OWN full-width row BELOW
                                    it (not inside PageHeading's children slot), mirrors
                                    the Vocabulary page layout exactly. */}
				<PageHeading
					title={t("templates.title")}
					description={t("templates.description")}
				/>
				<div className="flex flex-col gap-4">
					{/* Shared collection toolbar shell, the page injects
                                                every label key + the drift-decision values (Add is
                                                never disabled, the dialog owns its own save
                                                gating; JSON-only import accept because the
                                                templates parser reads JSON only). The hidden file
                                                input for Import renders inside the shell and
                                                re-uses the page hook's ref. */}
					<CollectionToolbar
						importInputRef={importInputRef}
						importAccept="application/json,.json"
						onImportClick={handleImportClick}
						onImportFile={handleImportFile}
						importAriaLabelKey="common.importAria"
						importLabelKey="common.import"
						importTitleKey="templates.importFormatHint"
						onExport={doExport}
						exportDisabled={templates.length === 0}
						onClearAll={() => setShowClearAllConfirm(true)}
						clearAllDisabled={templates.length === 0}
						clearAllAriaLabelKey="templates.clearAllAria"
						clearAllLabelKey="templates.clearAll"
						addAriaLabelKey="templates.addNewAria"
						addLabelKey="templates.addTemplate"
						onAdd={openAddDialog}
						sortOrder={sortOrder}
						onSortOrderChange={setSortOrder}
						hasEntries={templates.length > 0}
					/>

					<div className="flex flex-col gap-3">
						{templates.length === 0 ? (
							<EmptyState
								icon={File02Icon}
								title={t("templates.emptyTitle")}
								description={t("templates.emptyDescription")}
								actionLabel={t("templates.createFirst")}
								onAction={openAddDialog}
							/>
						) : filteredSortedTemplates.length === 0 ? (
							//search returned no matches, use the dedicated
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
								<div className="overflow-clip rounded-xl border border-border/5 bg-(--bg-subtle)">
									{/* Shared column-header shell, keys + the
                                                                                page-unique testid are injected.
                                                                                visibleIds is capped at displayCount to
                                                                                mirror the rows actually mounted. */}
									<CollectionListHeader
										testId="template-list-header"
										visibleIds={filteredSortedTemplates
											.slice(0, displayCount)
											.map((r) => r.id)}
										selectedIds={selection.selectedIds}
										onSelectAll={selection.setSelectMany}
										selectAllAriaKey="templates.selectAll"
										primaryColumnKey="templates.columnTrigger"
										secondaryColumnKey="templates.columnOutput"
										actionsColumnKey="templates.columnActions"
									/>
									<div className="divide-y divide-border/5">
										{filteredSortedTemplates
											.slice(0, displayCount)
											.map((row) => (
												<TemplateListRow
													key={row.id}
													row={row}
													selected={selection.selectedIds.has(row.id)}
													onToggleSelect={selection.toggleSelect}
													onEdit={handleEdit}
													onDelete={instantDeleteTemplate}
												/>
											))}
									</div>
								</div>
								{filteredSortedTemplates.length > displayCount && (
									<button
										type="button"
										data-testid="templates-show-more"
										onClick={() => setDisplayCount((c) => c + DISPLAY_CAP)}
										className="mx-auto flex items-center gap-2 rounded-full border border-border/5 bg-(--bg-subtle) px-4 py-1.5 text-xs font-medium text-accent transition-colors hover:border-accent/40 hover:bg-accent/5 cursor-pointer"
									>
										{t("templates.showMore")}
									</button>
								)}
							</>
						)}
					</div>
				</div>

				{/* Floating bulk bar, appears when templates are
                                    selected; rendered through the shared shell (keys +
                                    the page-unique testid are injected). Direct child
                                    of the page column (sticky bottom-4) so it stays
                                    pinned near the viewport bottom. */}
				{selection.selectedCount > 0 && (
					<CollectionBulkBar
						testId="template-bulk-bar"
						selectedCount={selection.selectedCount}
						selectedCountKey="templates.selectedCount"
						onDeleteSelected={selection.bulkDeleteSelected}
						deleteLabelKey="templates.bulkDelete"
						exportSelectedKey="templates.exportSelected"
						onExportSelected={(format) =>
							doExport(format, selection.selectedRows)
						}
						deselectAllKey="templates.deselectAll"
						onClearSelection={selection.clearSelection}
					/>
				)}
			</div>

			{/* Add/Edit Dialog, migrated to shared Modal (F-3) */}
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
				onInsertVariable={insertVariable}
			/>

			{/* Clear All, confirmation gated (mirrors Vocabulary). */}
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
