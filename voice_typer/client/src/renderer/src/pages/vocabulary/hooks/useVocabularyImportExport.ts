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
//     ``original|correction|category`` so double-imports don't create
//     duplicate rows)
//   - ``handleImportClick`` (delegates to the hidden input's ``.click()``)
//
// Kept in its own hook (rather than in ``useVocabulary``) so the
// import-file event handler doesn't re-create when the entries list
// changes (which would re-render the hidden ``<input>`` and reset its
// value mid-flight).

import { useCallback, useRef } from "react";
import { toast } from "sonner";
import { t } from "@/i18n/i18n";
import type { VocabularyData, VocabularyEntry } from "@/types/ipc";
import type { ExportFormat } from "../../../../../shared/export-format";

import { parseImportedVocabulary } from "../lib/importExport";
import { flattenEntries, type VocabRow, withEntryIds } from "../lib/transform";

type CallFn = <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>;

interface UseVocabularyImportExportArgs {
	call: CallFn;
	entriesRef: React.RefObject<VocabRow[]>;
	persistVocabulary: (updated: VocabRow[]) => Promise<void>;
	setEntries: (entries: VocabRow[]) => void;
}

interface UseVocabularyImportExportResult {
	importInputRef: React.RefObject<HTMLInputElement | null>;
	doExport: (format: ExportFormat) => Promise<void>;
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
		async (format: ExportFormat) => {
			try {
				const data = await call<VocabularyData>("get_vocabulary");
				// Include ``category`` in the export payload so re-importing
				// (or importing on another machine) preserves the user's
				// category assignments.  Previously the export stripped
				// category, which meant an imported entry lost its
				// category and fell back to auto-detect — silently
				// undoing the user's manual categorisation.
				const flatData = flattenEntries(data ?? {}).map((e) => ({
					original: e.original,
					correction: e.correction,
					category: e.category,
				}));
				const bridge = window.window_;
				if (!bridge) {
					toast.error(t("vocabulary.exportNotAvailable"));
					return;
				}
				const result = await bridge.exportVocabulary(
					{ entries: flatData },
					format,
				);
				if (result.success) {
					const path = result.path ?? "";
					const filename = path.split(/[\\/]/).pop() || "untitled";
					toast.success(t("vocabulary.exportSaved", { filename }));
				}
			} catch (err) {
				console.error("Vocabulary export failed:", err);
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
				const key = (e: VocabularyEntry) =>
					`${e.original}\u0000${e.correction}\u0000${e.category}`;
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
				console.error("Vocabulary import failed:", err);
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
