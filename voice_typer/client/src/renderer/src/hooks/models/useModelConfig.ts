import { useCallback, useEffect, useRef, useState } from "react";
import { safeApiKey } from "@/hooks/models/useCloudProviders";
import { useLatestRef } from "@/hooks/useLatestRef";
import { type PythonCall, usePythonEvent } from "@/hooks/usePython";
import { peekIpcCache, writeIpcCache } from "@/lib/ipcCache";
import {
	applyActiveState,
	INITIAL_MODELS,
	type ModelInfo,
	type ModelMetadata,
} from "@/lib/utils/models";
import type { LausuConfig } from "@/types/config";
import type { ModelStatusResponse, ModelStorageSummary } from "@/types/ipc";

// ── Types ─────────────────────────────────────────────────────────────

// Module-cache key for the SWR seed (see lib/ipcCache.ts).
const MODELS_CONFIG_CACHE_KEY = "models.config";

interface UseModelConfigArgs {
	call: PythonCall;
	markUpdated: () => void;
}

export interface UseModelConfigResult {
	// public (spread into the facade's return)
	config: LausuConfig | null;
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
	/** Shared-hub storage summary from `get_model_status._storage`
	 *  (null until the first status fetch settles or on older backends). */
	storage: ModelStorageSummary | null;
	// internal (facade destructures these out, not part of the public
	// return shape of useModelLifecycle)
	refreshModelStatus: () => Promise<void>;
	updateConfig: (updates: Partial<LausuConfig>) => Promise<void>;
	setConfig: React.Dispatch<React.SetStateAction<LausuConfig | null>>;
	setModels: React.Dispatch<React.SetStateAction<ModelInfo[]>>;
}

// ── Hook ──────────────────────────────────────────────────────────────

