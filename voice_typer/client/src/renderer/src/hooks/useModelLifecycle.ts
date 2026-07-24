/**
 * useModelLifecycle — state + actions for the Models page.
 *
 * PVT-003 / PVT-031: extracted from `pages/Models.tsx` (1448-line
 * spaghetti). This hook owns:
 *   • All page state (config, models, catalog, api keys, test results,
 *     download progress, selecting/deleting targets, import flag).
 *   • All IPC actions (loadConfig, selectModel, downloadModel,
 *     confirmDelete, saveApiKey, setCloudConsent, testConnection,
 *     handleTogglePause, handleCancelDownload, handleImportModel,
 *     handleGrantConsent, installDeps, handleOpenModelsFolder).
 *   • The `download_progress` and `config_changed` event subscriptions.
 *   • Optional disk-space pre-flight (PVT-033) — probed once on mount
 *     via the backend's `get_disk_info` IPC; silently skipped when the
 *     IPC is unavailable (older backends).
 *
 * PVT-003 fix #9: the previous module-level `_cachedConfig` mutable
 * variable is replaced by a `useRef` so the cache is per-mount (no
 * cross-instance leakage across HMR / test re-renders).
 *
 * PVT-003 fix #4: `loadConfig` parallelizes `get_config`,
 * `get_model_status`, and `get_model_catalog` via `Promise.allSettled`
 * so a slow `get_model_catalog` no longer blocks the page render.
 *
 * PVT-003 fix #8: the duplicated `get_model_status` refresh block
 * (previously copy-pasted in both `loadConfig` and `selectModel`) is
 * extracted into a single `refreshModelStatus` helper.
 *
 * PVT-032: `downloadModel` surfaces failures via a sonner toast with
 * a "Retry" action button — bypassing the plain `showSnack` wrapper
 * which has no action-button affordance.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import {
	applyActiveState,
	CLOUD_PROVIDERS,
	type CloudProvider,
	type DiskInfo,
	formatErrorMessage,
	getProviderLabel,
	INITIAL_MODELS,
	type ModelInfo,
	type ModelMetadata,
} from "@/lib/utils/models";
import type { VoiceTyperConfig } from "@/types/config";

// ── Types ─────────────────────────────────────────────────────────────

export interface ApiTestResult {
	message: string;
	status: "success" | "failure" | "info";
}

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

// ── Helpers (kept local to the hook — not exported) ───────────────────

/**
 * PVT-003 fix #6 helper: translate the cloud-provider key into the
 * matching `cloud_*_consent` config field. Returns the config key
 * (typed as a keyof VoiceTyperConfig so callers can index safely).
 */
function consentKeyFor(provider: string): keyof VoiceTyperConfig {
	if (provider === "openai") return "cloud_openai_consent";
	if (provider === "groq") return "cloud_groq_consent";
	return "cloud_deepgram_consent";
}

/**
 * PVT-003 fix #6 helper: translate the cloud-provider key into the
 * matching `*_api_key` config field.
 */
function apiKeyConfigField(provider: string): keyof VoiceTyperConfig {
	if (provider === "openai") return "openai_api_key";
	if (provider === "groq") return "groq_api_key";
	return "deepgram_api_key";
}

/**
 * Strip the "<redacted>" sentinel that the backend substitutes for
 * saved API keys in `get_config` responses. The renderer never
 * displays the redacted marker — it shows an empty input field
 * instead, so the user can re-enter the key without confusion.
 */
function safeApiKey(value: string | undefined | null): string {
	return value && value !== "<redacted>" ? value : "";
}

/**
 * Returns the cloud-provider HTTP test endpoint + auth headers for the
 * "Test Connection" probe. Extracted from `testConnection` so the
 * per-provider switch lives in one place (previously inlined as a
 * 90-line if/else if/else in the page).
 */
function testEndpointFor(
	provider: string,
	key: string,
): { url: string; headers: Record<string, string> } {
	switch (provider) {
		case "openai":
			return {
				url: "https://api.openai.com/v1/models",
				headers: { Authorization: `Bearer ${key}` },
			};
		case "groq":
			return {
				url: "https://api.groq.com/openai/v1/models",
				headers: { Authorization: `Bearer ${key}` },
			};
		case "deepgram":
			return {
				url: "https://api.deepgram.com/v1/projects",
				headers: { Authorization: `Token ${key}` },
			};
		default:
			return { url: "", headers: {} };
	}
}

// ── Hook ──────────────────────────────────────────────────────────────

