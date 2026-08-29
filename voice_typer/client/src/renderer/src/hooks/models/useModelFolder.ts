/**
 * useModelFolder — disk-space probe + import / open-folder slice.
 *
 *  (Phase 4.5 spaghetti split): extracted from the former
 * `useModelLifecycle.ts` (995-line) monolith. This sub-hook owns:
 *   • `diskInfo` — always `null` today. Historically the result of an
 *     optional `get_disk_info` IPC probe; the probe was removed
 *     because the command was never registered in the Python
 *     `_COMMAND_REGISTRY` nor allowed through the renderer
 *     allowlist (``src/main/allowed-commands.ts``), so the probe always
 *     failed silently and `diskInfo` stayed `null` in practice. The
 *     field is preserved in the return type for backwards-compat with
 *     the ``LocalModelsPanel`` consumer (which still expects the
 *     prop) and so a future backend can re-introduce the probe by
 *     re-adding the interface + allowlist entry without touching
 *     consumers.
 *   • `modelsFolderSupported` — always `false` today. Historically the
 *     result of an optional `models_folder_supported` probe; the probe
 *     was removed (same phantom-command reason as
 *     `diskInfo`). Preserved in the return type for the same
 *     backwards-compat reason — the consumer's conditional render of
 *     the "Open models folder" button simply always evaluates to
 *     `false`.
 *   • `isImporting` — flag for the "Import Model" button's loading
 *     state.
 *
 * And the two actions that drive them:
 *   • `handleImportModel` — opens the Electron folder picker, fires
 *     the `import_model` IPC with the picked path, surfaces success /
 *     warning / error snacks, and re-runs `loadConfig` to reconcile the
 *     local model list with the freshly-imported entries.
 *   • `handleOpenModelsFolder` — NO-OP today. Historically called the
 *     `open_models_folder` IPC, but that command was never
 *     registered so the button was never rendered (it was
 *     gated behind the always-failing `models_folder_supported`
 *     probe). The function is preserved as a no-op for
 *     backwards-compat with the ``LocalModelsPanel`` / ``Models``
 *     page consumer (which still passes it as the "Open models
 *     folder" button's onClick prop); since the button is never
 *     rendered, the no-op is never invoked.
 */
import { useCallback, useState } from "react";
import { t } from "@/i18n/i18n";
import { type DiskInfo, formatErrorMessage } from "@/lib/utils/models";

// ── Types ─────────────────────────────────────────────────────────────

type CallFn = <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>;

interface UseModelFolderArgs {
	call: CallFn;
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
	// mount-time probes were removed — both commands were never
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
			showSnack(t("a11y.importNotAvailableOutsideElectron"), "warning");
			return;
		}
		const result = await api.openModelImportDialog();
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

	// `handleOpenModelsFolder` was previously an async action
	// that called the `open_models_folder` IPC. That command was
	// never registered in `_COMMAND_REGISTRY` nor allowed through
	// `ALLOWED_COMMANDS`, AND the button invoking this action was
	// gated behind the always-failing `models_folder_supported`
	// probe (so the action never executed in practice). The body is
	// replaced with a no-op to preserve the public interface
	// (`LocalModelsPanel` / `Models.tsx` still pass it as the "Open
	// models folder" button's onClick prop — which itself is never
	// rendered because `modelsFolderSupported === false`).
	//
	// If a future backend exposes `open_models_folder`, re-add the
	// matching interface in `types/ipc/requests.ts`, the
	// `ALLOWED_COMMANDS` entry, the Python handler, AND restore the
	// `modelsFolderSupported` probe above before reintroducing a
	// real implementation here.
	const handleOpenModelsFolder = useCallback(async () => {
		/* no-op — see the comment above. */
	}, []);

	return {
		diskInfo,
		modelsFolderSupported,
		isImporting,
		handleImportModel,
		handleOpenModelsFolder,
	};
}
