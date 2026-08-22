// Vocabulary page — thin shell.
//
// Split from the former monolithic ``pages/Vocabulary.tsx`` (1053 lines)
// into:
//   - ``./vocabulary/lib/``        — pure helpers (categories, transform, sort, importExport)
//   - ``./vocabulary/hooks/``      — state + handlers (useVocabulary, useVocabularyEdit, useVocabularyImportExport, useVocabularyQuickAdd, useVocabularySelection)
//   - ``./vocabulary/components/`` — presentational (VocabToolbar, VocabSearchFilterBar, VocabListRow, VocabListHeader, VocabBulkBar, VocabInlineForm)
//
// This file owns ONLY the page layout (loading / load-error / empty /
// list / inline-form wiring). All state + business logic lives in the
// hooks; all rendering lives in the components.
//
// Add and Edit use the SAME inline-row pattern (VocabInlineForm): Add
// renders the row above the table, Edit replaces the edited row in
// place — the old edit modal was removed so there is one consistent
// create/modify flow.
//
// The page is a flat two-column correction list: wrong word/phrase on
// the left, corrected on the right. Categories are part of the
// persisted data layer only — they are never surfaced in the UI.

import {
	AlertCircleIcon,
	BookOpen02Icon,
	PencilEdit02Icon,
} from "@hugeicons/core-free-icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import PageHeading from "@/components/common/PageHeading";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Spinner } from "@/components/feedback/Spinner";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t, useT } from "@/i18n/i18n";
import { VocabBulkBar } from "./vocabulary/components/VocabBulkBar";
import { VocabDuplicateBanner } from "./vocabulary/components/VocabDuplicateBanner";
import { VocabInlineForm } from "./vocabulary/components/VocabInlineForm";
import { VocabListHeader } from "./vocabulary/components/VocabListHeader";
import { VocabListRow } from "./vocabulary/components/VocabListRow";
import { VocabSearchFilterBar } from "./vocabulary/components/VocabSearchFilterBar";
import { VocabToolbar } from "./vocabulary/components/VocabToolbar";
import { usageKey, useVocabulary } from "./vocabulary/hooks/useVocabulary";
import { useVocabularyEdit } from "./vocabulary/hooks/useVocabularyEdit";
import { useVocabularyImportExport } from "./vocabulary/hooks/useVocabularyImportExport";
import { useVocabularyQuickAdd } from "./vocabulary/hooks/useVocabularyQuickAdd";
import { useVocabularySelection } from "./vocabulary/hooks/useVocabularySelection";
import {
	type EntryTestResult,
	testPhraseOnServer,
} from "./vocabulary/lib/testServer";
import {
	findDuplicateGroups,
	normalizeWrongPhrase,
	type VocabRow,
} from "./vocabulary/lib/transform";