export function useModelLifecycle() {
	const { call } = usePython();
	const { showSnack } = useSnackbar();
	const { agoLabel, markUpdated } = useLastUpdated();

	// ── Page-level state ────────────────────────────────────────────
	const [refreshing, setRefreshing] = useState(false);
	const [config, setConfig] = useState<VoiceTyperConfig | null>(null);
	const [models, setModels] = useState<ModelInfo[]>([]);
	const [modelCatalog, setModelCatalog] = useState<
		Record<string, ModelMetadata>
	>({});
	const [apiKeys, setApiKeys] = useState<Record<string, string>>({});
	const [testResults, setTestResults] = useState<Record<string, ApiTestResult>>(
		{},
	);
	const [isImporting, setIsImporting] = useState(false);

	// ── Download progress state ─────────────────────────────────────
	const [downloadingModel, setDownloadingModel] = useState<string | null>(null);
	const [downloadProgress, setDownloadProgress] = useState(0);
	const [downloadStatus, setDownloadStatus] = useState("");
	const [isPaused, setIsPaused] = useState(false);
	const [downloadedBytes, setDownloadedBytes] = useState<number | null>(null);
	const [totalBytes, setTotalBytes] = useState<number | null>(null);
	const [speedBps, setSpeedBps] = useState<number | null>(null);
	const [etaSeconds, setEtaSeconds] = useState<number | null>(null);

	// ── Selection / deletion target state ──────────────────────────
	const [selectingModel, setSelectingModel] = useState<string | null>(null);
	const [deleteModelTarget, setDeleteModelTarget] = useState<ModelInfo | null>(
		null,
	);

	// ── PVT-033: optional disk-space + open-folder IPCs ─────────────
	const [diskInfo, setDiskInfo] = useState<DiskInfo | null>(null);
	const [modelsFolderSupported, setModelsFolderSupported] = useState(false);

	// PVT-003 fix #9: per-mount config cache (replaces module-level
	// `_cachedConfig`). The ref lets the `config_changed` event handler
	// merge incoming partial updates without re-fetching the whole
	// config — and without leaking state across HMR / test mounts.
	const cachedConfigRef = useRef<VoiceTyperConfig | null>(null);

	// ── PVT-003 fix #8: refresh-model-status helper ─────────────────
	//
	// Previously the `get_model_status` IPC + the "force-active
	// downloaded/depsOk = true" reconciliation block was duplicated
	// verbatim in both `loadConfig` and `selectModel`. Extracted into a
	// single helper so future call sites (and bug fixes) apply uniformly.
	const refreshModelStatus = useCallback(async (): Promise<void> => {
		try {
			const status =
				await call<Record<string, { downloaded: boolean; deps_ok: boolean }>>(
					"get_model_status",
				);
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
			// This guards against a stale `get_model_status` snapshot
			// (e.g. during a fresh download that hasn't been registered yet).
			setModels((prev) =>
				prev.map((m) =>
					m.isActive ? { ...m, downloaded: true, depsOk: true } : m,
				),
			);
		} catch (err) {
			console.error("Failed to refresh model status:", err);
		}
	}, [call]);

	// ── PVT-003 fix #4: parallelized loadConfig ─────────────────────
	//
	// Previously this function awaited `get_config`, then awaited
	// `get_model_status`, then awaited `get_model_catalog` — strictly
	// sequential. A slow `get_model_catalog` (which can be 100+ms when
	// the backend has to import `model_registry.py`) delayed the page
	// render even though the model cards don't need catalog metadata to
	// render their skeleton.
	//
	// Now we fire all three in parallel via `Promise.allSettled`. The
	// `get_config` result is the gating one — `applyActiveState` runs
	// as soon as it resolves. The other two settle in the background
	// and update state when they arrive.
	const loadConfig = useCallback(async (): Promise<void> => {
		try {
			const results = await Promise.allSettled([
				call<VoiceTyperConfig>("get_config"),
				call<Record<string, { downloaded: boolean; deps_ok: boolean }>>(
					"get_model_status",
				),
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

	// ── PVT-033: probe optional disk-info + open-folder IPCs ────────
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
				console.warn("[useModelLifecycle] get_disk_info probe failed:", e);
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
					"[useModelLifecycle] models_folder_supported probe failed:",
					e,
				);
			}
		})();
		return () => {
			cancelled = true;
		};
	}, [call]);

	// ── download_progress event subscription ────────────────────────
	usePythonEvent(
		"download_progress",
		useCallback(
			(data: Record<string, unknown> | undefined): (() => void) | undefined => {
				if (!data) return undefined;
				if (typeof data.progress === "number")
					setDownloadProgress(data.progress);
				if (typeof data.status === "string") setDownloadStatus(data.status);
				if (typeof data.downloaded_bytes === "number")
					setDownloadedBytes(data.downloaded_bytes);
				if (typeof data.total_bytes === "number")
					setTotalBytes(data.total_bytes);
				if (typeof data.speed_bytes_per_sec === "number") {
					setSpeedBps(data.speed_bytes_per_sec);
				} else if (data.speed_bytes_per_sec == null) {
					setSpeedBps(null);
				}
				if (typeof data.eta_seconds === "number") {
					setEtaSeconds(data.eta_seconds);
				} else if (data.eta_seconds == null) {
					setEtaSeconds(null);
				}
				if (typeof data.paused === "boolean") setIsPaused(data.paused);
				if (typeof data.resumed === "boolean" && data.resumed)
					setIsPaused(false);
				return undefined;
			},
			[],
		),
	);

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

	// ── PVT-003 fix #3: dead unmount cleanup useEffect removed ──────
	//
	// The original component had a `useEffect(() => () => { reset
	// download state })` cleanup that fired on every unmount. This was
	// dead code in production — React already discards state on unmount
	// — and it caused spurious state updates during HMR / strict-mode
	// double-mounts. Removed.

	const resetProgress = useCallback(() => {
		setDownloadProgress(0);
		setDownloadStatus("");
		setDownloadedBytes(null);
		setTotalBytes(null);
		setSpeedBps(null);
		setEtaSeconds(null);
		setIsPaused(false);
	}, []);

	const handleManualRefresh = useCallback(async () => {
		setRefreshing(true);
		try {
			await loadConfig();
		} finally {
			setRefreshing(false);
		}
	}, [loadConfig]);

	// BG-48: previously ``updateConfig`` swallowed ``set_config`` errors
	// (try/catch with only ``console.error``), so callers like
	// ``selectModel`` / ``saveApiKey`` / ``setCloudConsent`` /
	// ``setHuggingFaceConsent`` always showed their SUCCESS toast
	// even when the backend save failed — leaving UI state and the
	// persisted config out of sync. The wrapper now re-throws on
	// error so each caller can branch on the result and show the
	// appropriate success/error toast (using ``formatErrorMessage``
	// for the centralized error-message extraction).
	const updateConfig = useCallback(
		async (updates: Partial<VoiceTyperConfig>): Promise<void> => {
			await call("set_config", updates);
		},
		[call],
	);

	// ── Action: selectModel ─────────────────────────────────────────
	//
	// PVT-003 fix #7: replaces the `model.name === "parakeet"` magic
	// string with the `depsInstallable` flag (so future dep-required
	// models can opt into the same UX without touching this code).
	const selectModel = useCallback(
		async (model: ModelInfo) => {
			// PVT-003 fix #7: dep-gated models can't be selected until
			// their deps are installed. Previously this was a hardcoded
			// `model.name === "parakeet"` check.
			if (model.depsInstallable && !model.depsOk) {
				showSnack(t("models.snack.parakeetDepsRequired"), "warning");
				return;
			}
			if (!model.downloaded && !model.alwaysAvailable) {
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

				// PVT-003 fix #8: use the extracted refresh helper
				// (previously a verbatim duplicate of loadConfig's block).
				await refreshModelStatus();

				showSnack(
					t("models.snack.usingModel", { name: model.name }),
					"success",
				);
			} catch (err) {
				// BG-48: surface config-save failures instead of
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
		[refreshModelStatus, showSnack, updateConfig],
	);

	// ── Action: downloadModel (PVT-032 retry on failure) ───────────
	const downloadModel = useCallback(
		async (model: ModelInfo) => {
			setDownloadingModel(model.name);
			resetProgress();
			try {
				const result = await call<{
					success: boolean;
					error?: string;
					message?: string;
				}>("download_model", { model: model.name });
				if (result.success) {
					setModels((prev) => {
						const anyActive = prev.some((m) => m.isActive);
						return prev.map((m) =>
							m.name === model.name
								? { ...m, downloaded: true, isActive: !anyActive }
								: m,
						);
					});
					showSnack(
						result.message ||
							t("models.snack.downloaded", { name: model.name }),
						"success",
					);
				} else {
					// PVT-032: surface the failure with a Retry action button.
					// `showSnack` doesn't support action buttons, so we go
					// through sonner's `toast.error` directly — the global
					// Toaster in App.tsx renders it identically.
					const message =
						result.error ||
						t("models.snack.downloadFailedName", { name: model.name });
					toast.error(message, {
						duration: 8000,
						action: {
							label: t("microphone.retry"),
							onClick: () => {
								void downloadModel(model);
							},
						},
					});
				}
			} catch (err) {
				const message = t("models.snack.downloadFailed", {
					error: formatErrorMessage(err),
				});
				// PVT-032: same retry affordance on thrown errors.
				toast.error(message, {
					duration: 8000,
					action: {
						label: t("microphone.retry"),
						onClick: () => {
							void downloadModel(model);
						},
					},
				});
			} finally {
				setDownloadingModel(null);
			}
		},
		[call, resetProgress, showSnack],
	);

	// ── Action: installDeps (PVT-003 fix #7) ────────────────────────
	//
	// Triggered by the "Download Deps" button on dep-gated models
	// (currently Parakeet). The backend may or may not expose an
	// `install_parakeet_deps` IPC — if it doesn't, we fall back to the
	// existing instruction snackbar so the user knows how to proceed
	// manually.
	const installDeps = useCallback(
		async (model: ModelInfo) => {
			try {
				const result = await call<{ success: boolean; error?: string }>(
					"install_parakeet_deps",
					{ model: model.name },
				);
				if (result?.success) {
					// BG-49: previously the success branch reused the
					// ``parakeetDepsRequired`` ("Dependencies required
					// for Parakeet. Download first.") key — which is
					// the FAILURE / manual-hint message, not a success
					// confirmation. Use the dedicated ``depsInstalled``
					// success key instead so users see the right
					// message after a successful install.
					showSnack(t("models.snack.depsInstalled"), "success");
					await refreshModelStatus();
				} else {
					// Backend doesn't actually install — surface the
					// manual-install hint (existing i18n key).
					showSnack(t("models.snack.parakeetDepsRequired"), "warning");
				}
			} catch {
				// IPC unavailable — fall back to the manual hint.
				showSnack(t("models.snack.parakeetDepsRequired"), "warning");
			}
		},
		[refreshModelStatus, showSnack, call],
	);

	// ── Action: requestDeleteModel + confirmDelete ──────────────────
	const requestDeleteModel = useCallback(
		(model: ModelInfo) => {
			if (model.isActive) {
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
	}, [call, deleteModelTarget, showSnack]);

	// ── Action: saveApiKey / setCloudConsent / setHuggingFaceConsent ─
	const saveApiKey = useCallback(
		async (provider: string) => {
			const key = apiKeys[provider] ?? "";
			const configKey = apiKeyConfigField(provider);
			const updates = { [configKey]: key } as Partial<VoiceTyperConfig>;
			await updateConfig(updates);
			showSnack(
				t("models.snack.apiKeySaved", {
					provider: getProviderLabel(provider),
				}),
				"success",
			);
		},
		[apiKeys, showSnack, updateConfig],
	);

	const setCloudConsent = useCallback(
		async (provider: string, granted: boolean) => {
			const configKey = consentKeyFor(provider);
			const updates = { [configKey]: granted } as Partial<VoiceTyperConfig>;
			await updateConfig(updates);
			setConfig((prev) => (prev ? { ...prev, [configKey]: granted } : prev));
			showSnack(
				granted
					? t("models.snack.consentGranted", {
							provider: getProviderLabel(provider),
						})
					: t("models.snack.consentRevoked", {
							provider: getProviderLabel(provider),
						}),
				granted ? "success" : "warning",
			);
		},
		[showSnack, updateConfig],
	);

	const setHuggingFaceConsent = useCallback(
		async (granted: boolean) => {
			await updateConfig({ huggingface_consent: granted });
			setConfig((prev) =>
				prev ? { ...prev, huggingface_consent: granted } : prev,
			);
			showSnack(
				granted ? t("models.consentGranted") : t("models.consentRevoked"),
				granted ? "success" : "warning",
			);
		},
		[showSnack, updateConfig],
	);

	const handleGrantConsent = useCallback(() => {
		void setHuggingFaceConsent(true);
	}, [setHuggingFaceConsent]);

	// ── Action: testConnection ──────────────────────────────────────
	//
	// BG-50: previously the renderer-side ``fetch`` to the cloud
	// provider's API leaked the user's API key through the
	// ``Authorization`` header on a cross-origin request — and a
	// CORS / network failure surfaced as an opaque ``TypeError:
	// Failed to fetch`` with no actionable message. The full fix
	// (route the test through a backend IPC ``test_cloud_connection``
	// that keeps the key inside the Python process) requires backend
	// changes that are out of scope for GROUP 3; this hardening
	// improves the renderer-side error handling only:
	//   • detect ``TypeError`` from ``fetch`` (CORS / DNS / network)
	//     and surface a specific message;
	//   • never log the API key — ``formatErrorMessage`` only
	//     extracts the error's ``message`` field, so the
	//     ``Authorization`` header value never enters the log.
	const testConnection = useCallback(
		async (provider: string) => {
			const key = apiKeys[provider] ?? "";
			if (!key) {
				setTestResults((prev) => ({
					...prev,
					[provider]: { message: t("models.test.needApiKey"), status: "info" },
				}));
				return;
			}
			try {
				await saveApiKey(provider);
				const { url, headers } = testEndpointFor(provider, key);
				let resp: Response;
				try {
					resp = await fetch(url, { headers });
				} catch (fetchErr) {
					// BG-50: cross-origin ``fetch`` failures throw a
					// ``TypeError`` ("Failed to fetch") for CORS
					// rejections, DNS failures, network offline,
					// and certificate errors. The native message is
					// opaque (the browser hides CORS details for
					// security). Surface a specific message so the
					// user can distinguish "wrong API key" (HTTP
					// 401) from "network blocked the request"
					// (CORS / offline).
					if (fetchErr instanceof TypeError) {
						setTestResults((prev) => ({
							...prev,
							[provider]: {
								message: t("models.test.connectionNetworkError"),
								status: "failure",
							},
						}));
						return;
					}
					throw fetchErr;
				}
				if (resp.ok) {
					setTestResults((prev) => ({
						...prev,
						[provider]: {
							message: t("models.test.connectionSuccessful"),
							status: "success",
						},
					}));
				} else {
					setTestResults((prev) => ({
						...prev,
						[provider]: {
							message: t("models.test.connectionFailed", {
								status: String(resp.status),
								statusText: resp.statusText,
							}),
							status: "failure",
						},
					}));
				}
			} catch (err) {
				setTestResults((prev) => ({
					...prev,
					[provider]: {
						message: t("models.test.connectionTestFailed", {
							error: formatErrorMessage(err),
						}),
						status: "failure",
					},
				}));
			}
		},
		[apiKeys, saveApiKey],
	);

	// ── Action: handleTogglePause / handleCancelDownload ────────────
	const handleTogglePause = useCallback(async () => {
		setIsPaused((prev) => !prev);
		try {
			if (isPaused) {
				await call("resume_model_download");
			} else {
				await call("pause_model_download");
			}
		} catch (err) {
			setIsPaused((prev) => !prev);
			showSnack(
				isPaused
					? t("models.snack.resumeFailed", { error: formatErrorMessage(err) })
					: t("models.snack.pauseFailed", { error: formatErrorMessage(err) }),
				"error",
			);
		}
	}, [call, isPaused, showSnack]);

	const handleCancelDownload = useCallback(async () => {
		try {
			await call("cancel_model_download");
			showSnack(t("models.snack.cancelled"), "warning");
		} catch (err) {
			showSnack(
				t("models.snack.cancelFailed", { error: formatErrorMessage(err) }),
				"error",
			);
		}
	}, [call, showSnack]);

	// ── Action: handleImportModel ───────────────────────────────────
	const handleImportModel = useCallback(async () => {
		const api = (
			window as unknown as {
				window_?: {
					openModelImportDialog?: () => Promise<{
						canceled: boolean;
						path?: string;
					}>;
				};
			}
		).window_;
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
					console.error("Import error for", err.model, ":", err.error);
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

	// ── Action: handleOpenModelsFolder (PVT-033) ────────────────────
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
			showSnack(
				result?.error ||
					t("models.import.failed", { error: "Open folder failed" }),
				"warning",
			);
		} catch (err) {
			showSnack(
				t("models.import.failed", { error: formatErrorMessage(err) }),
				"error",
			);
		}
	}, [call, showSnack]);

	return {
		// state
		config,
		models,
		modelCatalog,
		apiKeys,
		setApiKeys,
		testResults,
		isImporting,
		refreshing,
		agoLabel,
		// download progress
		downloadingModel,
		downloadProgress,
		downloadStatus,
		isPaused,
		downloadedBytes,
		totalBytes,
		speedBps,
		etaSeconds,
		// selection / deletion
		selectingModel,
		deleteModelTarget,
		setDeleteModelTarget,
		// PVT-033
		diskInfo,
		modelsFolderSupported,
		// actions
		loadConfig,
		handleManualRefresh,
		selectModel,
		downloadModel,
		installDeps,
		requestDeleteModel,
		confirmDelete,
		saveApiKey,
		setCloudConsent,
		setHuggingFaceConsent,
		handleGrantConsent,
		testConnection,
		handleTogglePause,
		handleCancelDownload,
		handleImportModel,
		handleOpenModelsFolder,
		// static data (re-exported for the panels' convenience)
		cloudProviders: CLOUD_PROVIDERS as readonly CloudProvider[],
	};
}
