import {
	Alert02Icon,
	Delete01Icon,
	Download01Icon,
	Folder02Icon,
	PlayIcon,
	Shield01Icon,
	SparklesIcon,
	Tick02Icon,
	ZapIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type React from "react";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import PageHeading from "@/components/common/PageHeading";
import { Spinner } from "@/components/feedback/Spinner";
import { DownloadProgressBar } from "@/components/models/DownloadProgressBar";
import {
	Accordion,
	AccordionContent,
	AccordionItem,
	AccordionTrigger,
} from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
	SegmentedControl,
	type SegmentedControlOption,
} from "@/components/ui/segmented-control";
import { Switch } from "@/components/ui/switch";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import type { VoiceTyperConfig } from "@/types/config"; // Module-level cache — persists across page navigations so the models view

// renders instantly on re-visit instead of showing a loading spinner.
let _cachedConfig: VoiceTyperConfig | null = null;

interface ModelInfo {
	name: string;
	size: string;
	speed: string;
	backend: string;
	downloaded: boolean;
	depsOk: boolean;
	isActive: boolean;
	// UX-010: replaces the magic string `!model.alwaysAvailable` check.
	// Qwen doesn't need a separate download step (it auto-downloads
	// from HuggingFace on first use), so the "Download" button is
	// hidden for models where alwaysAvailable is true.
	alwaysAvailable?: boolean;
}

// NEW-MODEL-001: rich metadata from the backend's MODEL_REGISTRY.
// Fetched via the ``get_model_catalog`` IPC command and used to
// populate model cards with VRAM, language, and speed badges.
interface ModelMetadata {
	name: string;
	download_size_mb: number;
	required_vram_mb: number;
	backend: string;
	multilingual: boolean;
	supported_languages: string[] | null; // null = all languages
	description: string;
	repo_id: string;
	is_distilled: boolean;
	speed_rating: string; // "fast" | "medium" | "slow"
	accuracy_rating: string; // "low" | "medium" | "high"
}

const CLOUD_PROVIDERS = [
	{
		key: "openai",
		url: "https://api.openai.com/v1/audio/transcriptions",
		model: "whisper-1",
	},
	{
		key: "groq",
		url: "https://api.groq.com/openai/v1/audio/transcriptions",
		model: "whisper-large-v3",
	},
	{
		key: "deepgram",
		url: "https://api.deepgram.com/v1/listen",
		model: "nova-2",
	},
] as const;

// I18N-FIX: provider labels are translated at render time (not baked into
// the module-level constant) so locale changes take effect on the next
// render without re-loading the module.  ``key`` is the stable identifier;
// ``label`` is derived via getProviderLabel().
function getProviderLabel(providerKey: string): string {
	switch (providerKey) {
		case "openai":
			return t("models.providers.openai.label");
		case "groq":
			return t("models.providers.groq.label");
		case "deepgram":
			return t("models.providers.deepgram.label");
		default:
			return providerKey;
	}
}

// I18N-FIX: model size "Variable" is a sentinel for qwen / always-available
// models.  Translate it for display; pass through literal sizes like ~466MB.
function formatModelSize(size: string): string {
	return size === "Variable" ? t("models.variable") : size;
}

// VRAM formatting: show in GB when >= 1024 MB, otherwise in MB.
function formatVram(mb: number): string {
	if (mb >= 1024) {
		return `${(mb / 1024).toFixed(1)}GB`;
	}
	return `${mb}MB`;
}

const INITIAL_MODELS: ModelInfo[] = [
	{
		name: "tiny.en",
		size: "~75MB",
		speed: "Fastest",
		backend: "whisper",
		downloaded: false,
		depsOk: true,
		isActive: false,
	},
	{
		name: "small.en",
		size: "~466MB",
		speed: "Fast",
		backend: "whisper",
		downloaded: false,
		depsOk: true,
		isActive: false,
	},
	{
		name: "medium.en",
		size: "~1.5GB",
		speed: "Slow",
		backend: "whisper",
		downloaded: false,
		depsOk: true,
		isActive: false,
	},
	// NEW-MODEL-001: turbo + distilled variants.  Sizes from the
	// backend's MODEL_REGISTRY (download_size_mb field).
	{
		name: "large-v3-turbo",
		size: "~809MB",
		speed: "Fast",
		backend: "whisper",
		downloaded: false,
		depsOk: true,
		isActive: false,
	},
	{
		name: "distil-large-v3",
		size: "~1.5GB",
		speed: "Fast",
		backend: "distil-whisper",
		downloaded: false,
		depsOk: true,
		isActive: false,
	},
	{
		name: "distil-medium.en",
		size: "~780MB",
		speed: "Fast",
		backend: "distil-whisper",
		downloaded: false,
		depsOk: true,
		isActive: false,
	},
	// UX-010: alwaysAvailable replaces the magic string `!model.alwaysAvailable`
	{
		name: "qwen",
		size: "Variable",
		speed: "Fast",
		backend: "qwen",
		downloaded: false,
		depsOk: true,
		isActive: false,
		alwaysAvailable: true,
	},
	{
		name: "parakeet",
		size: "~2.5GB",
		speed: "Fast",
		backend: "parakeet",
		downloaded: false,
		depsOk: false,
		isActive: false,
	},
];

