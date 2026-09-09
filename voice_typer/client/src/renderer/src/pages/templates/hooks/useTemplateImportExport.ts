// Templates import / export — domain adapter over the shared
// :func:`useCollectionImportExport` round-trip skeleton.
//
// The skeleton owns the import/export FLOW (hidden-input ref → file.text()
// → domain parse → de-duplicated merge → persist → success/error toasts →
// input reset; export items → IPC bridge → saved/not-available/rejected
// toast mapping). THIS hook supplies only the Templates domain specifics:
//
//   - ``parseImportedTemplates`` (bare-array JSON or the ``{templates:
//     [...]}`` export shape — see lib/transform.ts)
//   - the ``trigger|output|match_mode`` de-duplication key (re-importing
//     the same file must not create duplicate rows)
//   - persistence via ``saveTemplates`` + ``loadRows`` (the page decides
//     how the UI state is rebuilt after the save)
//   - the ``window_.exportTemplates`` IPC bridge invocation
//   - the templates i18n message keys
//   - ``notifyOnExportRejected`` — a rejected export (IPC returned
//     ``success: false``) toasts the failure so the button is never a
//     silent dead control
//
// Kept in its own hook (rather than in ``useTemplates``) so the
// import-file event handler doesn't re-create when the templates list
// changes (which would re-render the hidden ``<input>`` and reset its
// value mid-flight). The hidden ``<input type="file">`` ELEMENT renders
// inside the shared CollectionToolbar shell; this hook owns the ref it
// attaches to, so re-selecting the same file fires onChange again.

import { useCallback } from "react";
import { useCollectionImportExport } from "@/hooks/useCollectionImportExport";
import type { PythonCall } from "@/hooks/usePython";
import type { ExportFormat } from "../../../../../shared/export-format";
import { saveTemplates } from "../lib/storage";
import { parseImportedTemplates, rowsToTemplates } from "../lib/transform";
import type { Template, TemplateRow } from "../lib/types";

interface UseTemplateImportExportArgs {
	call: PythonCall;
	loadRows: () => Promise<void>;
	templatesRef: React.RefObject<TemplateRow[]>;
}

interface UseTemplateImportExportResult {
	importInputRef: React.RefObject<HTMLInputElement | null>;
	doExport: (format?: ExportFormat, rows?: TemplateRow[]) => Promise<void>;
	handleImportFile: (file: File | undefined | null) => Promise<void>;
	handleImportClick: () => void;
}

//bridge.exportTemplates in types/ipc/bridge.ts doesn't yet accept a
// `format` parameter (the types/ipc owner will extend the signature).
// We pass `format` at runtime anyway so the IPC payload reaches the
// backend correctly once the type extension ships; this local alias
// keeps TypeScript happy in the meantime.
type ExportTemplatesWithFormat = (
	data: unknown,
	format: ExportFormat,
) => Promise<{ success: boolean; path?: string; error?: string }>;

export function useTemplateImportExport({
	call,
	loadRows,
	templatesRef,
}: UseTemplateImportExportArgs): UseTemplateImportExportResult {
	// Read the current items in the PERSISTED shape (row ids and the
	// index/expansion view-model fields are client-side concerns —
	// rowsToTemplates maps them back).
	const readExisting = useCallback(
		() => rowsToTemplates(templatesRef.current),
		[templatesRef],
	);

	// Persist the merged list via the shared save path (localStorage
	// mirror + save_templates IPC), then reload so the UI reflects
	// the merged state with fresh row ids.
	const persistMerged = useCallback(
		async (merged: Template[]) => {
			await saveTemplates(merged, call);
			await loadRows();
		},
		[call, loadRows],
	);

	// Bulk "Export selected" passes the exact rows; the toolbar export
	// passes none (→ all templates from the ref).
	const getExportItems = useCallback(
		(rows?: TemplateRow[]) => rowsToTemplates(rows ?? templatesRef.current),
		[templatesRef],
	);

	// The GDPR export IPC bridge. Returns null when the bridge (or its
	// exportTemplates member) is unavailable — e.g. running outside
	// Electron — so the skeleton shows the not-available toast instead
	// of a silent dead control. The cast mirrors the local alias above
	// (the declared type doesn't carry the format arg yet).
	const exportFile = useCallback((items: Template[], format: ExportFormat) => {
		const bridge = window.window_;
		if (!bridge?.exportTemplates) return Promise.resolve(null);
		return (bridge.exportTemplates as ExportTemplatesWithFormat)(
			{ templates: items },
			format,
		);
	}, []);

	return useCollectionImportExport<TemplateRow, Template>({
		debugLabel: "Templates",
		parseImported: parseImportedTemplates,
		rowKey: (tp) => `${tp.trigger}\u0000${tp.output}\u0000${tp.match_mode}`,
		readExisting,
		persistMerged,
		notifyOnExportRejected: true,
		getExportItems,
		exportFile,
		messages: {
			importEmpty: "templates.importEmpty",
			importSuccessSingular: "templates.importSuccessSingular",
			importSuccessPlural: "templates.importSuccessPlural",
			importFailed: "templates.importFailed",
			exportNotAvailable: "templates.exportNotAvailable",
			exportSaved: "templates.exportSaved",
			exportFailed: "templates.exportFailed",
		},
	});
}
