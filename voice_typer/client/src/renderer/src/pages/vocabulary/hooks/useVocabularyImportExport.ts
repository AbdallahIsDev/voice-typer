// Vocabulary import / export handlers.
//
// Owns:
//   - ``importInputRef`` (hidden ``<input type="file">`` ref — re-used
//     for every import so we don't pay the cost of remounting it)
//   - ``doExport`` (uses the optional ``window_.exportVocabulary`` IPC
//bridge —  GDPR right-to-export).  Includes the
//     ``category`` field in the export payload so re-importing on
//     another machine preserves the user's category assignments.
//   - ``handleImportFile`` (parses + de-dupes by
//     ``original|correction`` so double-imports don't create
//     duplicate rows)
//   - ``handleImportClick`` (delegates to the hidden input's ``.click()``)
//
// Kept in its own hook (rather than in ``useVocabulary``) so the
// import-file event handler doesn't re-create when the entries list
// changes (which would re-render the hidden ``<input>`` and reset its
// value mid-flight).

import { useCallback, useRef } from "react";
import { toast } from "sonner";
import type { PythonCall } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
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
	const importInputRef = useRef<HTMLInputElement | null>(null);

	const doExport = useCallback(
		async (format: ExportFormat, entries?: VocabRow[]) => {
			try {
				// Bulk "Export selected" passes the exact rows; the toolbar
				// export fetches the full list from the backend. Either way
				// the payload shape is identical.
				let source: Array<{
					original: string;
					correction: string;
					category: string | undefined;
				}>;
				if (entries) {
					source = entries.map((e) => ({
						original: e.original,
						correction: e.correction,
						category: e.category,
					}));
				} else {
					const data = await call<VocabularyData>("get_vocabulary");
					// Include ``category`` in the export payload so re-importing
					// (or importing on another machine) preserves the user's
					// category assignments.  Previously the export stripped
					// category, which meant an imported entry lost its
					// category and fell back to auto-detect — silently
					// undoing the user's manual categorisation.
					source = flattenEntries(data ?? {}).map((e) => ({
						original: e.original,
						correction: e.correction,
						category: e.category,
					}));
				}
				const bridge = window.window_;
				if (!bridge) {
					toast.error(t("vocabulary.exportNotAvailable"));
					return;
				}
				const result = await bridge.exportVocabulary(
					{ entries: source },
					format,
				);
				if (result.success) {
					const path = result.path ?? "";
					const filename = path.split(/[\\/]/).pop() || "untitled";
					toast.success(t("vocabulary.exportSaved", { filename }));
				}
			} catch (err) {
				console.error(
					"[renderer:useVocabularyImportExport] Vocabulary export failed:",
					err,
				);
				toast.error(t("vocabulary.exportFailed"));
			}
		},
		[call],
	);

	// Import: hidden ``<input type="file">`` opens the OS-native picker.
	// Mirrors the Templates import pattern.  We parse the file via
	// ``parseImportedVocabulary`` (which accepts both bare-array and
	// backend-shape VocabularyData), de-duplicate by
	// ``original|correction|category`` to avoid double-imports, persist
	// via ``persistVocabulary``, then reload to pick up the merged list.
	const handleImportFile = useCallback(
		async (file: File | undefined | null) => {
			if (!file) return;
			try {
				const text = await file.text();
				const imported = parseImportedVocabulary(text);
				if (imported.length === 0) {
					toast.error(t("vocabulary.importEmpty"));
					return;
				}
				const existing = entriesRef.current.map(
					({ _id: _ignored, ...rest }) => {
						void _ignored;
						return rest;
					},
				);
				// Pair-based dedupe (original + correction), matching the
				// load-time dedupe in useVocabulary — with categories
				// hidden from the UI, the same wrong→correct pair is a
				// visual duplicate regardless of its backend bucket.
				const key = (e: VocabularyEntry) =>
					`${e.original}\u0000${e.correction}`;
				const existingKeys = new Set(existing.map(key));
				const merged = [...existing];
				let added = 0;
				for (const e of imported) {
					if (!existingKeys.has(key(e))) {
						merged.push(e);
						existingKeys.add(key(e));
						added++;
					}
				}
				// Attach UUIDs to the merged list before persisting + setState.
				const mergedWithIds: VocabRow[] = withEntryIds(merged);
				await persistVocabulary(mergedWithIds);
				setEntries(mergedWithIds);
				if (added === 1) {
					toast.success(t("vocabulary.importSuccessSingular"));
				} else {
					toast.success(
						t("vocabulary.importSuccessPlural", { count: String(added) }),
					);
				}
			} catch (err) {
				console.error(
					"[renderer:useVocabularyImportExport] Vocabulary import failed:",
					err,
				);
				// Backend duplicate enforcement: the merged import contains a
				// wrong phrase that already exists (case-insensitive) and the
				// save was rejected. Surface the targeted message instead of
				// the generic parse/save failure.
				if (isDuplicateEntryError(err)) {
					toast.error(t("vocabulary.importDuplicate"));
					return;
				}
				toast.error(
					t("vocabulary.importFailed", {
						error: err instanceof Error ? err.message : String(err),
					}),
				);
			} finally {
				// Reset the input so re-selecting the same file fires
				// ``onChange`` again (otherwise the OS picker suppresses
				// the event if the path is unchanged).
				if (importInputRef.current) importInputRef.current.value = "";
			}
		},
		[entriesRef, persistVocabulary, setEntries],
	);

	const handleImportClick = useCallback(() => {
		importInputRef.current?.click();
	}, []);

	return {
		importInputRef,
		doExport,
		handleImportFile,
		handleImportClick,
	};
}
