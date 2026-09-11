// Vocabulary import / export, domain adapter over the shared
// :func:`useCollectionImportExport` round-trip skeleton.
//
// The skeleton owns the import/export FLOW (hidden-input ref → file.text()
// → domain parse → de-duplicated merge → persist → success/error toasts →
// input reset; export items → IPC bridge → saved/not-available/rejected
// toast mapping). THIS hook supplies only the Vocabulary domain specifics:
//
//   - ``parseImportedVocabulary`` (bare-array JSON, backend-shape
//     VocabularyData, or CSV, see lib/importExport.ts)
//   - pair-based de-dup key (``original|correction``, with categories
//     hidden from the UI the same wrong→correct pair is a visual duplicate
//     regardless of its backend bucket, matching the load-time dedupe)
//   - the persisted item shape mapping (``_id`` stripped on read,
//     ``withEntryIds`` attached before persist + setState)
//   - the ``window_.exportVocabulary`` IPC bridge invocation, including
//     the ``category`` field in the export payload so re-importing on
//     another machine preserves the user's category assignments
//     (GDPR right-to-export)
//   - the vocabulary i18n message keys
//   - the backend duplicate-rejection detector (``isDuplicateEntryError``)
//     so a duplicate save surfaces the targeted toast
//
// Kept in its own hook (rather than in ``useVocabulary``) so the
// import-file event handler doesn't re-create when the entries list
// changes (which would re-render the hidden ``<input>`` and reset its
// value mid-flight). The hidden ``<input type="file">`` ELEMENT renders
// inside the shared CollectionToolbar shell; this hook owns the ref it
// attaches to, so re-selecting the same file fires onChange again.
//
// A rejected export (IPC returned ``success: false``) toasts the failure
// (``notifyOnExportRejected``): a failed export must not be silent, the
// user clicked the button and otherwise gets no feedback at all.

import { useCallback } from "react";
import { useCollectionImportExport } from "@/hooks/useCollectionImportExport";
import type { PythonCall } from "@/hooks/usePython";
import type { VocabularyData, VocabularyEntry } from "@/types/ipc";
import type { ExportFormat } from "../../../../../shared/export-format";

import { parseImportedVocabulary } from "../lib/importExport";
import { flattenEntries, type VocabRow, withEntryIds } from "../lib/transform";
import { isDuplicateEntryError } from "./useVocabularyQuickAdd";

interface UseVocabularyImportExportArgs {
	call: PythonCall;
	entriesRef: React.RefObject<VocabRow[]>;
	persistVocabulary: (updated: VocabRow[]) => Promise<void>;
	setEntries: (entries: VocabRow[]) => void;
}

interface UseVocabularyImportExportResult {
	importInputRef: React.RefObject<HTMLInputElement | null>;
	/**
	 * Export entries. When *entries* is given (bulk "Export selected")
	 * those exact rows are exported; otherwise the full list is
	 * fetched from the backend and exported.
	 */
	doExport: (format: ExportFormat, entries?: VocabRow[]) => Promise<void>;
	handleImportFile: (file: File | undefined | null) => Promise<void>;
	handleImportClick: () => void;
}

export function useVocabularyImportExport({
	call,
	entriesRef,
	persistVocabulary,
	setEntries,
}: UseVocabularyImportExportArgs): UseVocabularyImportExportResult {
	// Read the current items in the PERSISTED shape (row ids are a
	// client-side React-key concern, not part of the import/export
	// contract, they are re-attached by withEntryIds below).
	const readExisting = useCallback(
		() =>
			entriesRef.current.map(({ _id: _ignored, ...rest }) => {
				void _ignored;
				return rest;
			}),
		[entriesRef],
	);

	// Persist the merged list with fresh ids, then update the page
	// state so the UI reflects the import in one step. A rejected
	// save throws BEFORE setEntries, so a failed import leaves the
	// visible list untouched.
	const persistMerged = useCallback(
		async (merged: VocabularyEntry[]) => {
			const mergedWithIds: VocabRow[] = withEntryIds(merged);
			await persistVocabulary(mergedWithIds);
			setEntries(mergedWithIds);
		},
		[persistVocabulary, setEntries],
	);

	// Bulk "Export selected" passes the exact rows (mapped to the
	// persisted item shape); the toolbar export passes none, so the
	// full list is fetched from the backend. Either way the payload
	// shape is identical, and ``category`` is included so re-importing
	// (or importing on another machine) preserves the user's category
	// assignments. Previously the export stripped category, which
	// meant an imported entry fell back to auto-detect, silently
	// undoing the user's manual categorisation.
	const getExportItems = useCallback(
		async (rows?: VocabRow[]): Promise<VocabularyEntry[]> => {
			if (rows) {
				return rows.map((e) => ({
					original: e.original,
					correction: e.correction,
					category: e.category,
				}));
			}
			const data = await call<VocabularyData>("get_vocabulary");
			return flattenEntries(data ?? {});
		},
		[call],
	);

	// The GDPR export IPC bridge. Returns null when the bridge is
	// unavailable (running outside Electron) so the skeleton shows the
	// not-available toast instead of a silent dead control.
	const exportFile = useCallback(
		(items: VocabularyEntry[], format: ExportFormat) => {
			const bridge = window.window_;
			if (!bridge) return Promise.resolve(null);
			return bridge.exportVocabulary({ entries: items }, format);
		},
		[],
	);

	return useCollectionImportExport<VocabRow, VocabularyEntry>({
		debugLabel: "Vocabulary",
		parseImported: parseImportedVocabulary,
		// Pair-based dedupe (original + correction), matching the
		// load-time dedupe in useVocabulary.
		rowKey: (e) => `${e.original}\u0000${e.correction}`,
		readExisting,
		persistMerged,
		isDuplicateError: isDuplicateEntryError,
		notifyOnExportRejected: true,
		getExportItems,
		exportFile,
		messages: {
			importEmpty: "vocabulary.importEmpty",
			importSuccessSingular: "vocabulary.importSuccessSingular",
			importSuccessPlural: "vocabulary.importSuccessPlural",
			importFailed: "vocabulary.importFailed",
			importDuplicate: "vocabulary.importDuplicate",
			exportNotAvailable: "vocabulary.exportNotAvailable",
			exportSaved: "vocabulary.exportSaved",
			exportFailed: "vocabulary.exportFailed",
		},
	});
}
