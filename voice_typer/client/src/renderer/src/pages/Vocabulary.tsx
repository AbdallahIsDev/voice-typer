// Vocabulary page — thin shell.
//
// Split from the former monolithic ``pages/Vocabulary.tsx`` (1053 lines)
// into:
//   - ``./vocabulary/lib/``        — pure helpers (categories, transform, sort, importExport, testCorrection)
//   - ``./vocabulary/hooks/``      — state + handlers (useVocabulary, useVocabularyDialog, useVocabularyImportExport, useVocabularyQuickAdd, useVocabularySelection)
//   - ``./vocabulary/components/`` — presentational (VocabToolbar, VocabSearchFilterBar, VocabListRow, VocabListHeader, VocabBulkBar, VocabQuickAdd, VocabTestPanel, VocabDialog)
//
// This file owns ONLY the page layout (loading / load-error / empty /
// list / dialog wiring). All state + business logic lives in the hooks;
// all rendering lives in the components.
//
// The page is a flat two-column correction list: wrong word/phrase on
// the left, corrected on the right. Categories are part of the
// persisted data layer only — they are never surfaced in the UI.
import { AlertCircleIcon, BookOpen02Icon } from "@hugeicons/core-free-icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import PageHeading from "@/components/common/PageHeading";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Spinner } from "@/components/feedback/Spinner";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t, useT } from "@/i18n/i18n";

import { VocabBulkBar } from "./vocabulary/components/VocabBulkBar";
import { VocabDialog } from "./vocabulary/components/VocabDialog";
import { VocabDuplicateBanner } from "./vocabulary/components/VocabDuplicateBanner";
import { VocabListHeader } from "./vocabulary/components/VocabListHeader";
import { VocabListRow } from "./vocabulary/components/VocabListRow";
import { VocabQuickAdd } from "./vocabulary/components/VocabQuickAdd";
import { VocabSearchFilterBar } from "./vocabulary/components/VocabSearchFilterBar";
import { VocabTestPanel } from "./vocabulary/components/VocabTestPanel";
import { VocabToolbar } from "./vocabulary/components/VocabToolbar";
import { useVocabulary } from "./vocabulary/hooks/useVocabulary";
import { useVocabularyDialog } from "./vocabulary/hooks/useVocabularyDialog";
import { useVocabularyImportExport } from "./vocabulary/hooks/useVocabularyImportExport";
import { useVocabularyQuickAdd } from "./vocabulary/hooks/useVocabularyQuickAdd";
import { useVocabularySelection } from "./vocabulary/hooks/useVocabularySelection";
import { useVocabularyTester } from "./vocabulary/hooks/useVocabularyTester";
import {
	findDuplicateGroups,
	normalizeWrongPhrase,
	type VocabRow,
} from "./vocabulary/lib/transform";