// NEW-PAUSE-001: helpers for formatting the rich download progress
// display.  Pure functions — no state, no IPC.  Exported indirectly
// via closure when used in the JSX below.

/**
 * UX-ERR-001: format an unknown caught value as a user-friendly string.
 *
 * Catch blocks frequently do ``showSnack(`Failed: ${err}`)`` which
 * stringifies the error via ``String(err)``.  For plain ``Error``
 * objects this produces ``"Error: <message>"`` (acceptable), but for
 * non-Error values it produces ``"[object Object]"`` (cryptic) or
 * ``"undefined"`` (useless).  This helper extracts a useful message
 * from any thrown value so the snackbar text is always actionable.
 *
 * @param err - the value caught in a ``catch (err)`` block
 * @param fallback - returned when no useful message can be extracted
 * @returns a short, user-facing string suitable for a snackbar
 */
function formatErrorMessage(err: unknown, fallback = "Unknown error"): string {
	if (err instanceof Error) {
		return err.message || fallback;
	}
	if (typeof err === "string") {
		return err || fallback;
	}
	if (err && typeof err === "object") {
		// IPC responses shape errors as { _error: "..." } or
		// { message: "..." }; prefer those when present.
		const obj = err as { _error?: unknown; message?: unknown; error?: unknown };
		if (typeof obj._error === "string" && obj._error) return obj._error;
		if (typeof obj.message === "string" && obj.message) return obj.message;
		if (typeof obj.error === "string" && obj.error) return obj.error;
	}
	return fallback;
}

// ── Model family grouping ──────────────────────────────────────────────
// Groups models by their backend family (Whisper, Qwen, Parakeet) so they
// render inside family cards with shared headers.
interface ModelFamily {
	id: string;
	name: string;
	description: string | null;
	variants: ModelInfo[];
}

function groupModelsByFamily(models: ModelInfo[]): ModelFamily[] {
	const whisper = models.filter(
		(m) => m.backend === "whisper" || m.backend === "distil-whisper",
	);
	const qwen = models.filter((m) => m.backend === "qwen");
	const parakeet = models.filter((m) => m.backend === "parakeet");
	const families: ModelFamily[] = [];
	if (whisper.length > 0) {
		families.push({
			id: "whisper",
			name: "Whisper",
			description: null,
			variants: whisper,
		});
	}
	if (qwen.length > 0) {
		families.push({
			id: "qwen",
			name: "Qwen",
			description: null,
			variants: qwen,
		});
	}
	if (parakeet.length > 0) {
		families.push({
			id: "parakeet",
			name: "Parakeet",
			description: null,
			variants: parakeet,
		});
	}
	return families;
}

/**
 * Returns the family ID that contains the currently active model,
 * or null if no model is active or no family match is found.
 */
function getActiveFamilyId(cfg: VoiceTyperConfig | null): string | null {
	if (!cfg) return null;
	const activeBackend = cfg.asr_backend ?? "whisper";
	const activeModel = cfg.model_size ?? "small.en";
	for (const m of INITIAL_MODELS) {
		let isActive = false;
		if (m.backend === "whisper") {
			isActive = activeBackend === "whisper" && m.name === activeModel;
		} else {
			isActive = activeBackend === m.backend;
		}
		if (isActive) {
			if (m.backend === "whisper" || m.backend === "distil-whisper") return "whisper";
			if (m.backend === "qwen") return "qwen";
			if (m.backend === "parakeet") return "parakeet";
		}
	}
	return null;
}