export default function VocabularyPage() {
	const { call } = usePython();
	const { showSnack } = useSnackbar();

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
			// The list is now empty — any leftover selection (ids of
			// rows that no longer exist) must be cleared too, otherwise
			// the floating bulk bar stays visible showing a stale
			// "N selected" count over an empty list.
			selection.clearSelection();
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
		usageByKey,
	} = useVocabulary({ call, showSnack });

	// Subscribe to locale changes via useT() (a useSyncExternalStore
	// wrapper) so this component re-renders when the locale switches
	// and every t() call re-resolves against the new locale.
	useT();

	// Inline edit row — same VocabInlineForm treatment as Add, rendered
	// in place of the row being edited (no modal; the list stays in
	// view). Save splices the entry in place preserving its _id.
	const {
		isEditing,
		editingEntry,
		trigger,
		replacement,
		openEdit,
		saveEdit,
		handleTriggerChange,
		handleReplacementChange,
		closeEdit,
	} = useVocabularyEdit({
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
	const openEditRef = useRef(openEdit);
	useEffect(() => {
		openEditRef.current = openEdit;
	});
	const handleEdit = useCallback((entry: VocabRow) => {
		openEditRef.current(entry);
	}, []);

	// Per-entry "Test this entry" — runs the entry's wrong phrase
	// through the LIVE server engine (no client mirror): the result is
	// the authoritative answer. One test at a time; clicking another
	// row's button replaces it. ``entryTestIdRef`` guards the async
	// continuation so a slow response for an earlier row can't clobber
	// a newer test.
	const [entryTest, setEntryTest] = useState<{
		id: string;
		result: EntryTestResult;
	} | null>(null);
	const entryTestIdRef = useRef<string | null>(null);

	const handleTestEntry = useCallback(
		async (entry: VocabRow) => {
			entryTestIdRef.current = entry._id;
			setEntryTest({ id: entry._id, result: { status: "running" } });
			try {
				const { output, applied } = await testPhraseOnServer(
					call,
					entry.original,
				);
				if (entryTestIdRef.current !== entry._id) return;
				setEntryTest({
					id: entry._id,
					result: { status: "done", output, applied },
				});
			} catch (err) {
				console.warn("[renderer:Vocabulary] live engine test failed:", err);
				if (entryTestIdRef.current !== entry._id) return;
				setEntryTest({ id: entry._id, result: { status: "error" } });
			}
		},
		[call],
	);

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
				<Spinner label={t("vocabulary.loading")} />
			</div>
		);
	}

	if (loadError && entries.length === 0) {
		return (
			<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col px-16 pt-28 pb-6">
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
			{/* The page column is centered (max-w-4xl mx-auto) in the main
			    content area, so anything sticky/centered inside it (the
			    floating bulk bar) stays centered relative to the CONTENT
			    in both sidebar states — the column recenters when the
			    sidebar expands/collapses. */}
			<div className="relative mx-auto flex min-h-full w-full max-w-4xl flex-col px-16 pt-28 pb-6">
				{/* Heading, then the toolbar on its OWN full-width row BELOW
				    it (not inside PageHeading's children slot).
				    PageHeading wraps children in a content-sized,
				    shrink-0 flex wrapper — inside it, the toolbar's
				    `justify-between` had zero free space to distribute
				    (the wrapper hugs the buttons), so Add Word never
				    reached the far right across three prior attempts.
				    As a direct child of the page column (max-w-4xl
				    w-full) the toolbar spans the full header width and
				    the left group / right Add Word separation is real. */}
				<PageHeading
					title={t("vocabulary.title")}
					description={t("vocabulary.description")}
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

				{/* Pre-existing duplicates review banner. */}
				{showDuplicateBanner && (
					<VocabDuplicateBanner
						count={duplicateCount}
						onRemoveDuplicates={handleRemoveDuplicates}
						onDismiss={() => setDuplicateBannerDismissed(true)}
					/>
				)}

				{entries.length > 0 && (
					// Search/sort only render when there are entries —
					// searching an empty list is meaningless (the empty
					// state's Add CTA is the only action). The entry count
					// lives in the search placeholder, not a separate label.
					<VocabSearchFilterBar
						searchQuery={searchQuery}
						onSearchChange={setSearchQuery}
						sortOrder={sortOrder}
						onSortOrderChange={setSortOrder}
						entryCount={entries.length}
					/>
				)}

				{/* Inline quick-add row. NOT gated on entries.length —
					"Add Word" must work from the empty state too. */}
				{quickAdd.open && (
					<div className="mt-4">
						<VocabInlineForm
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
							<div className="overflow-clip rounded-xl border border-border/10 bg-(--bg-subtle)">
								<VocabListHeader
									visibleIds={filteredSorted.map((e) => e._id)}
									selectedIds={selection.selectedIds}
									onSelectAll={selection.setSelectMany}
								/>
								<div className="divide-y divide-border/10">
									{filteredSorted.slice(0, displayCount).map((entry) =>
										isEditing && editingEntry?._id === entry._id ? (
											// In-place edit row — same inline treatment as
											// Add (no modal). Pencil icon + no bottom
											// border (the list's divide-y owns the
											// separators).
											<VocabInlineForm
												key={entry._id}
												testId="vocab-edit-row"
												submitIcon={PencilEdit02Icon}
												withBottomBorder={false}
												trigger={trigger}
												replacement={replacement}
												onTriggerChange={handleTriggerChange}
												onReplacementChange={handleReplacementChange}
												onSave={saveEdit}
												onCancel={closeEdit}
											/>
										) : (
											<VocabListRow
												key={entry._id}
												entry={entry}
												selected={selection.selectedIds.has(entry._id)}
												onToggleSelect={selection.toggleSelect}
												onEdit={handleEdit}
												onDelete={instantDeleteEntry}
												onTest={handleTestEntry}
												testResult={
													entryTest?.id === entry._id ? entryTest.result : null
												}
												usage={usageByKey.get(
													usageKey(entry.category, entry.original),
												)}
											/>
										),
									)}
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

				{/* Floating bulk bar — appears when rows are selected. It is a
				    DIRECT child of the page column with ``sticky bottom-4``
				    (no absolute wrapper): sticky pins it near the viewport
				    bottom while the column (taller than the viewport when
				    the list is long) stays in view — true floating, it does
				    NOT scroll away. ``mx-auto w-fit`` centers it on the
				    column, which is itself centered (max-w-4xl mx-auto) in
				    the main content area, so the bar stays centered relative
				    to the CONTENT in both sidebar states (the column
				    recenters when the sidebar expands/collapses). */}
				{selection.selectedCount > 0 && (
					<VocabBulkBar
						selectedCount={selection.selectedCount}
						onDeleteSelected={selection.bulkDeleteSelected}
						onExportSelected={(format) =>
							doExport(format, selection.selectedRows)
						}
						onClearSelection={selection.clearSelection}
					/>
				)}
			</div>

			{/* Backdrop click = Cancel (dismiss without data change),
			    matching standard modal behavior. Escape still closes via
			    the same onCancel path. Opt-in per dialog — the
			    ConfirmDialog default keeps the strict AlertDialog
			    contract (explicit acknowledge only). */}
			<ConfirmDialog
				open={showClearConfirm}
				title={t("vocabulary.clearAllTitle")}
				message={t("vocabulary.clearAllMessage")}
				confirmLabel={t("vocabulary.clearAllConfirm")}
				cancelLabel={t("common.cancel")}
				variant="destructive"
				dismissOnBackdrop
				onConfirm={handleClearAllConfirm}
				onCancel={() => setShowClearConfirm(false)}
			/>
		</>
	);
}
