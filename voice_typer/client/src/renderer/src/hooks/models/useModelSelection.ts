/**
 * useModelSelection — model-selection + deletion slice of the Models page.
 *
 *  (Phase 4.5 spaghetti split): extracted from the former
 * `useModelLifecycle.ts` (995-line) monolith. This sub-hook owns:
 *   • `selectingModel` — the name of the model currently being
 *     selected (drives the spinner on the model card's Select button).
 *   • `deleteModelTarget` + `setDeleteModelTarget` — the model pending
 *     deletion confirmation (drives the ConfirmDialog open state).
 *
 * And the three actions that drive them:
 *   • `selectModel` — dep-gated guard ( fix #7: replaces the
 *     `model.name === "parakeet"` magic string with the
 *     `depsInstallable` flag), persists the new active model via
 *     `updateConfig`, optimistically updates the local model list,
 *     then calls `refreshModelStatus` to reconcile ( fix #8:
 *     uses the extracted helper instead of duplicating the
 *     `get_model_status` block from `loadConfig`). : surfaces
 *     config-save failures instead of silently showing the success
 *     toast.
 *   • `requestDeleteModel` — refuses to delete the active model while
 *     it's on disk (deleting would break the running backend), but
 *     ALLOWS deleting an active model that is missing from disk — a
 *     stale selection the user can't otherwise get rid of (the
 *     backend clears the config and switches to another model).
 *     Otherwise stashes the target for the ConfirmDialog.
 *   • `confirmDelete` — fires the `delete_model` IPC, updates local
 *     state, and surfaces success / failure via snackbar.
 */
import { useCallback, useState } from "react";
import { t } from "@/i18n/i18n";
import { formatErrorMessage, type ModelInfo } from "@/lib/utils/models";
import type { VoiceTyperConfig } from "@/types/config";

// ── Types ─────────────────────────────────────────────────────────────

type CallFn = <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>;

interface UseModelSelectionArgs {
	call: CallFn;
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
	) => void;
	setModels: React.Dispatch<React.SetStateAction<ModelInfo[]>>;
	refreshModelStatus: () => Promise<void>;
	updateConfig: (updates: Partial<VoiceTyperConfig>) => Promise<void>;
}

export interface UseModelSelectionResult {
	selectingModel: string | null;
	deleteModelTarget: ModelInfo | null;
	setDeleteModelTarget: React.Dispatch<React.SetStateAction<ModelInfo | null>>;
	selectModel: (model: ModelInfo) => Promise<void>;
	requestDeleteModel: (model: ModelInfo) => void;
	confirmDelete: () => Promise<void>;
}

// ── Hook ──────────────────────────────────────────────────────────────

export function useModelSelection({
	call,
	showSnack,
	setModels,
	refreshModelStatus,
	updateConfig,
}: UseModelSelectionArgs): UseModelSelectionResult {
	const [selectingModel, setSelectingModel] = useState<string | null>(null);
	const [deleteModelTarget, setDeleteModelTarget] = useState<ModelInfo | null>(
		null,
	);

	// ── Action: selectModel ─────────────────────────────────────────
	//
	//fix #7: replaces the `model.name === "parakeet"` magic
	// string with the `depsInstallable` flag (so future dep-required
	// models can opt into the same UX without touching this code).
	const selectModel = useCallback(
		async (model: ModelInfo) => {
			//fix #7: dep-gated models can't be selected until
			// their deps are installed. Previously this was a hardcoded
			// `model.name === "parakeet"` check.
			if (model.depsInstallable && !model.depsOk) {
				showSnack(
					t("models.snack.depsRequiredName", { name: model.name }),
					"warning",
				);
				return;
			}
			// A model that is not downloaded (including Qwen — the backend
			// registry declares it local-only, NOT auto-fetched) cannot be
			// selected: the backend would refuse to load it ("model is not
			// downloaded yet" tray/Windows notification) while the in-app
			// UI claimed success. Block up front with a warning instead.
			if (!model.downloaded) {
				showSnack(
					t("models.snack.notDownloaded", { name: model.name }),
					"warning",
				);
				return;
			}
			setSelectingModel(model.name);
			try {
				const updates: Partial<VoiceTyperConfig> = {};
				if (model.backend === "whisper") {
					updates.asr_backend = "whisper";
					updates.model_size = model.name as VoiceTyperConfig["model_size"];
				} else {
					updates.asr_backend =
						model.backend as VoiceTyperConfig["asr_backend"];
					updates.model_size = model.name as VoiceTyperConfig["model_size"];
				}
				await updateConfig(updates);
				setModels((prev) =>
					prev.map((m) => ({ ...m, isActive: m.name === model.name })),
				);

				//fix #8: use the extracted refresh helper
				// (previously a verbatim duplicate of loadConfig's block).
				await refreshModelStatus();

				showSnack(
					t("models.snack.usingModel", { name: model.name }),
					"success",
				);
			} catch (err) {
				//surface config-save failures instead of
				// silently showing the success toast. The model
				// state is NOT updated (we never reached the
				// ``setModels`` call), so the UI stays in sync
				// with the persisted backend config.
				showSnack(
					t("models.snack.selectFailed", {
						name: model.name,
						error: formatErrorMessage(err),
					}),
					"error",
				);
			} finally {
				setSelectingModel(null);
			}
		},
		[refreshModelStatus, showSnack, updateConfig, setModels],
	);

	// ── Action: requestDeleteModel + confirmDelete ──────────────────
	const requestDeleteModel = useCallback(
		(model: ModelInfo) => {
			// STALE-ACTIVE fix: the "cannot delete active model" guard
			// only applies while the active model is ACTUALLY on disk
			// (deleting it would break the running ASR backend). An
			// active model with `downloaded: false` was removed from
			// disk out-of-band — the config points at a phantom. Deleting
			// it is the ONLY way to clear the stale selection (the
			// backend switches to another model), so allow it through to
			// the confirm dialog.
			if (model.isActive && model.downloaded) {
				showSnack(t("models.cannotDeleteActive"), "warning");
				return;
			}
			setDeleteModelTarget(model);
		},
		[showSnack],
	);

	const confirmDelete = useCallback(async () => {
		const target = deleteModelTarget;
		if (!target) return;
		try {
			const result = await call<{
				success: boolean;
				error?: string;
				message?: string;
			}>("delete_model", { model: target.name });
			if (result.success) {
				setModels((prev) =>
					prev.map((m) =>
						m.name === target.name
							? { ...m, downloaded: false, isActive: false }
							: m,
					),
				);
				showSnack(
					result.message || t("models.snack.deleted", { name: target.name }),
					"success",
				);
			} else {
				showSnack(result.error || t("models.snack.deleteFailed"), "error");
			}
		} catch (err) {
			showSnack(
				t("models.snack.deleteFailedError", {
					error: formatErrorMessage(err),
				}),
				"error",
			);
		} finally {
			setDeleteModelTarget(null);
		}
	}, [call, deleteModelTarget, showSnack, setModels]);

	return {
		selectingModel,
		deleteModelTarget,
		setDeleteModelTarget,
		selectModel,
		requestDeleteModel,
		confirmDelete,
	};
}
