import { useCallback, useState } from "react";
import type { PythonCall } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
import { formatErrorMessage, type ModelInfo } from "@/lib/utils/models";
import type { LausuConfig } from "@/types/config";

// ── Types ─────────────────────────────────────────────────────────────

interface UseModelSelectionArgs {
	call: PythonCall;
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
	) => void;
	setModels: React.Dispatch<React.SetStateAction<ModelInfo[]>>;
	refreshModelStatus: () => Promise<void>;
	updateConfig: (updates: Partial<LausuConfig>) => Promise<void>;
	/** Optimistic config-state merge after a successful save, the
	 * `config_changed` echo would correct this within milliseconds, but
	 * config-derived UI (e.g. the "No speech model is selected" banner)
	 * must reflect the user's committed action IMMEDIATELY, without a
	 * transport round-trip. Same pattern as setCloudConsent's
	 * optimistic consent flip. */
	setConfig: React.Dispatch<React.SetStateAction<LausuConfig | null>>;
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
	setConfig,
}: UseModelSelectionArgs): UseModelSelectionResult {
	const [selectingModel, setSelectingModel] = useState<string | null>(null);
	const [deleteModelTarget, setDeleteModelTarget] = useState<ModelInfo | null>(
		null,
	);

	// ── Action: selectModel ─────────────────────────────────────────
	// Dep-gated selection replaces the `model.name === "parakeet"` magic
	// string with the `depsInstallable` flag (so future dep-required
	// models can opt into the same UX without touching this code).
	const selectModel = useCallback(
		async (model: ModelInfo) => {
			// Dep-gated models can't be selected until
			// `model.name === "parakeet"` check.
			if (model.depsInstallable && !model.depsOk) {
				showSnack(
					t("models.snack.depsRequiredName", { name: model.name }),
					"warning",
				);
				return;
			}
			// A model that is not downloaded (including Qwen, the backend
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
				const updates: Partial<LausuConfig> = {};
				if (model.backend === "whisper") {
					updates.asr_backend = "whisper";
					updates.model_size = model.name as LausuConfig["model_size"];
				} else {
					updates.asr_backend = model.backend as LausuConfig["asr_backend"];
					updates.model_size = model.name as LausuConfig["model_size"];
				}
				await updateConfig(updates);
				// Optimistic config merge, same pattern as
				// setCloudConsent. The backend publishes the `config_changed`
				// echo for set_config, but the no-model banner (and every
				// other config-derived surface) must flip on the committed
				// user action, not on the transport echo's arrival time.
				setConfig((prev) => (prev ? { ...prev, ...updates } : prev));
				setModels((prev) =>
					prev.map((m) => ({ ...m, isActive: m.name === model.name })),
				);

				// Use the extracted refresh helper
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
		[refreshModelStatus, showSnack, updateConfig, setConfig, setModels],
	);

	// ── Action: requestDeleteModel + confirmDelete ──────────────────
	const requestDeleteModel = useCallback((model: ModelInfo) => {
		// No active-model guard here: deleting the ACTIVE model is
		// allowed (ACTIVE-DELETE), the backend removes the files and
		// reassigns the selection (first other downloaded model, or the
		// "no model selected" state when none exists). The old
		// refuse-and-switch guard dead-ended single-model users.
		setDeleteModelTarget(model);
	}, []);

	const confirmDelete = useCallback(async () => {
		const target = deleteModelTarget;
		if (!target) return;
		try {
			const result = await call<{
				success: boolean;
				error?: string;
				message?: string;
				reason?: string;
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
				// Structured failure reasons localize the snack
				// (C-I18N-1): the backend English `message` is legacy
				// fallback only, then the generic key. Unknown future
				// reasons degrade to the generic snack, never raw English.
				const reasonSnacks: Record<string, string> = {
					unknown_model: t("models.snack.deleteUnknownModel", {
						name: target.name,
					}),
					not_downloaded: t("models.snack.deleteNotDownloaded", {
						name: target.name,
					}),
					dictation_in_flight: t("models.snack.deleteDictationInFlight"),
					unload_failed: t("models.snack.deleteUnloadFailed", {
						name: target.name,
					}),
				};
				showSnack(
					(result.reason && reasonSnacks[result.reason]) ||
						result.message ||
						result.error ||
						t("models.snack.deleteFailed"),
					"error",
				);
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