export function useModelConfig({
	call,
	markUpdated,
}: UseModelConfigArgs): UseModelConfigResult {
	// Ref mirrors of `call` / `markUpdated` so `loadConfig` keeps a
	// STABLE identity ([] deps). Both are useCallback-stable in
	// production, but test mocks return FRESH functions per render, an
	// identity churn would re-fire the mount-load effect (loadConfig →
	// setModels/setConfig → re-render → new call → loop → worker OOM).
	// Same pattern as useVocabulary.ts.
	const callRef = useLatestRef(call);
	const markUpdatedRef = useRef(markUpdated);
	useEffect(() => {
		markUpdatedRef.current = markUpdated;
	}, [markUpdated]);

	// SWR seed: revisit renders the last visit's config instantly from
	// the module cache (survives page unmount) so the page skips its
	// loading branch entirely, `loadConfig` below still revalidates.
	// Read ONCE at init (lazy useState initializers), not per render.
	const [config, setConfig] = useState<LausuConfig | null>(
		() => peekIpcCache<LausuConfig>(MODELS_CONFIG_CACHE_KEY) ?? null,
	);
	// Failure surface for the gating `get_config` fetch. Without this,
	// a rejected `get_config` left `config` null forever and the page
	// spun on its loading branch with no recovery path.
	const [loadError, setLoadError] = useState<string | null>(null);
	const [models, setModels] = useState<ModelInfo[]>(() => {
		const seeded = peekIpcCache<LausuConfig>(MODELS_CONFIG_CACHE_KEY);
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
	// merges, consent flips via setConfig), not just the loadConfig
	// path, so the next page visit seeds from current data instead of
	// flickering back to a stale value until revalidation lands.
	useEffect(() => {
		if (config) writeIpcCache(MODELS_CONFIG_CACHE_KEY, config);
	}, [config]);
	const [modelCatalog, setModelCatalog] = useState<
		Record<string, ModelMetadata>
	>({});
	const [apiKeys, setApiKeys] = useState<Record<string, string>>({});
	// Shared-hub storage summary (the `_storage` key of the
	// `get_model_status` payload). Kept beside the model list because
	// both derive from the same fetch; null = unknown, not zero.
	const [storage, setStorage] = useState<ModelStorageSummary | null>(null);

	// Per-mount config cache (replaces module-level
	// `_cachedConfig`). The ref lets the `config_changed` event handler
	// merge incoming partial updates without re-fetching the whole
	// config, and without leaking state across HMR / test mounts.
	const cachedConfigRef = useRef<LausuConfig | null>(null);

	// Refresh-model-status helper ─────────────────────────
	// downloaded/depsOk = true" reconciliation block was duplicated
	// verbatim in both `loadConfig` and `selectModel`. Extracted into a
	// single helper so future call sites (and bug fixes) apply uniformly.
	//  STALE-ACTIVE fix: the "active model is always considered
	// assumption: the configured model can be removed from disk
	// out-of-band (deleted folder, moved cache), leaving the config
	// pointing at a missing model. The backend's `get_model_status`
	// (which stats the actual filesystem) is authoritative, the card
	// for an active-but-missing model must show `downloaded: false` so
	// the UI offers a restore/clear affordance instead of a dead-end
	// disabled "Active" tick.
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract, .current must NOT become a dep
	const refreshModelStatus = useCallback(async (): Promise<void> => {
		try {
			const status =
				await callRef.current<ModelStatusResponse>("get_model_status");
			if (status && typeof status === "object") {
				setStorage(status._storage ?? null);
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

	// Parallelized loadConfig ─────────────────────────────
	// `get_model_status`, then awaited `get_model_catalog`, strictly
	// sequential. A slow `get_model_catalog` delayed the page render
	// even though the model cards don't need catalog metadata to render
	// their skeleton.
	// Now we fire all three in parallel via `Promise.allSettled`. The
	// `get_config` result is the gating one, `applyActiveState` runs
	// as soon as it resolves. The other two settle in the background.
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract, .current must NOT become a dep
	const loadConfig = useCallback(async (): Promise<void> => {
		// Claim the load generation, an earlier in-flight load whose
		// responses resolve after this one started must not clobber the
		// fresher state this run produces.
		const generation = ++loadGenerationRef.current;
		const isCurrent = () => loadGenerationRef.current === generation;
		try {
			const results = await Promise.allSettled([
				callRef.current<LausuConfig>("get_config"),
				callRef.current<ModelStatusResponse>("get_model_status"),
				callRef.current<{ models: ModelMetadata[] }>("get_model_catalog"),
			]);

			const cfgResult = results[0];
			const statusResult = results[1];
			const catalogResult = results[2];

			if (!isCurrent()) {
				// A newer loadConfig superseded this run, its results own
				// the state now; applying this run's (older) responses
				// would regress config/models/apiKeys.
				return;
			}
			if (cfgResult.status === "fulfilled") {
				setLoadError(null);
				const cfg = cfgResult.value;
				cachedConfigRef.current = cfg;
				setConfig(cfg);
				// SWR write-through, the next visit seeds from this snapshot.
				writeIpcCache(MODELS_CONFIG_CACHE_KEY, cfg);

				// Apply active-state mapping immediately so the cards
				// render with the right Active badge on first paint.
				// First load: `models` is still [], seed from the
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
					setStorage(status._storage ?? null);
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
				const merged = { ...prev, ...data } as LausuConfig;
				cachedConfigRef.current = merged;
				setConfig(merged);
				setModels((curr) => applyActiveState(curr, merged));
				return undefined;
			},
			[],
		),
	);

	// (try/catch with only ``console.error``), so callers like
	// ``selectModel`` / ``saveApiKey`` / ``setCloudConsent``
	// always showed their SUCCESS toast even when the backend save
	// failed. The wrapper now re-throws on error so each caller can
	// branch on the result.
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract, .current must NOT become a dep
	const updateConfig = useCallback(
		async (updates: Partial<LausuConfig>): Promise<void> => {
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
		storage,
		// internal, facade destructures these out
		refreshModelStatus,
		updateConfig,
		setConfig,
		setModels,
	};
}
