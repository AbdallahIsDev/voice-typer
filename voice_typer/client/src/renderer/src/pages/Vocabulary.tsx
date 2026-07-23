// Vocabulary page — thin shell.
//
// Split from the former monolithic ``pages/Vocabulary.tsx`` (1053 lines)
// into:
//   - ``./vocabulary/lib/``        — pure helpers (categories, transform, sort, importExport)
//   - ``./vocabulary/hooks/``      — state + handlers (useVocabulary, useVocabularyDialog, useVocabularyImportExport)
//   - ``./vocabulary/components/`` — presentational (VocabToolbar, VocabSearchFilterBar, VocabListRow, VocabDialog)
//
// This file owns ONLY the page layout (loading / load-error / empty /
// list / dialog wiring). All state + business logic lives in the hooks;
// all rendering lives in the components. Behaviour is preserved
// byte-for-byte — this is a pure structural refactor.
import { AlertCircleIcon, BookOpen02Icon } from "@hugeicons/core-free-icons";

import PageHeading from "@/components/common/PageHeading";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Spinner } from "@/components/feedback/Spinner";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";

import { VocabDialog } from "./vocabulary/components/VocabDialog";
import { VocabListRow } from "./vocabulary/components/VocabListRow";
import { VocabSearchFilterBar } from "./vocabulary/components/VocabSearchFilterBar";
import { VocabToolbar } from "./vocabulary/components/VocabToolbar";
import { useVocabulary } from "./vocabulary/hooks/useVocabulary";
import { useVocabularyDialog } from "./vocabulary/hooks/useVocabularyDialog";
import { useVocabularyImportExport } from "./vocabulary/hooks/useVocabularyImportExport";
import { getCategoryLabels } from "./vocabulary/lib/categories";

export default function VocabularyPage() {
	const { call } = usePython();
	const { showSnack } = useSnackbar();

	const {
		entries,
		loading,
		loadError,
		saving,
		entriesRef,
		loadVocabulary,
		persistVocabulary,
		instantDeleteEntry,
		setEntries,
		searchQuery,
		setSearchQuery,
		sortOrder,
		setSortOrder,
		categoryFilter,
		setCategoryFilter,
		filteredSorted,
	} = useVocabulary({ call, showSnack });

	const {
		showDialog,
		editingEntry,
		trigger,
		replacement,
		category,
		openAddDialog,
		openEditDialog,
		saveEntry,
		handleTriggerChange,
		handleReplacementChange,
		handleCloseDialog,
		setCategory,
	} = useVocabularyDialog({
		entries,
		setEntries,
		persistVocabulary,
		showSnack,
	});

	const { importInputRef, doExport, handleImportFile, handleImportClick } =
		useVocabularyImportExport({
			call,
			entriesRef,
			persistVocabulary,
			setEntries,
		});

	// CR-37: resolve category labels at render time so locale switches
	// re-resolve the t() keys against the new locale.
	const categoryLabels = getCategoryLabels();

	if (loading) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner />
			</div>
		);
	}

	// NF-R10-1 / fix #8: distinguish "backend failed to load" from
	// "vocabulary is genuinely empty" so the user knows to retry
	// instead of being presented with the add-first-word empty state.
	// Matches the History/Templates retry pattern.
	if (loadError && entries.length === 0) {
		return (
			<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
				<PageHeading
					title={t("vocabulary.title")}
					description={t("vocabulary.description")}
				/>
				<EmptyState
					icon={AlertCircleIcon}
					title={t("vocabulary.loadFailedTitle")}
					description={loadError}
					actionLabel={t("vocabulary.retry")}
					onAction={() => loadVocabulary()}
				/>
			</div>
		);
	}

	return (
		<>
			<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
				<PageHeading
					title={t("vocabulary.title")}
					description={t("vocabulary.description")}
				>
					<VocabToolbar
						importInputRef={importInputRef}
						onImportClick={handleImportClick}
						onImportFile={handleImportFile}
						onExport={doExport}
						onAdd={openAddDialog}
						exportDisabled={entries.length === 0}
						addDisabled={saving}
					/>
				</PageHeading>

				{entries.length > 0 && (
					<VocabSearchFilterBar
						searchQuery={searchQuery}
						onSearchChange={setSearchQuery}
						categoryFilter={categoryFilter}
						onCategoryFilterChange={setCategoryFilter}
						sortOrder={sortOrder}
						onSortOrderChange={setSortOrder}
						categoryLabels={categoryLabels}
					/>
				)}

				<div className="mt-4">
					{entries.length === 0 ? (
						<EmptyState
							icon={BookOpen02Icon}
							title={t("vocabulary.emptyTitle")}
							description={t("vocabulary.emptyDescription")}
							actionLabel={t("vocabulary.addFirstWord")}
							onAction={openAddDialog}
						/>
					) : filteredSorted.length === 0 ? (
						<EmptyState
							icon={BookOpen02Icon}
							title={t("vocabulary.noResults")}
						/>
					) : (
						<div className="rounded-lg border border-border bg-(--bg-subtle) divide-y divide-border">
							{filteredSorted.map((entry) => (
								<VocabListRow
									key={entry._id}
									entry={entry}
									categoryLabels={categoryLabels}
									onEdit={openEditDialog}
									onDelete={instantDeleteEntry}
								/>
							))}
						</div>
					)}
				</div>

				{/* Count footer */}
				{entries.length > 0 && !searchQuery.trim() && (
					<p className="mt-3 text-xs text-(--text-muted) text-center">
						{t(
							entries.length === 1
								? "vocabulary.entryCountSingular"
								: "vocabulary.entryCountPlural",
							{ count: String(entries.length) },
						)}
					</p>
				)}
			</div>

			{/* Add/Edit Dialog — migrated to shared Modal (F-3) */}
			<VocabDialog
				open={showDialog}
				editingEntry={editingEntry}
				trigger={trigger}
				replacement={replacement}
				category={category}
				onTriggerChange={handleTriggerChange}
				onReplacementChange={handleReplacementChange}
				onCategoryChange={setCategory}
				onClose={handleCloseDialog}
				onSave={saveEntry}
				categoryLabels={categoryLabels}
			/>
		</>
	);
}
