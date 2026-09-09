// useCollectionImportExport — the shared import/export round-trip
// skeleton for collection pages (Vocabulary, Templates).
//
// Extracted from the 1:1 mirror pair useVocabularyImportExport /
// useTemplateImportExport: both own the same flow with only the
// domain specifics varying. The SKELETON owns the round trip —
//
//   IMPORT:  hidden file input ref → OS picker click → file.text() →
//            domain parse (throws) → empty check → merge with the
//            current list (de-duplicated by a domain key) → domain
//            persist → singular/plural success toast → input reset;
//            catch → duplicate-aware error toast.
//   EXPORT:  domain items (selected rows or the full list) → domain
//            IPC save → saved/unavailable/rejected outcome mapping →
//            filename toast / not-available toast / rejected toast;
//            catch → console.error + generic failure toast.
//
// — while the DOMAIN (page) injects the variable parts as parameters:
//
//   - parseImported / rowKey / readExisting / persistMerged  (import)
//   - getExportItems / exportFile                             (export)
//   - messages (i18n keys — resolved here via t(), the same
//     key-injection pattern the shared useRowSelection hook
//     established for the collection-page family)
//
// The hidden `<input type="file">` ELEMENT renders in the page's
// toolbar (CollectionToolbar); this hook owns the ref the toolbar
// attaches it to, so re-selecting the same file fires onChange again
// (the input's value is reset in the import round-trip's finally).
//
// Kept in its own hook (rather than in the page data hooks) so the
// import-file event handler doesn't re-create when the list changes
// (which would re-render the hidden `<input>` and reset its value
// mid-flight).
//
// ── Drift decision points (resolve at MIGRATION time, Wave 5) ──────
// The two pages' export/import error handling shipped two drifts; the
// skeleton keeps BOTH forms possible via optional parameters:
//
//   1. Export rejected (IPC returned `success: false`) — Vocabulary
//      is SILENT (no toast); Templates toasts
//      `result.error || t(exportFailed)`. Optional flag
//      `notifyOnExportRejected`: absent = silent (Vocabulary form),
//      present = the rejected outcome toasts with the generic
//      exportFailed key as fallback (Templates form, byte-identical).
//      Wave 5 should likely set the flag for BOTH pages (a failed
//      export must not be silent) — but that unification is a
//      deliberate migration-time behavior decision, not something this
//      skeleton decides.
//   2. Import duplicate rejection — Vocabulary detects the backend's
//      duplicate error (`client.duplicate_entry`) and shows a
//      targeted toast; Templates shows the generic failure. Optional
//      pair `isDuplicateError` + `messages.importDuplicate`: both
//      present = targeted toast (Vocabulary form), absent = generic
//      (Templates form).
//
//   3. doExport's `format` defaults to `"json"` (the Templates
//      signature — the superset). Vocabulary's current signature has
//      no default but every call site passes the format explicitly,
//      so the default changes nothing at migration.

import { useCallback, useRef } from "react";
import { toast } from "sonner";
import { t } from "@/i18n/i18n";
import type { ExportFormat } from "../../../shared/export-format";

/**
 * Raw IPC export result — the common shape both page bridges
 * (`exportVocabulary` / `exportTemplates`) resolve with.
 */
export interface CollectionExportResult {
	success: boolean;
	/** Absolute saved path (present on success). */
	path?: string;
	/** Backend error message (present on failure). */
	error?: string;
}

/**
 * i18n message keys for the import/export toasts. Keys are resolved by
 * THIS hook via `t()`; params (`{count}`, `{error}`, `{filename}`) are
 * interpolated where the skeleton owns the value.
 *
 * Optional parameters are drift decision points (see the file header):
 * `importDuplicate` (with `isDuplicateError`) and
 * `notifyOnExportRejected`.
 */
export interface CollectionImportExportMessages {
	importEmpty: string;
	importSuccessSingular: string;
	/** Resolved with `{ count: String(added) }`. */
	importSuccessPlural: string;
	/** Resolved with `{ error: message }`. */
	importFailed: string;
	/** Targeted duplicate-rejection toast. See `isDuplicateError`. */
	importDuplicate?: string;
	exportNotAvailable: string;
	/** Resolved with `{ filename }`. */
	exportSaved: string;
	exportFailed: string;
}

/**
 * Domain adapters for the import/export round trip.
 *
 * @typeParam Row - the page's row type (list state, carries the row id).
 * @typeParam Item - the domain's import/export item type (persisted
 *   shape — Vocabulary entries, templates).
 */
