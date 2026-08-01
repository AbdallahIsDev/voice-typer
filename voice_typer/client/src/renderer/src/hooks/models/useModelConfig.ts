/**
 * useModelConfig — config + models + catalog slice of the Models page.
 *
 *  (Phase 4.5 spaghetti split): extracted from the former
 * `useModelLifecycle.ts` (995-line) monolith. This sub-hook owns the
 * page's "core" state — the active config, the local model list, the
 * model catalog, the API-key cache, and the per-mount config cache ref
 * — plus the actions that fetch and persist them:
 *   • `loadConfig` — parallelized `get_config` + `get_model_status` +
 *     `get_model_catalog` ( fix #4).
 *   • `refreshModelStatus` — the extracted `get_model_status` + active-
 *     model reconciliation helper ( fix #8).
 *   • `handleManualRefresh` — user-driven `loadConfig` with `refreshing`
 *     flag.
 *   • `updateConfig` — `set_config` wrapper (: re-throws on error
 *     so callers can branch success vs. failure).
 *   • The `config_changed` event subscription (merges partial payload
 *     into the cached config ref + reapplies active-state — no re-fetch).
 *
 * The `apiKeys` state lives here (not in `useCloudProviders`) because
 * `loadConfig` populates it from the freshly-fetched config; keeping it
 * local avoids a circular dep between this hook and `useCloudProviders`.
 * The state is forwarded to `useCloudProviders` via the facade so the
 * cloud-providers panel can read + write it.
 *
 * The hook returns a small set of "internal" helpers (`refreshModelStatus`,
 * `updateConfig`, `setConfig`, `setModels`) that the facade destructures
 * out before spreading into the final return object — they are NOT part
 * of the public `useModelLifecycle` return shape (preserved verbatim
 * from the pre-split hook).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { safeApiKey } from "@/hooks/models/useCloudProviders";
import { usePythonEvent } from "@/hooks/usePython";
import {
	applyActiveState,
	INITIAL_MODELS,
	type ModelInfo,
	type ModelMetadata,
} from "@/lib/utils/models";
import type { VoiceTyperConfig } from "@/types/config";
import type { ModelStatusMap } from "@/types/ipc";

// ── Types ─────────────────────────────────────────────────────────────

type CallFn = <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>;

interface UseModelConfigArgs {
	call: CallFn;
	markUpdated: () => void;
}

export interface UseModelConfigResult {
	// public (spread into the facade's return)
	config: VoiceTyperConfig | null;
	models: ModelInfo[];
	modelCatalog: Record<string, ModelMetadata>;
	refreshing: boolean;
	apiKeys: Record<string, string>;
	setApiKeys: React.Dispatch<React.SetStateAction<Record<string, string>>>;
	loadConfig: () => Promise<void>;
	handleManualRefresh: () => Promise<void>;
	// internal (facade destructures these out — not part of the public
	// return shape of useModelLifecycle)
	refreshModelStatus: () => Promise<void>;
	updateConfig: (updates: Partial<VoiceTyperConfig>) => Promise<void>;
	setConfig: React.Dispatch<React.SetStateAction<VoiceTyperConfig | null>>;
	setModels: React.Dispatch<React.SetStateAction<ModelInfo[]>>;
}

// ── Hook ──────────────────────────────────────────────────────────────

export function useModelConfig({
	call,
	markUpdated,
}: UseModelConfigArgs): UseModelConfigResult {
	const [refreshing, setRefreshing] = useState(false);
	const [config, setConfig] = useState<VoiceTyperConfig | null>(null);
	const [models, setModels] = useState<ModelInfo[]>([]);
	const [modelCatalog, setModelCatalog] = useState<
		Record<string, ModelMetadata>
	>({});
	const [apiKeys, setApiKeys] = useState<Record<string, string>>({});

	//fix #9: per-mount config cache (replaces module-level
	// `_cachedConfig`). The ref lets the `config_changed` event handler
	// merge incoming partial updates without re-fetching the whole
	// config — and without leaking state across HMR / test mounts.
	const cachedConfigRef = useRef<VoiceTyperConfig | null>(null);

	//fix #8: refresh-model-status helper ─────────────────
	//
	// Previously the `get_model_status` IPC + the "force-active
	// downloaded/depsOk = true" reconciliation block was duplicated
	// verbatim in both `loadConfig` and `selectModel`. Extracted into a
	// single helper so future call sites (and bug fixes) apply uniformly.
	const refreshModelStatus = useCallback(async (): Promise<void> => {
		try {
			const status = await call<ModelStatusMap>("get_model_status");
			if (status && typeof status === "object") {
				setModels((prev) =>
					prev.map((m) => {
						const s = status[m.name];
						if (s) {
							return { ...m, downloaded: s.downloaded, depsOk: s.deps_ok };
						}
						return m;
					}),
				);
			}
			// Reconcile: the active model is always considered downloaded
			// + depsOk (the backend wouldn't have selected it otherwise).
			setModels((prev) =>
				prev.map((m) =>
					m.isActive ? { ...m, downloaded: true, depsOk: true } : m,
				),
			);
		} catch (err) {
			console.error("Failed to refresh model status:", err);
		}
	}, [call]);

	//fix #4: parallelized loadConfig ─────────────────────
	//
	// Previously this function awaited `get_config`, then awaited
	// `get_model_status`, then awaited `get_model_catalog` — strictly
	// sequential. A slow `get_model_catalog` delayed the page render
	// even though the model cards don't need catalog metadata to render
	// their skeleton.
	//
	// Now we fire all three in parallel via `Promise.allSettled`. The
	// `get_config` result is the gating one — `applyActiveState` runs
	// as soon as it resolves. The other two settle in the background.
	const loadConfig = useCallback(async (): Promise<void> => {
		try {
			const results = await Promise.allSettled([
				call<VoiceTyperConfig>("get_config"),
				call<ModelStatusMap>("get_model_status"),
				call<{ models: ModelMetadata[] }>("get_model_catalog"),
			]);

			const cfgResult = results[0];
			const statusResult = results[1];
			const catalogResult = results[2];

			if (cfgResult.status === "fulfilled") {
				const cfg = cfgResult.value;
				cachedConfigRef.current = cfg;
				setConfig(cfg);

				// Apply active-state mapping immediately so the cards
				// render with the right Active badge on first paint.
				// First load: `models` is still [] — seed from the
				// static INITIAL_MODELS catalog. Subsequent loads:
				// `models` already has entries; just refresh isActive.
				setModels((prev) =>
					applyActiveState(prev.length === 0 ? INITIAL_MODELS : prev, cfg),
				);

				setApiKeys({
					openai: safeApiKey(cfg?.openai_api_key),
					groq: safeApiKey(cfg?.groq_api_key),
					deepgram: safeApiKey(cfg?.deepgram_api_key),
				});
			} else {
				console.error("Failed to load config:", cfgResult.reason);
			}

			if (statusResult.status === "fulfilled") {
				const status = statusResult.value;
				if (status && typeof status === "object") {
					setModels((prev) =>
						prev.map((m) => {
							const s = status[m.name];
							if (s) {
								return { ...m, downloaded: s.downloaded, depsOk: s.deps_ok };
							}
							return m;
						}),
					);
					// Reconcile active model as downloaded+depsOk.
					setModels((prev) =>
						prev.map((m) =>
							m.isActive ? { ...m, downloaded: true, depsOk: true } : m,
						),
					);
				}
			} else {
				console.error("Failed to get model status:", statusResult.reason);
			}

			if (catalogResult.status === "fulfilled") {
				const catalog = catalogResult.value;
				if (catalog?.models && Array.isArray(catalog.models)) {
					const byName: Record<string, ModelMetadata> = {};
					for (const m of catalog.models) {
						byName[m.name] = m;
					}
					setModelCatalog(byName);
				}
			} else {
				console.error("Failed to get model catalog:", catalogResult.reason);
			}
		} finally {
			markUpdated();
		}
	}, [call, markUpdated]);

	// Fire loadConfig on mount.
	useEffect(() => {
		loadConfig();
	}, [loadConfig]);

	// ── config_changed event subscription ───────────────────────────
	//
	// The backend pushes `config_changed` whenever `set_config` runs.
	// We merge the partial payload into the cached config + reapply
	// active-state. No `get_config` re-fetch needed.
	usePythonEvent(
		"config_changed",
		useCallback(
			(data: Record<string, unknown> | undefined): (() => void) | undefined => {
				if (!data) return undefined;
				const prev = cachedConfigRef.current;
				if (!prev) return undefined;
				const merged = { ...prev, ...data } as VoiceTyperConfig;
				cachedConfigRef.current = merged;
				setConfig(merged);
				setModels((curr) => applyActiveState(curr, merged));
				return undefined;
			},
			[],
		),
	);

	const handleManualRefresh = useCallback(async () => {
		setRefreshing(true);
		try {
			await loadConfig();
		} finally {
			setRefreshing(false);
		}
	}, [loadConfig]);

	//previously ``updateConfig`` swallowed ``set_config`` errors
	// (try/catch with only ``console.error``), so callers like
	// ``selectModel`` / ``saveApiKey`` / ``setCloudConsent`` /
	// ``setHuggingFaceConsent`` always showed their SUCCESS toast
	// even when the backend save failed. The wrapper now re-throws on
	// error so each caller can branch on the result.
	const updateConfig = useCallback(
		async (updates: Partial<VoiceTyperConfig>): Promise<void> => {
			await call("set_config", updates);
		},
		[call],
	);

	return {
		config,
		models,
		modelCatalog,
		refreshing,
		apiKeys,
		setApiKeys,
		loadConfig,
		handleManualRefresh,
		// internal — facade destructures these out
		refreshModelStatus,
		updateConfig,
		setConfig,
		setModels,
	};
}
