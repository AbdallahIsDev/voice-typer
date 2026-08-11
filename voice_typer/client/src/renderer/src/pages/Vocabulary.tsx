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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import PageHeading from "@/components/common/PageHeading";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Spinner } from "@/components/feedback/Spinner";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { getLocaleSnapshot, t, useT } from "@/i18n/i18n";

import { VocabDialog } from "./vocabulary/components/VocabDialog";
import { VocabListRow } from "./vocabulary/components/VocabListRow";
import { VocabSearchFilterBar } from "./vocabulary/components/VocabSearchFilterBar";
import { VocabToolbar } from "./vocabulary/components/VocabToolbar";
import { useVocabulary } from "./vocabulary/hooks/useVocabulary";
import { useVocabularyDialog } from "./vocabulary/hooks/useVocabularyDialog";
import { useVocabularyImportExport } from "./vocabulary/hooks/useVocabularyImportExport";
import { getCategoryLabels } from "./vocabulary/lib/categories";
import type { VocabRow } from "./vocabulary/lib/transform";

export default function VocabularyPage() {
	const { call } = usePython();
	const { showSnack } = useSnackbar();
	const { agoLabel, markUpdated } = useLastUpdated();

	//: soft display cap — the list renders at most this many rows
	// until the user clicks "Show more". Keeps very large vocabularies
	// (thousands of entries) from mounting thousands of DOM rows.
	const DISPLAY_CAP = 200;
	const [displayCount, setDisplayCount] = useState(DISPLAY_CAP);

	//: "Clear All" is gated by a confirmation dialog — granting it
	// wipes every category (an irreversible privacy-adjacent action).
	const [showClearConfirm, setShowClearConfirm] = useState(false);

	const handleClearAllConfirm = async () => {
		setShowClearConfirm(false);
		try {
			await persistVocabulary([]);
			setEntries([]);
			showSnack(t("vocabulary.clearAllToast"), "success");
		} catch (err) {
			console.error("Failed to clear vocabulary:", err);
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
		categoryFilter,
		setCategoryFilter,
		filteredSorted,
	} = useVocabulary({ call, showSnack });

	//: mark the data as fresh once the page has finished a load (or an
	// empty successful load) — the indicator shows "Just now" after load.
	// Driven by the loading-true→false transition so markUpdated fires
	// exactly once per load (never during render → no re-render loop).
	const prevLoadingRef = useRef(true);
	useEffect(() => {
		if (!loading && !loadError && prevLoadingRef.current) {
			markUpdated();
		}
		prevLoadingRef.current = loading;
	}, [loading, loadError, markUpdated]);

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

	// Subscribe to locale changes via useT() (a useSyncExternalStore
	// wrapper) so this component re-renders when the locale switches.
	// getLocaleSnapshot() returns a locale#revision string that changes
	// on every locale change (including the post-dynamic-import
	// notification) — using it as the sole useMemo dependency means
	// getCategoryLabels() is re-resolved ONLY on locale switch, not on
	// every keystroke. Previously getCategoryLabels() was called on every
	// render, creating a fresh object that broke VocabListRow's memo.
	// (The snapshot value is not read inside the factory — getCategoryLabels()
	// resolves t() keys against the current locale at call time — so the
	// dependency is an intentional invalidation key, same pattern as
	// AudioFilterChain.tsx.)
	useT();
	const localeRevision = getLocaleSnapshot();
	// biome-ignore lint/correctness/useExhaustiveDependencies: localeRevision is the intentional invalidation key — getCategoryLabels() reads the current locale via t() at call time
	const categoryLabels = useMemo(() => getCategoryLabels(), [localeRevision]);

	// openEditDialog from useVocabularyDialog is a plain function
	// (recreated every render). Wrap it in a stable useCallback via the
	// "latest ref" pattern so the memo'd VocabListRow sees a
	// referentially-stable onEdit prop and skips re-rendering on
	// unrelated state changes (e.g. search keystrokes). Mirrors the
	// ActivityListRow stable-callback pattern
	// (components/dashboard/ActivityList.tsx:74).
	const openEditDialogRef = useRef(openEditDialog);
	useEffect(() => {
		openEditDialogRef.current = openEditDialog;
	});
	const handleEdit = useCallback((entry: VocabRow) => {
		openEditDialogRef.current(entry);
	}, []);

	if (loading) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner />
			</div>
		);
	}

	//fix #8: distinguish "backend failed to load" from
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
						onAdd={openAddDialog}
						exportDisabled={entries.length === 0}
						addDisabled={saving}
						onClearAll={() => setShowClearConfirm(true)}
						clearAllDisabled={entries.length === 0}
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
							description={t("vocabulary.noResultsDescription")}
						/>
					) : (
						<>
							<div className="overflow-hidden rounded-xl border border-border bg-(--bg-subtle) divide-y divide-border">
								{filteredSorted.slice(0, displayCount).map((entry) => (
									<VocabListRow
										key={entry._id}
										entry={entry}
										categoryLabels={categoryLabels}
										onEdit={handleEdit}
										onDelete={instantDeleteEntry}
									/>
								))}
							</div>
							{filteredSorted.length > displayCount && (
								<button
									type="button"
									onClick={() => setDisplayCount((c) => c + DISPLAY_CAP)}
									className="mx-auto mt-3 flex items-center gap-1.5 rounded-full border border-border bg-(--bg-subtle) px-4 py-1.5 text-xs font-medium text-accent transition-colors hover:border-accent/40 hover:bg-accent/5 cursor-pointer"
								>
									{t("vocabulary.showMore")}
								</button>
							)}
						</>
					)}
				</div>

				{/* Count footer */}
				{entries.length > 0 && (
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
