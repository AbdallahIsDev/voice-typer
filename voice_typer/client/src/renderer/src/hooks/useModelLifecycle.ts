/**
 * useModelLifecycle — facade composing the Models-page sub-hooks.
 *
 *  (Phase 4.5 spaghetti split): the former 995-line monolith has
 * been decomposed into 5 cohesive sub-hooks, each owning one concern:
 *
 *   • `useModelConfig`     — config + models + catalog state, the
 *                            `config_changed` subscription, and the
 *                            load / refresh / update actions.
 *   • `useModelDownload`   — download-progress state machine, the
 *                            `download_progress` subscription, and the
 *                            download / pause / cancel / install-deps
 *                            actions.
 *   • `useModelSelection`  — `selectingModel` / `deleteModelTarget`
 *                            state + the select / request-delete /
 *                            confirm-delete actions.
 *   • `useCloudProviders`  — cloud-provider API keys + test results +
 *                            the save-key / set-consent / test-
 *                            connection actions (also owns the 3
 *                            module-level helpers: `consentKeyFor`,
 *                            `apiKeyConfigField`, `safeApiKey`).
 *   • `useModelFolder`     — disk-info + open-folder-IPC probe state
 *                            + the import / open-folder actions.
 *
 * This facade wires the sub-hooks together, forwarding the shared
 * state (`models` / `setModels` / `apiKeys` / `setConfig` /
 * `updateConfig` / `refreshModelStatus` / `loadConfig`) from
 * `useModelConfig` into the sub-hooks that need it. It also pulls in
 * the cross-cutting `usePython` / `useSnackbar` / `useLastUpdated`
 * hooks so the sub-hooks can stay focused on their own state.
 *
 * The return object shape is **identical** to the pre-split hook —
 * `Models.tsx` and its tests consume the same 39-field surface (the
 * "27-field" count in  was an undercount; the actual return has
 * 39 keys, all preserved here). The 4 internal helpers
 * (`refreshModelStatus`, `updateConfig`, `setConfig`, `setModels`)
 * are destructured out of `useModelConfig`'s return before spreading
 * so they don't leak into the public shape.
 *
 * `ApiTestResult` (the type used by `CloudProvidersPanel`) is re-
 * exported from this module so existing imports
 * (`import type { ApiTestResult } from "@/hooks/useModelLifecycle"`)
 * keep working unchanged.
 */

import { useCallback } from "react";
import { useCloudProviders } from "@/hooks/models/useCloudProviders";
import { useModelConfig } from "@/hooks/models/useModelConfig";
import { useModelDownload } from "@/hooks/models/useModelDownload";
import { useModelFolder } from "@/hooks/models/useModelFolder";
import { useModelSelection } from "@/hooks/models/useModelSelection";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import {
	CLOUD_PROVIDERS,
	type CloudProvider,
	type ModelInfo,
	requiresHuggingFaceConsent,
} from "@/lib/utils/models";

// Re-export the cloud-provider test-result type so existing imports
// (`import type { ApiTestResult } from "@/hooks/useModelLifecycle"`)
// keep working after the split. The canonical definition lives in
// `useCloudProviders` (where it's used); re-exported here for back-
// compat with `CloudProvidersPanel.tsx` and its tests.
export type { ApiTestResult } from "@/hooks/models/useCloudProviders";

// ── Facade ────────────────────────────────────────────────────────────

export function useModelLifecycle() {
	const { call } = usePython();
	const { showSnack } = useSnackbar();
	const { agoLabel, markUpdated } = useLastUpdated();

	// 1. Core config + models + apiKeys state + load / refresh / update
	//    actions + the `config_changed` subscription.
	const configHook = useModelConfig({ call, markUpdated });
	// Destructure the internal helpers out so they don't leak into the
	// public return shape (they're not part of the pre-split return).
	const {
		refreshModelStatus,
		updateConfig,
		setConfig,
		setModels,
		...configRest
	} = configHook;

	// 2. Download-progress state machine + the `download_progress`
	//    subscription + the download / pause / cancel / install-deps
	//    actions. Needs `setModels` (to mark the just-downloaded model
	//    as `downloaded: true`) and `refreshModelStatus` (so `installDeps`
	//    can reconcile the deps-installed state).
	const download = useModelDownload({
		call,
		showSnack,
		setModels,
		refreshModelStatus,
	});

	// 3. Model-selection + deletion state + actions. Needs `setModels`
	//    (optimistic active-model flip + post-delete state clear),
	//    `refreshModelStatus` (post-select reconciliation), and
	//    `updateConfig` (persist the new active model).
	const selection = useModelSelection({
		call,
		showSnack,
		setModels,
		refreshModelStatus,
		updateConfig,
	});

	// 4. Cloud-provider API keys + test results + consent actions.
	//    Needs `apiKeys` (read by saveApiKey + testConnection),
	//    `setConfig` (optimistic consent flip), and `updateConfig`
	//    (persist key + consent).
	const cloud = useCloudProviders({
		showSnack,
		setConfig,
		config: configRest.config,
		apiKeys: configRest.apiKeys,
		updateConfig,
		call,
	});

	// 5. Disk-info probe + import / open-folder actions. Needs
	//    `loadConfig` so a successful import re-syncs the local model
	//    list with the freshly-imported entries.
	const folder = useModelFolder({
		call,
		showSnack,
		loadConfig: configRest.loadConfig,
	});

	// 6. (UI/UX overhaul 2026-08-20, point 4) — just-in-time
	//    HuggingFace-consent gate for downloads. The persistent
	//    consent banner was removed; consent is now checked ONLY at
	//    the moment the user clicks a model's Download button:
	//      • consent already granted (or the model doesn't download
	//        from HuggingFace, e.g. qwen) → proceed immediately;
	//      • consent missing → block the download and show a
	//        TRANSIENT warning toast with a "Grant consent" action
	//        that persists the consent AND proceeds with the download
	//        in one click (no Settings navigation). The backend's own
	//        `_require_huggingface_consent` gate remains the GDPR
	//        enforcement — this only changes when/how the requirement
	//        is surfaced to the user.
	const handleDownloadModel = useCallback(
		(model: ModelInfo) => {
			if (
				!requiresHuggingFaceConsent(model) ||
				configRest.config?.huggingface_consent
			) {
				void download.downloadModel(model);
				return;
			}
			showSnack(t("models.hfConsent.jitMessage"), "warning", {
				action: {
					label: t("models.hfConsent.grant"),
					onClick: () => {
						void (async () => {
							await cloud.setHuggingFaceConsent(true);
							await download.downloadModel(model);
						})();
					},
				},
			});
		},
		[
			configRest.config,
			download.downloadModel,
			cloud.setHuggingFaceConsent,
			showSnack,
		],
	);

	return {
		...configRest,
		...download,
		...selection,
		...cloud,
		...folder,
		agoLabel,
		// Just-in-time consent-gated download entry point (point 4).
		handleDownloadModel,
		// Static data (re-exported for the panels' convenience).
		cloudProviders: CLOUD_PROVIDERS as readonly CloudProvider[],
	};
}
