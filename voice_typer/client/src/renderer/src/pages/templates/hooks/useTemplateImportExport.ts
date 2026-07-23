// Templates import / export handlers.
//
// Owns:
//   - ``importInputRef`` (hidden ``<input type="file">`` ref — re-used
//     for every import so we don't pay the cost of remounting it)
//   - ``doExport`` (uses the optional ``window_.exportTemplates`` IPC
//     bridge — NEW-PRIV-007 GDPR right-to-export)
//   - ``handleImportFile`` (parses + de-dupes by trigger|output|match_mode
//     so re-importing the same file doesn't create duplicate rows)
//   - ``handleImportClick`` (delegates to the hidden input's ``.click()``)
//
// Kept in its own hook (rather than in ``useTemplates``) so the
// import-file event handler doesn't re-create when the templates list
// changes (which would re-render the hidden ``<input>`` and reset its
// value mid-flight).

import { useCallback, useRef } from "react";
import { toast } from "sonner";
import { t } from "@/i18n/i18n";
import { saveTemplates } from "../lib/storage";
import { parseImportedTemplates, rowsToTemplates } from "../lib/transform";
import type { Template, TemplateRow } from "../lib/types";

type CallFn = <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>;

interface UseTemplateImportExportArgs {
	call: CallFn;
	loadRows: () => Promise<void>;
	templatesRef: React.RefObject<TemplateRow[]>;
}

interface UseTemplateImportExportResult {
	importInputRef: React.RefObject<HTMLInputElement | null>;
	doExport: () => Promise<void>;
	handleImportFile: (file: File | undefined | null) => Promise<void>;
	handleImportClick: () => void;
}

export function useTemplateImportExport({
	call,
	loadRows,
	templatesRef,
}: UseTemplateImportExportArgs): UseTemplateImportExportResult {
	const importInputRef = useRef<HTMLInputElement | null>(null);

	// ── Import / Export ──────────────────────────────────────────────
	//
	// Export: uses the optional ``window_.exportTemplates`` IPC
	// (NEW-PRIV-007 GDPR right-to-export) when available.  Falls back
	// to a no-op toast if the bridge is missing (e.g. running outside
	// Electron) so the button isn't a silent dead control.
	const doExport = useCallback(async () => {
		try {
			const items = rowsToTemplates(templatesRef.current);
			const bridge = window.window_;
			if (!bridge?.exportTemplates) {
				toast.error(t("vocabulary.exportNotAvailable"));
				return;
			}
			const result = await bridge.exportTemplates({ templates: items });
			if (result.success) {
				const path = result.path ?? "";
				const filename = path.split(/[\\/]/).pop() || "untitled";
				toast.success(t("history.exportSaved", { filename }));
			} else {
				toast.error(result.error || t("history.exportFailed"));
			}
		} catch (err) {
			console.error("Templates export failed:", err);
			toast.error(t("history.exportFailed"));
		}
	}, [templatesRef]);

	// Import: hidden ``<input type="file">`` opens the OS-native picker.
	// We read the file via ``File.text()`` (Chromium ≥ 76, Electron
	// renderer), parse it via ``parseImportedTemplates`` (which accepts
	// both bare-array and ``{templates: [...]}`` shapes), then merge
	// with the existing list (de-duplicating by trigger+output to avoid
	// accidental double-imports) and persist via ``saveTemplates``.
	const handleImportFile = useCallback(
		async (file: File | undefined | null) => {
			if (!file) return;
			try {
				const text = await file.text();
				const imported = parseImportedTemplates(text);
				if (imported.length === 0) {
					toast.error(t("templates.importEmpty"));
					return;
				}
				const existing = rowsToTemplates(templatesRef.current);
				// De-duplicate by ``trigger|output|match_mode`` so re-importing
				// the same file doesn't create duplicate rows.
				const key = (tp: Template) =>
					`${tp.trigger}\u0000${tp.output}\u0000${tp.match_mode}`;
				const existingKeys = new Set(existing.map(key));
				const merged = [...existing];
				let added = 0;
				for (const tp of imported) {
					if (!existingKeys.has(key(tp))) {
						merged.push(tp);
						existingKeys.add(key(tp));
						added++;
					}
				}
				await saveTemplates(merged, call);
				await loadRows();
				if (added === 1) {
					toast.success(t("templates.importSuccessSingular"));
				} else {
					toast.success(
						t("templates.importSuccessPlural", { count: String(added) }),
					);
				}
			} catch (err) {
				console.error("Templates import failed:", err);
				toast.error(
					t("templates.importFailed", {
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
		[call, loadRows, templatesRef],
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
