/**
 * useModelFolder — disk-space probe + import / open-folder slice.
 *
 *  (Phase 4.5 spaghetti split): extracted from the former
 * `useModelLifecycle.ts` (995-line) monolith. This sub-hook owns:
 *   • `diskInfo` — result of the optional `get_disk_info` IPC probe
 *     (). `null` until the probe resolves; stays `null` on
 *     older backends that don't expose the IPC.
 *   • `modelsFolderSupported` — whether the `open_models_folder` IPC
 *     family is registered (probed once on mount).
 *   • `isImporting` — flag for the "Import Model" button's loading
 *     state.
 *
 * And the two actions that drive them:
 *   • `handleImportModel` — opens the Electron folder picker, fires
 *     the `import_model` IPC with the picked path, surfaces success /
 *     warning / error snacks, and re-runs `loadConfig` to reconcile the
 *     local model list with the freshly-imported entries.
 *   • `handleOpenModelsFolder` — calls the optional `open_models_folder`
 *     IPC; silently no-ops (button hidden) on backends that don't
 *     expose it.
 *   • The mount-time disk-info + open-folder-IPC probe effect.
 */
import { useCallback, useEffect, useState } from "react";
import { t } from "@/i18n/i18n";
import { type DiskInfo, formatErrorMessage } from "@/lib/utils/models";

// ── Types ─────────────────────────────────────────────────────────────

type CallFn = <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>;

/**
 * Result returned by the backend's optional `open_models_folder` IPC.
 * Older backends don't expose this command; the renderer probes once
 * and hides the "Open models folder" button when the IPC is missing.
 */
interface OpenFolderResult {
	success: boolean;
	path?: string;
	error?: string;
}

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
	//optional disk-space + open-folder IPCs ─────────────
	const [diskInfo, setDiskInfo] = useState<DiskInfo | null>(null);
	const [modelsFolderSupported, setModelsFolderSupported] = useState(false);

	//probe optional disk-info + open-folder IPCs ────────
	//
	// The backend may or may not expose `get_disk_info` and
	// `open_models_folder`. We probe both once on mount; on failure we
	// silently disable the corresponding UI affordances (disk-space
	// warning, "Open models folder" button). This keeps the page
	// forward-compatible with future backend improvements without
	// breaking on older backends.
	useEffect(() => {
		let cancelled = false;
		(async () => {
			try {
				const info = await call<DiskInfo>("get_disk_info");
				if (!cancelled && info && typeof info.free_bytes === "number") {
					setDiskInfo(info);
				}
			} catch (e) {
				// Backend doesn't support get_disk_info (older server version) —
				// silently skip; disk-space widget stays hidden.
				console.warn("[useModelFolder] get_disk_info probe failed:", e);
			}
			try {
				// Probe whether the open-folder family of IPCs is
				// registered. We don't actually open anything here; the
				// real open call happens in `handleOpenModelsFolder`
				// when the user clicks the button.
				const probe = await call<unknown>("models_folder_supported");
				if (!cancelled) {
					// Any non-erroring response means the IPC is registered.
					setModelsFolderSupported(true);
					void probe; // explicit no-op discard
				}
			} catch (e) {
				// Backend doesn't expose the open-folder family — keep
				// the button hidden.
				console.warn(
					"[useModelFolder] models_folder_supported probe failed:",
					e,
				);
			}
		})();
		return () => {
			cancelled = true;
		};
	}, [call]);

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
				// XZ-R16-09: prefix with [renderer:useModelFolder] to
				// match the [renderer:<module>] convention.
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

	//Action: handleOpenModelsFolder () ────────────────────
	//
	// Calls the backend's optional `open_models_folder` IPC. The button
	// is only rendered when the probe on mount succeeded
	// (`modelsFolderSupported === true`), so this should normally
	// succeed. We still guard against an IPC error so a transient
	// backend issue doesn't crash the page.
	const handleOpenModelsFolder = useCallback(async () => {
		try {
			const result = await call<OpenFolderResult>("open_models_folder");
			if (result?.success) return;
			showSnack(result?.error || t("models.openFolderFailed"), "warning");
		} catch (err) {
			showSnack(
				t("models.import.failed", { error: formatErrorMessage(err) }),
				"error",
			);
		}
	}, [call, showSnack]);

	return {
		diskInfo,
		modelsFolderSupported,
		isImporting,
		handleImportModel,
		handleOpenModelsFolder,
	};
}