export default function ModelsPage() {
	const { call } = usePython();
	const { showSnack, Snackbar } = useSnackbar();
	const [activeTab, setActiveTab] = useState<"local" | "cloud">("local");
	const tabOptions: SegmentedControlOption<string>[] = [
		{ value: "local", label: t("models.localModels") },
		{ value: "cloud", label: t("models.cloudProviders") },
	];
	const [config, setConfig] = useState<VoiceTyperConfig | null>(_cachedConfig);
	const [models, setModels] = useState<ModelInfo[]>(() => {
		// MOODLES-FIX: initialize with correct isActive from cached config
		// so the Accordion defaults to only the active model's family open.
		if (_cachedConfig) {
			const activeBackend = _cachedConfig.asr_backend ?? "whisper";
			const activeModel = _cachedConfig.model_size ?? "small.en";
			return INITIAL_MODELS.map((m) => {
				let isActive = false;
				if (m.backend === "whisper") {
					isActive = activeBackend === "whisper" && m.name === activeModel;
				} else {
					isActive = activeBackend === m.backend;
				}
				return { ...m, isActive };
			});
		}
		return INITIAL_MODELS;
	});
	const [_initialLoading, setInitialLoading] = useState(true);
	const [downloadProgress, setDownloadProgress] = useState(0);
	const [downloadStatus, setDownloadStatus] = useState("");
	const [downloadingModel, setDownloadingModel] = useState<string | null>(null);
	// NEW-PAUSE-001: pause/resume + rich progress fields.
	const [isPaused, setIsPaused] = useState(false);
	const [downloadedBytes, setDownloadedBytes] = useState<number | null>(null);
	const [totalBytes, setTotalBytes] = useState<number | null>(null);
	const [speedBps, setSpeedBps] = useState<number | null>(null);
	const [etaSeconds, setEtaSeconds] = useState<number | null>(null);
	// NEW-MODEL-001: model catalog (rich metadata from backend).
	const [modelCatalog, setModelCatalog] = useState<
		Record<string, ModelMetadata>
	>({});
	const [benchmarkResult, setBenchmarkResult] = useState("");
	const [isBenchmarking, _setIsBenchmarking] = useState(false);
	// Selecting state — disables the "Select" button immediately when clicked
	// so the user gets instant feedback that a model change is in progress
	// (the backend may take several seconds to unload + load the new model).
	const [selectingModel, setSelectingModel] = useState<string | null>(null);

	// #7: ConfirmDialog state for model deletion
	const [deleteModelTarget, setDeleteModelTarget] = useState<ModelInfo | null>(
		null,
	);

	// Cloud provider API keys
	const [apiKeys, setApiKeys] = useState<Record<string, string>>({});
	// I18N-FIX: testResults now carries a status enum alongside the
	// message so the JSX can color the result by status (success /
	// failure / info) instead of grepping the message for "successful"
	// / "Failed" substrings (which would break once the message is
	// translated).
	const [testResults, setTestResults] = useState<
		Record<string, { message: string; status: "success" | "failure" | "info" }>
	>({});

	// Controlled accordion state — only the active model's family is open.
	const [accordionValue, setAccordionValue] = useState<string[]>(() => {
		const activeFamilyId = getActiveFamilyId(_cachedConfig);
		return activeFamilyId ? [activeFamilyId] : [];
	});

	// Sync accordion after async config load completes (first visit only).
	// Uses a ref to fire exactly once in the component lifecycle.
	const syncGuardRef = useRef(false);
	useEffect(() => {
		if (!_initialLoading && !syncGuardRef.current) {
			syncGuardRef.current = true;
			const activeModelName = models.find((m) => m.isActive)?.name;
			if (activeModelName) {
				const families = groupModelsByFamily(models);
				const activeFamily = families.find((f) =>
					f.variants.some((v) => v.name === activeModelName),
				);
				if (activeFamily) {
					setAccordionValue([activeFamily.id]);
				}
			}
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [_initialLoading]);

	const loadConfig = useCallback(async () => {
		setInitialLoading(true);
		try {
			const cfg = await call<VoiceTyperConfig>("get_config");
			_cachedConfig = cfg;
			setConfig(cfg);

			// Update models based on config
			const activeBackend = cfg?.asr_backend ?? "whisper";
			const activeModel = cfg?.model_size ?? "small.en";
			setModels(
				INITIAL_MODELS.map((m) => {
					let isActive = false;
					if (m.backend === "whisper") {
						isActive = activeBackend === "whisper" && m.name === activeModel;
					} else {
						isActive = activeBackend === m.backend;
					}
					return { ...m, isActive };
				}),
			);

			// Item 10/11: fetch real model download status from the backend
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
				// ARCH-007: ensure the currently active model is always reported
				// as downloaded + depsOk regardless of what get_model_status
				// returned. If a model is active, it's necessarily downloaded
				// and its deps are OK — the backend may not correctly detect
				// this for non-Whisper backends (Parakeet, Qwen) that aren't
				// in the standard HF cache layout or use different import
				// module names.
				setModels((prev) =>
					prev.map((m) =>
						m.isActive ? { ...m, downloaded: true, depsOk: true } : m,
					),
				);
			} catch (err) {
				console.error("Failed to get model status:", err);
			}

			// NEW-MODEL-001: fetch rich metadata (VRAM, languages,
			// speed/accuracy ratings) for the model catalog.  Used to
			// populate the model cards with extra info badges.
			try {
				const catalog = await call<{ models: ModelMetadata[] }>(
					"get_model_catalog",
				);
				if (catalog?.models && Array.isArray(catalog.models)) {
					const byName: Record<string, ModelMetadata> = {};
					for (const m of catalog.models) {
						byName[m.name] = m;
					}
					setModelCatalog(byName);
				}
			} catch (err) {
				console.error("Failed to get model catalog:", err);
			}

			setApiKeys({
				// SEC-003: backend redacts keys to '<redacted>'.  Show empty
				// in the input fields so the user can type a new key to
				// replace it; the placeholder conveys "configured".
				openai:
					cfg?.openai_api_key && cfg.openai_api_key !== "<redacted>"
						? cfg.openai_api_key
						: "",
				groq:
					cfg?.groq_api_key && cfg.groq_api_key !== "<redacted>"
						? cfg.groq_api_key
						: "",
				deepgram:
					cfg?.deepgram_api_key && cfg.deepgram_api_key !== "<redacted>"
						? cfg.deepgram_api_key
						: "",
			});
		} catch (err) {
			console.error("Failed to load config:", err);
		} finally {
			setInitialLoading(false);
		}
	}, [call]);

	useEffect(() => {
		loadConfig();
	}, [loadConfig]);

	// UX-005: Subscribe to download_progress push events from the backend
	// so the progress bar and status text update in real time during a
	// model download. Previously these state values were declared but
	// never written, so the progress bar stayed at 0% forever.
	//
	// NEW-PAUSE-001: also read the new rich fields (downloaded_bytes,
	// total_bytes, speed_bytes_per_sec, eta_seconds, paused, resumed)
	// so the renderer can show a detailed progress bar with speed / ETA
	// and a Pause/Resume button.
	usePythonEvent(
		"download_progress",
		useCallback((data: Record<string, unknown> | undefined) => {
			if (!data) return;
			if (typeof data.progress === "number") {
				setDownloadProgress(data.progress);
			}
			if (typeof data.status === "string") {
				setDownloadStatus(data.status);
			}
			if (typeof data.downloaded_bytes === "number") {
				setDownloadedBytes(data.downloaded_bytes);
			}
			if (typeof data.total_bytes === "number") {
				setTotalBytes(data.total_bytes);
			}
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
			if (typeof data.paused === "boolean") {
				setIsPaused(data.paused);
			}
			if (typeof data.resumed === "boolean" && data.resumed) {
				setIsPaused(false);
			}
		}, []),
	);

	// Reset download state when the component unmounts or user navigates away
	useEffect(() => {
		return () => {
			setDownloadingModel(null);
			setDownloadProgress(0);
			setDownloadStatus("");
			setDownloadedBytes(null);
			setTotalBytes(null);
			setSpeedBps(null);
			setEtaSeconds(null);
			setIsPaused(false);
		};
	}, []);

	// Reset progress when a download starts / finishes
	const resetProgress = useCallback(() => {
		setDownloadProgress(0);
		setDownloadStatus("");
		setDownloadedBytes(null);
		setTotalBytes(null);
		setSpeedBps(null);
		setEtaSeconds(null);
		setIsPaused(false);
	}, []);

	const updateConfig = async (updates: Partial<VoiceTyperConfig>) => {
		try {
			await call("set_config", updates);
		} catch (err) {
			console.error("Failed to update config:", err);
		}
	};

	const selectModel = async (model: ModelInfo) => {
		if (model.name === "parakeet" && !model.depsOk) {
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
				updates.asr_backend = model.backend as VoiceTyperConfig["asr_backend"];
				updates.model_size = model.name as VoiceTyperConfig["model_size"];
			}

			await updateConfig(updates);
			setModels((prev) =>
				prev.map((m) => ({ ...m, isActive: m.name === model.name })),
			);
			// Refresh model download/deps status so the user can immediately
			// see the correct state when switching back (e.g. Parakeet depsOk
			// was updated by get_model_status on the previous page load, but
			// after a model change the active-model override no longer applies).
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
				// Force active model as downloaded + depsOk (defensive).
				setModels((prev) =>
					prev.map((m) =>
						m.isActive ? { ...m, downloaded: true, depsOk: true } : m,
					),
				);
			} catch (err) {
				console.error("Failed to refresh model status:", err);
			}
			showSnack(t("models.snack.usingModel", { name: model.name }), "success");
		} finally {
			setSelectingModel(null);
		}
	};

	const downloadModel = async (model: ModelInfo) => {
		// UX-005: Real download via IPC — calls download_model route which
		// loads the model into the HF cache (triggers HuggingFace download).
		// The backend pushes `download_progress` events during the download
		// (see usePythonEvent subscription above); we just initiate the call
		// and update model state on success.
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
					// Auto-activate if no model is currently active (first run)
					const anyActive = prev.some((m) => m.isActive);
					return prev.map((m) =>
						m.name === model.name
							? { ...m, downloaded: true, isActive: !anyActive }
							: m,
					);
				});
				showSnack(
					result.message || t("models.snack.downloaded", { name: model.name }),
					"success",
				);
			} else {
				showSnack(
					result.error ||
						t("models.snack.downloadFailedName", { name: model.name }),
					"error",
				);
			}
		} catch (err) {
			showSnack(
				t("models.snack.downloadFailed", {
					error: formatErrorMessage(err),
				}),
				"error",
			);
		} finally {
			setDownloadingModel(null);
			// Keep the final progress/status visible briefly so the user
			// sees the "complete" message; the next downloadModel() call
			// will reset via resetProgress().
		}
	};

	// #7: ConfirmDialog — ask before deleting a model
	const requestDeleteModel = (model: ModelInfo) => {
		if (model.isActive) {
			showSnack(t("models.cannotDeleteActive"), "warning");
			return;
		}
		setDeleteModelTarget(model);
	};

	const confirmDeleteModel = async () => {
		if (!deleteModelTarget) return;
		// NEW-UX-005: actually call the backend to delete the model files
		// from disk, not just remove from the UI list.  Previously this
		// was a no-op that left 1.5 GB of files on disk.
		// FIX: mark downloaded=false instead of filtering the card out —
		// the model should still appear in the list so the user can
		// re-download it.
		try {
			const result = await call<{ success: boolean; message: string }>(
				"delete_model",
				{ model: deleteModelTarget.name },
			);
			if (result?.success) {
				setModels((prev) =>
					prev.map((m) =>
						m.name === deleteModelTarget.name ? { ...m, downloaded: false } : m,
					),
				);
				showSnack(
					t("models.snack.deleted", { name: deleteModelTarget.name }),
					"warning",
				);
			} else {
				showSnack(result?.message ?? t("models.snack.deleteFailed"), "error");
			}
		} catch (e) {
			showSnack(
				t("models.snack.deleteFailedError", { error: String(e) }),
				"error",
			);
		}
		setDeleteModelTarget(null);
	};

	const saveApiKey = async (provider: string) => {
		const key = apiKeys[provider] ?? "";
		const configKey =
			provider === "openai"
				? "openai_api_key"
				: provider === "groq"
					? "groq_api_key"
					: "deepgram_api_key";
		const updates = { [configKey]: key } as Partial<VoiceTyperConfig>;
		await updateConfig(updates);
		showSnack(
			t("models.snack.apiKeySaved", { provider: getProviderLabel(provider) }),
			"success",
		);
	};

	// NEW-PRIV-006: per-provider consent toggle.  The backend
	// CloudEngine refuses to transcribe without consent (raises
	// ConsentRequiredError), so the renderer MUST expose a way to
	// grant it.  Without this UI, a user who pastes a cloud API key
	// would hit a silent error at dictation time with no in-app
	// explanation.  The toggle is shown only when an API key is
	// configured for the provider — there's no point granting consent
	// without a key.
	//
	// The consent disclosure (what the user is agreeing to) is shown
	// inline above the toggle so the user can make an informed decision
	// without leaving the page.
	const setCloudConsent = async (provider: string, granted: boolean) => {
		const configKey =
			provider === "openai"
				? "cloud_openai_consent"
				: provider === "groq"
					? "cloud_groq_consent"
					: "cloud_deepgram_consent";
		const updates = { [configKey]: granted } as Partial<VoiceTyperConfig>;
		await updateConfig(updates);
		// Mirror to local state so the toggle reflects immediately.
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
	};

	// NEW-PRIV-005: HuggingFace consent toggle.  Shown at the top of
	// the Models page so the user understands why their first model
	// download requires consent.  The backend's _pre_download_model
	// refuses to download without this flag.
	const setHuggingFaceConsent = async (granted: boolean) => {
		await updateConfig({ huggingface_consent: granted });
		setConfig((prev) =>
			prev ? { ...prev, huggingface_consent: granted } : prev,
		);
		showSnack(
			granted ? t("models.consentGranted") : t("models.consentRevoked"),
			granted ? "success" : "warning",
		);
	};

	// NEW-PRIV-006: helper to look up the consent flag name for a
	// provider.  Avoids repeating the ternary in 4 places in the JSX.
	const consentKeyFor = (provider: string): keyof VoiceTyperConfig => {
		if (provider === "openai") return "cloud_openai_consent";
		if (provider === "groq") return "cloud_groq_consent";
		return "cloud_deepgram_consent";
	};

	const testConnection = async (provider: string) => {
		const key = apiKeys[provider] ?? "";
		if (!key) {
			setTestResults((prev) => ({
				...prev,
				[provider]: { message: t("models.test.needApiKey"), status: "info" },
			}));
			return;
		}
		// Save the key first, then try a lightweight test.
		try {
			await saveApiKey(provider);
			// OpenAI has a lightweight models list endpoint we can use
			// to verify the key without transcribing audio.
			if (provider === "openai") {
				const resp = await fetch("https://api.openai.com/v1/models", {
					headers: { Authorization: `Bearer ${key}` },
				});
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
			} else if (provider === "groq") {
				const resp = await fetch("https://api.groq.com/openai/v1/models", {
					headers: { Authorization: `Bearer ${key}` },
				});
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
			} else if (provider === "deepgram") {
				const resp = await fetch("https://api.deepgram.com/v1/projects", {
					headers: { Authorization: `Token ${key}` },
				});
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
			} else {
				setTestResults((prev) => ({
					...prev,
					[provider]: {
						message: t("models.test.endpointUnavailable"),
						status: "info",
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
	};

	const runBenchmark = async () => {
		// DEAD-021-025: previously this returned a hardcoded "~2.3s".
		// We now show an honest "not implemented" message.
		setBenchmarkResult(t("models.benchmarkNotImplemented"));
	};

	const handleTogglePause = async () => {
		// Optimistically toggle isPaused so the button updates instantly.
		// The backend will confirm via the download_progress push event.
		setIsPaused((prev) => !prev);
		try {
			if (isPaused) {
				await call("resume_model_download");
			} else {
				await call("pause_model_download");
			}
		} catch (err) {
			// Revert the optimistic toggle on failure.
			setIsPaused((prev) => !prev);
			showSnack(
				isPaused
					? t("models.snack.resumeFailed", {
							error: formatErrorMessage(err),
						})
					: t("models.snack.pauseFailed", {
							error: formatErrorMessage(err),
						}),
				"error",
			);
		}
	};

	const handleGrantConsent = () => setHuggingFaceConsent(true);

	const handleCancelDownload = async () => {
		try {
			await call("cancel_model_download");
			showSnack(t("models.snack.cancelled"), "warning");
		} catch (err) {
			showSnack(
				t("models.snack.cancelFailed", {
					error: formatErrorMessage(err),
				}),
				"error",
			);
		}
	};

	const getStatusBadge = (
		model: ModelInfo,
	): {
		label: string;
		bg: string;
		color: string;
	} | null => {
		// "Active" and "Downloaded" badges removed per user request — only
		// show the "Deps Required" badge when a model has missing deps.
		if (!model.depsOk)
			return {
				label: t("models.status.depsRequired"),
				bg: "color-mix(in srgb, #f59e0b 15%, transparent)",
				color: "#f59e0b",
			};
		return null;
	};

	// MODEL-IMPORT: state and handler for the Import Model button.
	// Must be declared BEFORE the early return guard to avoid violating
	// React's Rules of Hooks (hook count must be consistent across renders).
	const [isImporting, setIsImporting] = useState(false);

	if (!_cachedConfig && !config) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner />
			</div>
		);
	}

	const handleImportModel = async () => {
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
				// Reload model status to reflect newly imported models
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
	};

	const modelFamilies = groupModelsByFamily(models);
	return (
		<>
			{/* Fixed tab bar at the top */}

			{/* Full-width sticky bar with background; inner wrapper centres the tab control */}
			<div className="sticky left-0 right-0 top-0 z-50 ">
				<div className="mx-auto w-full max-w-2xl px-6 py-1.5">
					<SegmentedControl
						variant="tabs"
						options={tabOptions}
						value={activeTab}
						onChange={(v) => setActiveTab(v as "local" | "cloud")}
						ariaLabel={t("models.title")}
						indicatorClassName="bg-(--bg) border border-border/75"
						labelClassName="flex-1 text-center"
						className="bg-(--bg-subtle) rounded-lg w-full"
					/>
				</div>
			</div>
			<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-[156px] pb-6">
				<PageHeading
					title={t("models.title")}
					description={t("models.subtitle")}
				>
					<Button
						variant="outline"
						size="sm"
						onClick={handleImportModel}
						disabled={isImporting}
						title={t("models.import.title")}
						className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
						aria-label={t("models.import.title")}
					>
						<HugeiconsIcon
							icon={Folder02Icon}
							strokeWidth={2}
							className="h-4 w-4"
						/>
						{isImporting
							? t("models.import.importing")
							: t("models.import.importModel")}
					</Button>
				</PageHeading>

				<div className="space-y-6">
					{activeTab === "local" ? (
						<>
							{/* NEW-PRIV-005: HuggingFace consent banner */}
							{config && !config.huggingface_consent && (
								<div className="rounded-lg border border-amber-400/40 bg-amber-400/5 p-4">
									<div className="flex items-start gap-3">
										<HugeiconsIcon
											icon={Alert02Icon}
											strokeWidth={2}
											className="mt-0.5 h-5 w-5 shrink-0 text-amber-500"
										/>
										<div className="flex-1">
											<h3 className="text-sm font-semibold text-(--text-primary)">
												{t("models.hfConsent.title")}
											</h3>
											<p className="mt-1 text-xs leading-relaxed text-(--text-muted)">
												{t("models.hfConsent.description")}
											</p>
											<div className="mt-3 flex items-center gap-3">
												<Button
													variant="default"
													size="sm"
													onClick={handleGrantConsent}
													aria-label={t("models.hfConsent.grantAria")}
												>
													{t("models.hfConsent.grant")}
												</Button>
												<span className="text-xs text-(--text-muted)">
													{t("models.hfConsent.blockedHint")}
												</span>
											</div>
										</div>
									</div>
								</div>
							)}

							{/* Model Cards — grouped by family using shadcn Accordion */}
							{/* MODELS-COLLAPSE: only the family containing the active model is
							    open by default. All other families start collapsed, reducing
							    vertical noise when many model variants are installed.
							    The Accordion's internal state persists during the page visit
							    (collapsing/expanding via clicks), but resets on page re-mount. */}
							<Accordion
								type="multiple"
								value={accordionValue}
								onValueChange={setAccordionValue}
								className="rounded-lg border border-border bg-(--bg-subtle)"
							>
								{modelFamilies.map((family) => (
									<AccordionItem
										key={family.id}
										value={family.id}
										className="border-border data-open:bg-transparent"
									>
										<AccordionTrigger className="px-3.5 py-2.5 text-sm font-semibold text-(--text-primary) hover:no-underline hover:bg-black/2 dark:hover:bg-white/5 data-open:bg-transparent">
											{family.name}
										</AccordionTrigger>
										<AccordionContent className="px-0 pb-0 divide-y divide-border">
											{family.variants.map((model) => {
												const badge = getStatusBadge(model);
												const meta = modelCatalog[model.name];
												const isSelectingThis = selectingModel === model.name;
												const handleSelectThis = () => selectModel(model);
												const handleDeleteThis = () =>
													requestDeleteModel(model);
												const handleDownloadThis = () => downloadModel(model);
												const downloadingThis = downloadingModel === model.name;

												return (
													<Fragment key={model.name}>
														<div className="flex items-center gap-3 px-0 py-2.5">
															<div className="flex-1 min-w-0">
																<div className="flex items-center gap-2">
																	<h4 className="text-sm font-semibold text-(--text-primary) truncate">
																		{DISPLAY_NAMES[model.name] ?? model.name}
																	</h4>
																	{badge && (
																		<output
																			className="shrink-0 inline-flex items-center rounded-md px-2 py-0.5 text-[9px] font-semibold border"
																			aria-live="polite"
																			style={{
																				backgroundColor: badge.bg,
																				color: badge.color,
																				borderColor: `${badge.color}40`,
																			}}
																		>
																			{badge.label}
																		</output>
																	)}
																</div>
																<p className="text-xs text-(--text-muted) mt-0.5">
																	{model.name === "parakeet"
																		? t("models.card.parakeetLabel")
																		: ""}
																	{t("models.card.size", {
																		size: formatModelSize(model.size),
																	})}
																	{meta && (
																		<span className="text-(--text-muted)">
																			{"  ·  "}{" "}
																			{t("models.card.vram", {
																				vram: formatVram(meta.required_vram_mb),
																			})}
																			{"  ·  "}
																			{meta.multilingual
																				? t("models.card.multilingual")
																				: t("models.card.englishOnly")}
																			{"  ·  "}
																			{t("models.card.speedSuffix", {
																				rating: meta.speed_rating,
																			})}
																			{meta.is_distilled
																				? t("models.card.distilled")
																				: ""}
																		</span>
																	)}
																</p>
															</div>
															<div className="flex items-center gap-2 shrink-0">
																{/* State-aware button */}
																{model.isActive ? (
																	<>
																		<Button
																			variant="secondary"
																			size="sm"
																			className="gap-1 cursor-default opacity-60"
																			disabled
																			aria-label={t("models.card.activeAria", {
																				name: model.name,
																			})}
																		>
																			<HugeiconsIcon
																				icon={Tick02Icon}
																				strokeWidth={2}
																				className="h-4 w-4"
																			/>
																			{t("models.active")}
																		</Button>
																		{model.downloaded &&
																			!model.alwaysAvailable && (
																				<Button
																					variant="ghost"
																					size="icon-xs"
																					onClick={handleDeleteThis}
																					className="text-(--text-muted) hover:text-destructive"
																					aria-label={t(
																						"models.card.deleteAria",
																						{
																							name: model.name,
																						},
																					)}
																					title={t("models.card.deleteAria", {
																						name: model.name,
																					})}
																				>
																					<HugeiconsIcon
																						icon={Delete01Icon}
																						strokeWidth={2.5}
																						className="h-4 w-4"
																					/>
																				</Button>
																			)}
																	</>
																) : !model.downloaded &&
																	!model.alwaysAvailable ? (
																	<Button
																		variant="outline"
																		size="sm"
																		className="gap-1"
																		onClick={handleDownloadThis}
																		disabled={downloadingModel !== null}
																		aria-label={t("models.card.downloadAria", {
																			name: model.name,
																		})}
																	>
																		<HugeiconsIcon
																			icon={Download01Icon}
																			strokeWidth={2}
																			className="h-4 w-4"
																		/>
																		{downloadingThis
																			? t("models.downloading")
																			: t("models.downloadModel")}
																	</Button>
																) : (
																	<>
																		<Button
																			variant={
																				isSelectingThis
																					? "secondary"
																					: "outline"
																			}
																			size="sm"
																			className="gap-1"
																			onClick={handleSelectThis}
																			disabled={isSelectingThis}
																			aria-label={t("models.card.selectAria", {
																				name: model.name,
																			})}
																		>
																			{" "}
																			<HugeiconsIcon
																				icon={PlayIcon}
																				strokeWidth={2}
																				className={cn(
																					"h-4 w-4",
																					isSelectingThis && "animate-spin",
																				)}
																			/>
																			{isSelectingThis
																				? t("models.selecting")
																				: t("models.select")}
																		</Button>
																		{model.downloaded &&
																			!model.alwaysAvailable && (
																				<Button
																					variant="ghost"
																					size="icon-xs"
																					onClick={handleDeleteThis}
																					className="text-(--text-muted) hover:text-destructive"
																					aria-label={t(
																						"models.card.deleteAria",
																						{
																							name: model.name,
																						},
																					)}
																					title={t("models.card.deleteAria", {
																						name: model.name,
																					})}
																				>
																					<HugeiconsIcon
																						icon={Delete01Icon}
																						strokeWidth={2.5}
																						className="h-4 w-4"
																					/>
																				</Button>
																			)}
																	</>
																)}{" "}
															</div>
														</div>
													</Fragment>
												);
											})}
										</AccordionContent>
									</AccordionItem>
								))}
							</Accordion>

							{/* Model Benchmark */}
							<div className="rounded-xl border border-border bg-(--bg-subtle) p-6">
								<h2 className="font-sans text-lg font-semibold text-(--text-primary)">
									{t("models.benchmark.title")}
								</h2>
								<p className="text-sm text-(--text-muted) mt-0.5 mb-4">
									{t("models.benchmark.description")}
								</p>
								<Button
									variant="default"
									className="gap-2"
									onClick={runBenchmark}
									disabled={isBenchmarking}
									aria-label={t("models.benchmark.runAria")}
								>
									<HugeiconsIcon
										icon={ZapIcon}
										strokeWidth={2}
										className="h-4 w-4"
									/>
									{isBenchmarking
										? t("models.benchmark.running")
										: t("models.benchmark.run")}
								</Button>
								{benchmarkResult && (
									<p className="text-sm text-(--text-muted) mt-3">
										{benchmarkResult}
									</p>
								)}
							</div>
						</>
					) : (
						<>
							{/* Cloud ASR Providers */}
							<div className="space-y-4">
								<h2 className="font-sans text-lg font-semibold text-(--text-primary)">
									{t("models.cloudProviders")}
								</h2>
								<p className="text-sm text-(--text-muted) -mt-3">
									{t("models.cloudProvidersDescription")}
								</p>

								<div className="space-y-4">
									{CLOUD_PROVIDERS.map((provider) => {
										const handleSaveApiKey = () => saveApiKey(provider.key);
										const handleTestConnection = () =>
											testConnection(provider.key);
										const handleApiKeyInput = (
											e: React.ChangeEvent<HTMLInputElement>,
										) =>
											setApiKeys((prev) => ({
												...prev,
												[provider.key]: e.target.value,
											}));
										const handleConsentChange = (checked: boolean) =>
											setCloudConsent(provider.key, checked);
										return (
											<div
												key={provider.key}
												className="rounded-xl border border-border bg-(--bg-subtle) p-6"
											>
												<div className="flex items-center gap-2.5 mb-4">
													<HugeiconsIcon
														icon={Shield01Icon}
														strokeWidth={2}
														className="h-4 w-4 text-accent"
													/>
													<h3 className="text-base font-semibold text-(--text-primary)">
														{t("models.cloud.providerSettings", {
															provider: getProviderLabel(provider.key),
														})}
													</h3>
												</div>
												<div className="mb-4">
													<label
														htmlFor="api-key-input"
														className="text-sm font-medium text-(--text-primary) mb-1.5 block"
													>
														{t("models.cloud.apiKey")}
													</label>
													<Input
														id="api-key-input"
														type="password"
														value={apiKeys[provider.key] ?? ""}
														onChange={handleApiKeyInput}
														placeholder={t("models.apiKeyPlaceholder")}
														className="w-full max-w-md"
													/>
												</div>
												<div className="flex items-center gap-3">
													<Button
														variant="default"
														size="sm"
														onClick={handleSaveApiKey}
														aria-label={t("models.cloud.saveKeyAria", {
															provider: getProviderLabel(provider.key),
														})}
													>
														{t("models.cloud.saveKey")}
													</Button>
													<Button
														variant="outline"
														size="sm"
														className="gap-2"
														onClick={handleTestConnection}
														aria-label={t("models.cloud.testConnectionAria", {
															provider: getProviderLabel(provider.key),
														})}
													>
														<HugeiconsIcon
															icon={SparklesIcon}
															strokeWidth={2}
															className="h-4 w-4"
														/>
														{t("models.cloud.testConnection")}
													</Button>
													{testResults[provider.key] && (
														<span
															className={cn(
																"text-xs",
																testResults[provider.key].status === "success"
																	? "text-primary"
																	: testResults[provider.key].status ===
																			"failure"
																		? "text-destructive"
																		: "text-[(--text-muted)]",
															)}
														>
															{testResults[provider.key].message}
														</span>
													)}
												</div>{" "}
												{(apiKeys[provider.key] ||
													config?.[consentKeyFor(provider.key)]) && (
													<div className="mt-4 rounded-lg border border-border bg-(--bg) p-4">
														<div className="flex items-start justify-between gap-4">
															<div className="flex-1">
																<h4 className="text-sm font-semibold text-(--text-primary)">
																	{t("models.cloud.consentTitle")}
																</h4>
																<p className="mt-1 text-xs leading-relaxed text-(--text-muted)">
																	{t("models.cloud.consentDescription", {
																		provider: getProviderLabel(provider.key),
																	})}
																</p>
																<p className="mt-2 text-xs text-(--text-muted)">
																	{t("models.cloud.statusLabel")}{" "}
																	{config?.[consentKeyFor(provider.key)] ? (
																		<span className="font-medium text-emerald-500">
																			{t("models.cloud.consentGrantedStatus")}
																		</span>
																	) : (
																		<span className="font-medium text-amber-500">
																			{t(
																				"models.cloud.consentNotGrantedStatus",
																			)}
																		</span>
																	)}
																</p>
															</div>
															<Switch
																checked={
																	(config?.[consentKeyFor(provider.key)] as
																		| boolean
																		| undefined) ?? false
																}
																onCheckedChange={handleConsentChange}
																aria-label={t("models.cloud.consentAria", {
																	provider: getProviderLabel(provider.key),
																})}
															/>
														</div>
													</div>
												)}
											</div>
										);
									})}
								</div>
							</div>

							{/* Model Benchmark */}
							<div className="rounded-xl border border-border bg-(--bg-subtle) p-6">
								<h2 className="font-sans text-lg font-semibold text-(--text-primary)">
									{t("models.benchmark.title")}
								</h2>
								<p className="text-sm text-(--text-muted) mt-0.5 mb-4">
									{t("models.benchmark.description")}
								</p>
								<Button
									variant="default"
									className="gap-2"
									onClick={runBenchmark}
									disabled={isBenchmarking}
									aria-label={t("models.benchmark.runAria")}
								>
									<HugeiconsIcon
										icon={ZapIcon}
										strokeWidth={2}
										className="h-4 w-4"
									/>
									{isBenchmarking
										? t("models.benchmark.running")
										: t("models.benchmark.run")}
								</Button>
								{benchmarkResult && (
									<p className="text-sm text-(--text-muted) mt-3">
										{benchmarkResult}
									</p>
								)}
							</div>
						</>
					)}
				</div>

				{/* Snackbar */}
				<Snackbar />

				{/* #7: ConfirmDialog for model deletion */}
				<ConfirmDialog
					open={deleteModelTarget !== null}
					title={t("models.deleteDialog.title")}
					message={t("models.deleteDialog.message", {
						name: deleteModelTarget?.name ?? "",
					})}
					confirmLabel={t("models.delete")}
					onConfirm={confirmDeleteModel}
					onCancel={() => setDeleteModelTarget(null)}
				/>
			</div>
		</>
	);
}