export interface UseCollectionImportExportArgs<Row, Item> {
	/** Page name for `console.error` prefixes (e.g. "Vocabulary"). */
	debugLabel: string;
	/** Parse the imported file's text. THROWS on malformed input (the
	 *  skeleton routes the throw to the failure toast). Returns an empty
	 *  array when the file is well-formed but contains no items (the
	 *  skeleton routes that to the empty toast). */
	parseImported: (text: string) => Item[];
	/** De-duplication key for an item — re-importing the same file must
	 *  not create duplicate rows. */
	rowKey: (item: Item) => string;
	/** Read the current items (the page's rows ref, mapped to the
	 *  persisted item shape). */
	readExisting: () => Item[];
	/** Persist the merged list (save via IPC + reload the page state —
	 *  the page decides how). THROWS when the save is rejected. */
	persistMerged: (merged: Item[]) => Promise<void>;
	/** Detect the backend's duplicate-rejection error so the import can
	 *  show the targeted `importDuplicate` toast. Optional — see the
	 *  file header's drift note 2. */
	isDuplicateError?: (err: unknown) => boolean;
	/** Toast when the export is REJECTED (`success: false`): the toast
	 *  body is `result.error || t(messages.exportFailed)`. Optional —
	 *  see the file header's drift note 1. */
	notifyOnExportRejected?: boolean;
	/** Export payload items: the given rows (bulk "Export selected") or
	 *  the full list (toolbar export — fetched from the backend or the
	 *  rows ref, the page decides). */
	getExportItems: (rows?: Row[]) => Promise<Item[]> | Item[];
	/** Run the export via the IPC bridge. Returns `null` when the bridge
	 *  is unavailable (running outside Electron) so the skeleton can
	 *  show the not-available toast instead of a silent dead control. */
	exportFile: (
		items: Item[],
		format: ExportFormat,
	) => Promise<CollectionExportResult | null>;
	/** i18n keys — see {@link CollectionImportExportMessages}. */
	messages: CollectionImportExportMessages;
}

export interface UseCollectionImportExportResult<Row> {
	importInputRef: React.RefObject<HTMLInputElement | null>;
	/**
	 * Export rows. When *rows* is given (bulk "Export selected") those
	 * exact rows are exported; otherwise the domain's full list is
	 * exported. `format` defaults to `"json"` (see drift note 3).
	 */
	doExport: (format?: ExportFormat, rows?: Row[]) => Promise<void>;
	handleImportFile: (file: File | undefined | null) => Promise<void>;
	handleImportClick: () => void;
}

export function useCollectionImportExport<Row, Item>({
	debugLabel,
	parseImported,
	rowKey,
	readExisting,
	persistMerged,
	isDuplicateError,
	notifyOnExportRejected,
	getExportItems,
	exportFile,
	messages,
}: UseCollectionImportExportArgs<
	Row,
	Item
>): UseCollectionImportExportResult<Row> {
	const importInputRef = useRef<HTMLInputElement | null>(null);

	// Import: the OS-native picker is opened by the hidden input
	// (rendered in CollectionToolbar). We read the file via
	// `File.text()`, parse it with the domain adapter (which accepts
	// both bare-array and backend envelope shapes), then merge with the
	// existing list (de-duplicating by the domain key to avoid
	// accidental double-imports) and persist via the domain adapter.
	const handleImportFile = useCallback(
		async (file: File | undefined | null) => {
			if (!file) return;
			try {
				const text = await file.text();
				const imported = parseImported(text);
				if (imported.length === 0) {
					toast.error(t(messages.importEmpty));
					return;
				}
				const existing = readExisting();
				const existingKeys = new Set(existing.map(rowKey));
				const merged = [...existing];
				let added = 0;
				for (const item of imported) {
					if (!existingKeys.has(rowKey(item))) {
						merged.push(item);
						existingKeys.add(rowKey(item));
						added++;
					}
				}
				await persistMerged(merged);
				if (added === 1) {
					toast.success(t(messages.importSuccessSingular));
				} else {
					toast.success(
						t(messages.importSuccessPlural, { count: String(added) }),
					);
				}
			} catch (err) {
				console.error(
					`[renderer:useCollectionImportExport] ${debugLabel} import failed:`,
					err,
				);
				// Backend duplicate enforcement: the merged import contains
				// a row the backend already has and the save was rejected.
				// Surface the targeted message instead of the generic
				// parse/save failure (drift note 2 — optional pair).
				if (isDuplicateError && messages.importDuplicate) {
					if (isDuplicateError(err)) {
						toast.error(t(messages.importDuplicate));
						return;
					}
				}
				toast.error(
					t(messages.importFailed, {
						error: err instanceof Error ? err.message : String(err),
					}),
				);
			} finally {
				// Reset the input so re-selecting the same file fires
				// `onChange` again (otherwise the OS picker suppresses the
				// event if the path is unchanged).
				if (importInputRef.current) importInputRef.current.value = "";
			}
		},
		[
			debugLabel,
			parseImported,
			rowKey,
			readExisting,
			persistMerged,
			isDuplicateError,
			messages,
		],
	);

	// Export: the domain adapter fetches the payload items (selected
	// rows or the full list) and runs the IPC save; this skeleton maps
	// the outcome to the toasts — saved → filename toast, bridge
	// missing → not-available toast, rejected → error toast when the
	// page opted in (drift note 1), throw → generic failure toast.
	const doExport = useCallback(
		async (format: ExportFormat = "json", rows?: Row[]) => {
			try {
				const items = await getExportItems(rows);
				const result = await exportFile(items, format);
				if (result === null) {
					toast.error(t(messages.exportNotAvailable));
					return;
				}
				if (result.success) {
					const path = result.path ?? "";
					const filename = path.split(/[\\/]/).pop() || "untitled";
					toast.success(t(messages.exportSaved, { filename }));
					return;
				}
				if (notifyOnExportRejected) {
					toast.error(result.error || t(messages.exportFailed));
				}
			} catch (err) {
				console.error(
					`[renderer:useCollectionImportExport] ${debugLabel} export failed:`,
					err,
				);
				toast.error(t(messages.exportFailed));
			}
		},
		[debugLabel, getExportItems, exportFile, messages, notifyOnExportRejected],
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
