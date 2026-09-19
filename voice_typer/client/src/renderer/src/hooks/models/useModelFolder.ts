import { useCallback, useState } from "react";
import type { PythonCall } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
import { type DiskInfo, formatErrorMessage } from "@/lib/utils/models";

// ── Types ─────────────────────────────────────────────────────────────

interface UseModelFolderArgs {
	call: PythonCall;
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
	) => void;
	loadConfig: () => Promise<void>;
}

export interface UseModelFolderResult {
	diskInfo: DiskInfo | null;
	modelsFolderSupported: boolean;
	isImporting: boolean;
	handleImportModel: () => Promise<void>;
	handleOpenModelsFolder: () => Promise<void>;
}

// ── Hook ──────────────────────────────────────────────────────────────

export function useModelFolder({
	call,
	showSnack,
	loadConfig,
}: UseModelFolderArgs): UseModelFolderResult {
	const [isImporting, setIsImporting] = useState(false);
	// The optional `get_disk_info` / `models_folder_supported`
	// mount-time probes were removed, both commands were never
	// registered in the Python `_COMMAND_REGISTRY` nor allowed
	// through the renderer allowlist, so the probes always failed
	// silently and the state stayed at its initial values
	// (`null` / `false`). The constants below preserve the public
	// interface for the `LocalModelsPanel` / `Models.tsx` consumers
	// (which still read these fields as props) without lying about
	// the existence of an active probe. If a future backend exposes
	// either command, re-add the matching interface in
	// `types/ipc/requests.ts`, the `ALLOWED_COMMANDS` entry, the
	// Python handler, and only then restore the probe here.
	const diskInfo: DiskInfo | null = null;
	const modelsFolderSupported = false;

	// ── Action: handleImportModel ───────────────────────────────────
	const handleImportModel = useCallback(async () => {
		// Read directly from the globally-augmented ``window.window_``
		// (declared in ``types/ipc/bubble_bridge.ts``) instead of
		// re-declaring the bridge shape inline.
		const api = window.window_;
		if (!api?.openModelImportDialog) {
			showSnack(t("a11y.importNotAvailable"), "warning");
			return;
		}
		let result: { canceled?: boolean; path?: string };
		try {
			result = await api.openModelImportDialog();
		} catch (err) {
			// promise rejection with zero user feedback.
			console.error("[renderer:useModelFolder] import dialog failed:", err);
			showSnack(t("models.import.failedAll"), "error");
			return;
		}
		if (result.canceled || !result.path) return;
		setIsImporting(true);
		try {
			const importResult = await call<{
				success: boolean;
				imported: string[];
				found: string[];
				errors: { model: string; error: string }[];
			}>("import_model", { dir_path: result.path });
			if (importResult.success && importResult.imported.length > 0) {
				await loadConfig();
				showSnack(
					t("models.import.success", {
						count: String(importResult.imported.length),
						models: importResult.imported.join(", "),
					}),
					"success",
				);
			} else if (importResult.found.length === 0) {
				showSnack(t("models.import.noModelsFound"), "warning");
			} else {
				showSnack(t("models.import.failedAll"), "error");
			}
			if (importResult.errors.length > 0) {
				for (const err of importResult.errors) {
					// Prefix with [renderer:useModelFolder] to match the
					// [renderer:<module>] convention.
					console.error(
						"[renderer:useModelFolder] Import error for",
						err.model,
						":",
						err.error,
					);
				}
			}
		} catch (err) {
			showSnack(
				t("models.import.failed", { error: formatErrorMessage(err) }),
				"error",
			);
		} finally {
			setIsImporting(false);
		}
	}, [call, loadConfig, showSnack]);

	// that called the `open_models_folder` IPC. That command was
	// never registered in `_COMMAND_REGISTRY` nor allowed through
	// `ALLOWED_COMMANDS`, AND the button invoking this action was
	// gated behind the always-failing `models_folder_supported`
	// probe (so the action never executed in practice). The body is
	// replaced with a no-op to preserve the public interface
	// (`LocalModelsPanel` / `Models.tsx` still pass it as the "Open
	// models folder" button's onClick prop, which itself is never
	// rendered because `modelsFolderSupported === false`).
	// If a future backend exposes `open_models_folder`, re-add the
	// matching interface in `types/ipc/requests.ts`, the
	// `ALLOWED_COMMANDS` entry, the Python handler, AND restore the
	// `modelsFolderSupported` probe above before reintroducing a
	// real implementation here.
	const handleOpenModelsFolder = useCallback(async () => {
		/* no-op, see the comment above. */
	}, []);

	return {
		diskInfo,
		modelsFolderSupported,
		isImporting,
		handleImportModel,
		handleOpenModelsFolder,
	};
}
