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

import PageHeading from "@/components/common/PageHeading";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Spinner } from "@/components/feedback/Spinner";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";

import { TemplateDialog } from "./templates/components/TemplateDialog";
import { TemplateListRow } from "./templates/components/TemplateListRow";
import { TemplateSearchSortBar } from "./templates/components/TemplateSearchSortBar";
import { TemplateToolbar } from "./templates/components/TemplateToolbar";
import { useTemplateDialog } from "./templates/hooks/useTemplateDialog";
import { useTemplateImportExport } from "./templates/hooks/useTemplateImportExport";
import { useTemplates } from "./templates/hooks/useTemplates";

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
		searchQuery,
		setSearchQuery,
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
	} = useTemplateDialog({ call, showSnack, templatesRef, loadRows });

	const { importInputRef, doExport, handleImportFile, handleImportClick } =
		useTemplateImportExport({ call, loadRows, templatesRef });

	if (loading) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner />
			</div>
		);
	}

	// NF-R10-8: distinguish "no templates exist" (valid empty array from
	// backend) from "load failed" (backend unreachable or returned
	// malformed data). When the load genuinely failed AND we have no
	// templates to show (including from localStorage fallback), surface
	// a retry EmptyState instead of the "create your first template"
	// empty state — the latter is misleading when the real issue is a
	// backend connectivity problem.
	if (loadError && templates.length === 0) {
		return (
			<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
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
			<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
				<PageHeading
					title={t("templates.title")}
					description={t("templates.description")}
				>
					<TemplateToolbar
						importInputRef={importInputRef}
						onImportClick={handleImportClick}
						onImportFile={handleImportFile}
						// BG-63: forward the format chosen by the
						// ExportFormatMenu (json | csv) through to
						// doExport so the IPC bridge receives it.
						// Previously the arrow function
						// `() => doExport()` dropped the format arg,
						// silently making CSV export behave like JSON.
						onExport={(format) => doExport(format)}
						onAdd={openAddDialog}
						exportDisabled={templates.length === 0}
					/>
				</PageHeading>

				{templates.length > 0 && (
					<TemplateSearchSortBar
						searchQuery={searchQuery}
						onSearchChange={setSearchQuery}
						sortOrder={sortOrder}
						onSortOrderChange={setSortOrder}
					/>
				)}

				<div className="mt-4">
					{templates.length === 0 ? (
						<EmptyState
							icon={File02Icon}
							title={t("templates.emptyTitle")}
							description={t("templates.emptyDescription")}
							actionLabel={t("templates.createFirst")}
							onAction={openAddDialog}
						/>
					) : filteredSortedTemplates.length === 0 ? (
						// NH-15: search returned no matches — use the dedicated
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
						<div className="rounded-lg border border-border bg-(--bg-subtle) divide-y divide-border">
							{filteredSortedTemplates.map((row) => (
								<TemplateListRow
									key={row.id}
									row={row}
									onEdit={openEditDialog}
									onDelete={instantDeleteTemplate}
								/>
							))}
						</div>
					)}
				</div>

				{/* Count footer */}
				{templates.length > 0 && (
					<p className="mt-3 text-xs text-(--text-muted) text-center">
						{t(
							templates.length === 1
								? "templates.countSingular"
								: "templates.countPlural",
							{ count: String(templates.length) },
						)}
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
		</>
	);
}