export default function VocabularyPage() {
	const { call } = usePython();
	const { showSnack } = useSnackbar();
	const { agoLabel, markUpdated } = useLastUpdated();

	//: soft display cap — the flat list renders at most this many rows
	// until the user clicks "Show more". Keeps very large vocabularies
	// (thousands of entries) from mounting thousands of DOM rows.
	const DISPLAY_CAP = 200;
	const [displayCount, setDisplayCount] = useState(DISPLAY_CAP);

	//: "Clear All" is gated by a confirmation dialog — granting it
	// wipes every entry (an irreversible privacy-adjacent action).
	const [showClearConfirm, setShowClearConfirm] = useState(false);

	const handleClearAllConfirm = async () => {
		setShowClearConfirm(false);
		try {
			await persistVocabulary([]);
			setEntries([]);
			showSnack(t("vocabulary.clearAllToast"), "success");
		} catch (err) {
			console.error("[renderer:Vocabulary] Failed to clear vocabulary:", err);
			showSnack(t("vocabulary.clearAllFailed"), "error");
		}
	};

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
		filteredSorted,
	} = useVocabulary({ call, showSnack });

	//: mark the data as fresh once the page has finished a load (or an
	// empty successful load) — the indicator shows "Just now" after load.
	const prevLoadingRef = useRef(true);
	useEffect(() => {
		if (!loading && !loadError && prevLoadingRef.current) {
			markUpdated();
		}
		prevLoadingRef.current = loading;
	}, [loading, loadError, markUpdated]);

	// Subscribe to locale changes via useT() (a useSyncExternalStore
	// wrapper) so this component re-renders when the locale switches
	// and every t() call re-resolves against the new locale.
	useT();

	const {
		showDialog,
		editingEntry,
		trigger,
		replacement,
		openEditDialog,
		saveEntry,
		handleTriggerChange,
		handleReplacementChange,
		handleCloseDialog,
	} = useVocabularyDialog({
		entries,
		setEntries,
		persistVocabulary,
		showSnack,
	});

	// Inline quick-add row (replaces the disconnected Add modal — the
	// list stays visible while adding).
	const quickAdd = useVocabularyQuickAdd({
		entries,
		setEntries,
		persistVocabulary,
		showSnack,
	});

	// Bulk selection + bulk delete.
	const selection = useVocabularySelection({
		entries,
		setEntries,
		entriesRef,
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

	// Stable callbacks for the memo'd VocabListRow.
	const openEditDialogRef = useRef(openEditDialog);
	useEffect(() => {
		openEditDialogRef.current = openEditDialog;
	});
	const handleEdit = useCallback((entry: VocabRow) => {
		openEditDialogRef.current(entry);
	}, []);

	// "Test corrections" panel state.
	const [testOpen, setTestOpen] = useState(false);
	const [testQuery, setTestQuery] = useState("");

	// "Test corrections" panel — the preview runs against the LIVE
	// backend correction engine (debounced) with a client-mirror
	// fallback; see useVocabularyTester.
	const tester = useVocabularyTester({ call, entries, query: testQuery });

	// One-time cleanup: surface PRE-EXISTING duplicates (same wrong
	// phrase, case-insensitive) already in the list so the user can
	// resolve them. New duplicates are blocked by the backend write
	// path; this banner covers data that predates that check (e.g.
	// hand-edited JSON, double-imports). Dismissible per session.
	const duplicateGroups = useMemo(
		() => findDuplicateGroups(entries),
		[entries],
	);
	const duplicateCount = duplicateGroups.reduce(
		(sum, g) => sum + g.entries.length - 1,
		0,
	);
	const [duplicateBannerDismissed, setDuplicateBannerDismissed] =
		useState(false);
	const showDuplicateBanner = duplicateCount > 0 && !duplicateBannerDismissed;

	const handleRemoveDuplicates = async () => {
		try {
			const keepIds = new Set(
				duplicateGroups.map((g) => g.entries[0]?._id).filter(Boolean),
			);
			const cleaned = entries.filter((e) => {
				const group = duplicateGroups.find(
					(g) => normalizeWrongPhrase(e.original) === g.phrase,
				);
				return !group || keepIds.has(e._id);
			});
			if (cleaned.length === entries.length) return;
			await persistVocabulary(cleaned);
			setEntries(cleaned);
			selection.clearSelection();
			setDuplicateBannerDismissed(true);
			showSnack(
				t("vocabulary.duplicatesRemoved", {
					count: String(entries.length - cleaned.length),
				}),
				"success",
			);
		} catch {
			showSnack(t("vocabulary.saveFailed"), "error");
		}
	};

	const handleClearSearch = useCallback(() => {
		setSearchQuery("");
	}, [setSearchQuery]);

	if (loading) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner />
			</div>
		);
	}

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
					variant="error"
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
					<LastUpdatedIndicator
						agoLabel={agoLabel}
						onRefresh={loadVocabulary}
						refreshing={loading}
						className="mb-2"
					/>
					<VocabToolbar
						importInputRef={importInputRef}
						onImportClick={handleImportClick}
						onImportFile={handleImportFile}
						onExport={doExport}
						onAdd={() => quickAdd.openQuickAdd()}
						exportDisabled={entries.length === 0}
						addDisabled={saving}
						onClearAll={() => setShowClearConfirm(true)}
						clearAllDisabled={entries.length === 0}
					/>
				</PageHeading>

				{/* Live entry count — updates as entries are added/removed
				    and as the search filter narrows the visible list. */}
				{entries.length > 0 && (
					<p
						data-testid="vocab-entry-count"
						className="mt-4 text-xs font-medium text-(--text-muted)"
					>
						{searchQuery.trim() && filteredSorted.length !== entries.length
							? t("vocabulary.entryCountFiltered", {
									shown: String(filteredSorted.length),
									total: String(entries.length),
								})
							: t(
									entries.length === 1
										? "vocabulary.entryCountSingular"
										: "vocabulary.entryCountPlural",
									{ count: String(entries.length) },
								)}
					</p>
				)}

				{/* Pre-existing duplicates review banner. */}
				{showDuplicateBanner && (
					<VocabDuplicateBanner
						count={duplicateCount}
						onRemoveDuplicates={handleRemoveDuplicates}
						onDismiss={() => setDuplicateBannerDismissed(true)}
					/>
				)}

				{entries.length > 0 && (
					<VocabSearchFilterBar
						searchQuery={searchQuery}
						onSearchChange={setSearchQuery}
						sortOrder={sortOrder}
						onSortOrderChange={setSortOrder}
					/>
				)}

				{/* Live correction tester — collapsible, above the list. The
				    output runs through the backend engine (see
				    useVocabularyTester). */}
				{entries.length > 0 && (
					<VocabTestPanel
						open={testOpen}
						onOpenChange={setTestOpen}
						query={testQuery}
						onQueryChange={setTestQuery}
						output={tester.output}
						applied={tester.applied}
						pending={tester.pending}
					/>
				)}

				{/* Inline quick-add row. NOT gated on entries.length —
					"Add Word" must work from the empty state too. */}
				{quickAdd.open && (
					<div className="mt-4">
						<VocabQuickAdd
							trigger={quickAdd.trigger}
							replacement={quickAdd.replacement}
							error={quickAdd.error}
							onTriggerChange={quickAdd.setTrigger}
							onReplacementChange={quickAdd.setReplacement}
							onSave={quickAdd.saveQuickAdd}
							onCancel={quickAdd.closeQuickAdd}
						/>
					</div>
				)}

				<div className="mt-4">
					{entries.length === 0 ? (
						<EmptyState
							icon={BookOpen02Icon}
							title={t("vocabulary.emptyTitle")}
							description={t("vocabulary.emptyDescription")}
							actionLabel={t("vocabulary.addFirstWord")}
							onAction={() => quickAdd.openQuickAdd()}
						/>
					) : filteredSorted.length === 0 ? (
						<EmptyState
							icon={BookOpen02Icon}
							title={t("vocabulary.noResults")}
							description={t("vocabulary.noResultsDescription")}
							actionLabel={t("vocabulary.clearFilters")}
							onAction={handleClearSearch}
						/>
					) : (
						<>
							<div className="rounded-xl border border-border/10 bg-(--bg-subtle)">
								<VocabListHeader
									visibleIds={filteredSorted.map((e) => e._id)}
									selectedIds={selection.selectedIds}
									onSelectAll={selection.setSelectMany}
								/>
								<div className="overflow-hidden rounded-b-xl">
									<div className="divide-y divide-border/10">
										{filteredSorted.slice(0, displayCount).map((entry) => (
											<VocabListRow
												key={entry._id}
												entry={entry}
												selected={selection.selectedIds.has(entry._id)}
												onToggleSelect={selection.toggleSelect}
												onEdit={handleEdit}
												onDelete={instantDeleteEntry}
											/>
										))}
									</div>
								</div>
							</div>
							{filteredSorted.length > displayCount && (
								<button
									type="button"
									onClick={() => setDisplayCount((c) => c + DISPLAY_CAP)}
									className="mx-auto mt-3 flex items-center gap-1.5 rounded-full border border-border/10 bg-(--bg-subtle) px-4 py-1.5 text-xs font-medium text-accent transition-colors hover:border-accent/40 hover:bg-accent/5 cursor-pointer"
								>
									{t("vocabulary.showMore")}
								</button>
							)}
						</>
					)}
				</div>

				{/* Floating bulk bar — appears when rows are selected. */}
				{selection.selectedCount > 0 && (
					<div className="pointer-events-none absolute inset-x-0 bottom-0 z-20 flex justify-center px-6 pb-4">
						<div className="pointer-events-auto">
							<VocabBulkBar
								selectedCount={selection.selectedCount}
								onDeleteSelected={selection.bulkDeleteSelected}
								onExportSelected={(format) =>
									doExport(format, selection.selectedRows)
								}
								onClearSelection={selection.clearSelection}
							/>
						</div>
					</div>
				)}
			</div>

			<ConfirmDialog
				open={showClearConfirm}
				title={t("vocabulary.clearAllTitle")}
				message={t("vocabulary.clearAllMessage")}
				confirmLabel={t("vocabulary.clearAllConfirm")}
				cancelLabel={t("common.cancel")}
				variant="destructive"
				onConfirm={handleClearAllConfirm}
				onCancel={() => setShowClearConfirm(false)}
			/>

			<VocabDialog
				open={showDialog}
				editingEntry={editingEntry}
				trigger={trigger}
				replacement={replacement}
				onTriggerChange={handleTriggerChange}
				onReplacementChange={handleReplacementChange}
				onClose={handleCloseDialog}
				onSave={saveEntry}
			/>
		</>
	);
}
