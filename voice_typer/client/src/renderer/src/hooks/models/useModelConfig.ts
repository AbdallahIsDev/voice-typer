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
import { peekIpcCache, writeIpcCache } from "@/lib/ipcCache";
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

// Module-cache key for the SWR seed (see lib/ipcCache.ts).
const MODELS_CONFIG_CACHE_KEY = "models.config";

interface UseModelConfigArgs {
	call: CallFn;
	markUpdated: () => void;
}

export interface UseModelConfigResult {
	// public (spread into the facade's return)
	config: VoiceTyperConfig | null;
	/** Set when the gating `get_config` fetch fails. The page renders
	 *  a load-failure EmptyState with a Retry action instead of an
	 *  endless spinner (config stays null on failure). Cleared on the
	 *  next successful load. */
	loadError: string | null;
	models: ModelInfo[];
	modelCatalog: Record<string, ModelMetadata>;
	apiKeys: Record<string, string>;
	setApiKeys: React.Dispatch<React.SetStateAction<Record<string, string>>>;
	loadConfig: () => Promise<void>;
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
	// Ref mirrors of `call` / `markUpdated` so `loadConfig` keeps a
	// STABLE identity ([] deps). Both are useCallback-stable in
	// production, but test mocks return FRESH functions per render — an
	// identity churn would re-fire the mount-load effect (loadConfig →
	// setModels/setConfig → re-render → new call → loop → worker OOM).
	// Same pattern as useVocabulary.ts.
	const callRef = useRef(call);
	useEffect(() => {
		callRef.current = call;
	}, [call]);
	const markUpdatedRef = useRef(markUpdated);
	useEffect(() => {
		markUpdatedRef.current = markUpdated;
	}, [markUpdated]);

	// SWR seed: revisit renders the last visit's config instantly from
	// the module cache (survives page unmount) so the page skips its
	// loading branch entirely — `loadConfig` below still revalidates.
	// Read ONCE at init (lazy useState initializers), not per render.
	const [config, setConfig] = useState<VoiceTyperConfig | null>(
		() => peekIpcCache<VoiceTyperConfig>(MODELS_CONFIG_CACHE_KEY) ?? null,
	);
	// Failure surface for the gating `get_config` fetch. Without this,
	// a rejected `get_config` left `config` null forever and the page
	// spun on its loading branch with no recovery path.
	const [loadError, setLoadError] = useState<string | null>(null);
	const [models, setModels] = useState<ModelInfo[]>(() => {
		const seeded = peekIpcCache<VoiceTyperConfig>(MODELS_CONFIG_CACHE_KEY);
		return seeded ? applyActiveState(INITIAL_MODELS, seeded) : [];
	});

	// Request-generation guard for `loadConfig`: overlapping loads are
	// possible (Retry double-click, import-triggered reload while a
	// load is in flight). IPC dispatches run concurrently, so an
	// EARLIER `get_config` can resolve AFTER a newer one and clobber
	// fresher state. Each run claims a generation; only the newest may
	// apply its results.
	const loadGenerationRef = useRef(0);

	// SWR write-through: keep the module cache in sync with EVERY
	// committed config state change (loadConfig results, `config_changed`
	// merges, consent flips via setConfig) — not just the loadConfig
	// path — so the next page visit seeds from current data instead of
	// flickering back to a stale value until revalidation lands.
	useEffect(() => {
		if (config) writeIpcCache(MODELS_CONFIG_CACHE_KEY, config);
	}, [config]);
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
	//
	//  STALE-ACTIVE fix: the "active model is always considered
	// downloaded + depsOk" override was REMOVED. It was a false
	// assumption: the configured model can be removed from disk
	// out-of-band (deleted folder, moved cache), leaving the config
	// pointing at a missing model. The backend's `get_model_status`
	// (which stats the actual filesystem) is authoritative — the card
	// for an active-but-missing model must show `downloaded: false` so
	// the UI offers a restore/clear affordance instead of a dead-end
	// disabled "Active" tick.
	const refreshModelStatus = useCallback(async (): Promise<void> => {
		try {
			const status = await callRef.current<ModelStatusMap>("get_model_status");
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
		} catch (err) {
			// Prefix with [renderer:useModelConfig] to match the
			// [renderer:<module>] convention adopted by other hooks
			// (usePython, useConnection, etc).
			console.error(
				"[renderer:useModelConfig] Failed to refresh model status:",
				err,
			);
		}
	}, []);

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
		// Claim the load generation — an earlier in-flight load whose
		// responses resolve after this one started must not clobber the
		// fresher state this run produces.
		const generation = ++loadGenerationRef.current;
		const isCurrent = () => loadGenerationRef.current === generation;
		try {
			const results = await Promise.allSettled([
				callRef.current<VoiceTyperConfig>("get_config"),
				callRef.current<ModelStatusMap>("get_model_status"),
				callRef.current<{ models: ModelMetadata[] }>("get_model_catalog"),
			]);

			const cfgResult = results[0];
			const statusResult = results[1];
			const catalogResult = results[2];

			if (!isCurrent()) {
				// A newer loadConfig superseded this run — its results own
				// the state now; applying this run's (older) responses
				// would regress config/models/apiKeys.
				return;
			}
			if (cfgResult.status === "fulfilled") {
				setLoadError(null);
				const cfg = cfgResult.value;
				cachedConfigRef.current = cfg;
				setConfig(cfg);
				// SWR write-through — the next visit seeds from this snapshot.
				writeIpcCache(MODELS_CONFIG_CACHE_KEY, cfg);

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
				// Prefix with [renderer:useModelConfig] to match the
				// [renderer:<module>] convention.
				console.error(
					"[renderer:useModelConfig] Failed to load config:",
					cfgResult.reason,
				);
				// Surface the failure so the page can leave the loading
				// branch and render the load-failure EmptyState (Retry)
				// instead of spinning forever on a null config.
				setLoadError(
					cfgResult.reason instanceof Error
						? cfgResult.reason.message
						: String(cfgResult.reason),
				);
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
				}
			} else {
				// Prefix with [renderer:useModelConfig] per the log-prefix convention.
				console.error(
					"[renderer:useModelConfig] Failed to get model status:",
					statusResult.reason,
				);
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
				// Prefix with [renderer:useModelConfig] per the log-prefix convention.
				console.error(
					"[renderer:useModelConfig] Failed to get model catalog:",
					catalogResult.reason,
				);
			}
		} finally {
			markUpdatedRef.current();
		}
	}, []);

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

	//previously ``updateConfig`` swallowed ``set_config`` errors
	// (try/catch with only ``console.error``), so callers like
	// ``selectModel`` / ``saveApiKey`` / ``setCloudConsent``
	// always showed their SUCCESS toast even when the backend save
	// failed. The wrapper now re-throws on error so each caller can
	// branch on the result.
	const updateConfig = useCallback(
		async (updates: Partial<VoiceTyperConfig>): Promise<void> => {
			// callRef mirror (same convention as loadConfig /
			// refreshModelStatus) so the identity stays stable even under
			// test mocks that return a fresh `call` per render.
			await callRef.current("set_config", updates);
		},
		[],
	);

	return {
		config,
		loadError,
		models,
		modelCatalog,
		apiKeys,
		setApiKeys,
		loadConfig,
		// internal — facade destructures these out
		refreshModelStatus,
		updateConfig,
		setConfig,
		setModels,
	};
}
