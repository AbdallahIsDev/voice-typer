/**
 * useModelLifecycle — facade composing the Models-page sub-hooks.
 *
 * DT-34 (Phase 4.5 spaghetti split): the former 995-line monolith has
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
 *                            connection actions (also owns the 4
 *                            module-level helpers: `consentKeyFor`,
 *                            `apiKeyConfigField`, `safeApiKey`,
 *                            `testEndpointFor`).
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
 * "27-field" count in DT-34 was an undercount; the actual return has
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

import { useCloudProviders } from "@/hooks/models/useCloudProviders";
import { useModelConfig } from "@/hooks/models/useModelConfig";
import { useModelDownload } from "@/hooks/models/useModelDownload";
import { useModelFolder } from "@/hooks/models/useModelFolder";
import { useModelSelection } from "@/hooks/models/useModelSelection";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { CLOUD_PROVIDERS, type CloudProvider } from "@/lib/utils/models";

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
		apiKeys: configRest.apiKeys,
		updateConfig,
	});

	// 5. Disk-info probe + import / open-folder actions. Needs
	//    `loadConfig` so a successful import re-syncs the local model
	//    list with the freshly-imported entries.
	const folder = useModelFolder({
		call,
		showSnack,
		loadConfig: configRest.loadConfig,
	});

	return {
		...configRest,
		...download,
		...selection,
		...cloud,
		...folder,
		agoLabel,
		// Static data (re-exported for the panels' convenience).
		cloudProviders: CLOUD_PROVIDERS as readonly CloudProvider[],
	};
}
