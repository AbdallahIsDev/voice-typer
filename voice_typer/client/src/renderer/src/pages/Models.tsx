import {
	Alert02Icon,
	Delete01Icon,
	Download01Icon,
	PlayIcon,
	Shield01Icon,
	SparklesIcon,
	Tick02Icon,
	ZapIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useState } from "react";
import ConfirmDialog from "@/components/ConfirmDialog";
import PageHeading from "@/components/PageHeading";
import { Spinner } from "@/components/Spinner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
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

const CLOUD_PROVIDERS = [
	{
		key: "openai",
		label: "OpenAI Whisper API",
		url: "https://api.openai.com/v1/audio/transcriptions",
		model: "whisper-1",
	},
	{
		key: "groq",
		label: "Groq Whisper API",
		url: "https://api.groq.com/openai/v1/audio/transcriptions",
		model: "whisper-large-v3",
	},
	{
		key: "deepgram",
		label: "Deepgram API",
		url: "https://api.deepgram.com/v1/listen",
		model: "nova-2",
	},
] as const;

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

export default function ModelsPage() {
	const { call } = usePython();
	const { showSnack, Snackbar } = useSnackbar();
	const [config, setConfig] = useState<VoiceTyperConfig | null>(_cachedConfig);
	const [models, setModels] = useState<ModelInfo[]>(INITIAL_MODELS);
	const [_initialLoading, setInitialLoading] = useState(true);
	const [downloadProgress, setDownloadProgress] = useState(0);
	const [downloadStatus, setDownloadStatus] = useState("");
	const [isDownloading, setIsDownloading] = useState(false);
	const [benchmarkResult, setBenchmarkResult] = useState("");
	const [isBenchmarking, _setIsBenchmarking] = useState(false);

	// #7: ConfirmDialog state for model deletion
	const [deleteModelTarget, setDeleteModelTarget] = useState<ModelInfo | null>(
		null,
	);

	// Cloud provider API keys
	const [apiKeys, setApiKeys] = useState<Record<string, string>>({});
	const [testResults, setTestResults] = useState<Record<string, string>>({});

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
			} catch (err) {
				console.error("Failed to get model status:", err);
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
	usePythonEvent(
		"download_progress",
		useCallback((data: Record<string, unknown> | undefined) => {
			if (data && typeof data.progress === "number") {
				setDownloadProgress(data.progress);
			}
			if (data && typeof data.status === "string") {
				setDownloadStatus(data.status);
			}
		}, []),
	);

	// Reset download state when the component unmounts or user navigates away
	useEffect(() => {
		return () => {
			setIsDownloading(false);
			setDownloadProgress(0);
			setDownloadStatus("");
		};
	}, []);

	// Reset progress when a download starts / finishes
	const resetProgress = useCallback(() => {
		setDownloadProgress(0);
		setDownloadStatus("");
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
			showSnack(
				"Dependencies required for Parakeet. Download first.",
				"warning",
			);
			return;
		}
		if (!model.downloaded && !model.alwaysAvailable) {
			showSnack(
				`Model "${model.name}" not downloaded yet. Download it first.`,
				"warning",
			);
			return;
		}

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
		showSnack(`Using model: ${model.name}`, "success");
	};

	const downloadModel = async (model: ModelInfo) => {
		// UX-005: Real download via IPC — calls download_model route which
		// loads the model into the HF cache (triggers HuggingFace download).
		// The backend pushes `download_progress` events during the download
		// (see usePythonEvent subscription above); we just initiate the call
		// and update model state on success.
		setIsDownloading(true);
		resetProgress();
		try {
			const result = await call<{
				success: boolean;
				error?: string;
				message?: string;
			}>("download_model", { model: model.name });
			if (result.success) {
				setModels((prev) =>
					prev.map((m) =>
						m.name === model.name ? { ...m, downloaded: true } : m,
					),
				);
				showSnack(
					result.message || `${model.name} downloaded successfully`,
					"success",
				);
			} else {
				showSnack(result.error || `Failed to download ${model.name}`, "error");
			}
		} catch (err) {
			showSnack(`Download failed: ${err}`, "error");
		} finally {
			setIsDownloading(false);
			// Keep the final progress/status visible briefly so the user
			// sees the "complete" message; the next downloadModel() call
			// will reset via resetProgress().
		}
	};

	// #7: ConfirmDialog — ask before deleting a model
	const requestDeleteModel = (model: ModelInfo) => {
		if (model.isActive) {
			showSnack(
				"Cannot delete the active model. Switch to another model first.",
				"warning",
			);
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
				showSnack(`Deleted: ${deleteModelTarget.name}`, "warning");
			} else {
				showSnack(result?.message ?? "Delete failed", "error");
			}
		} catch (e) {
			showSnack(`Delete failed: ${e}`, "error");
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
			`${CLOUD_PROVIDERS.find((p) => p.key === provider)?.label} API key saved`,
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
				? `Consent granted for ${CLOUD_PROVIDERS.find((p) => p.key === provider)?.label} — audio will be sent to this provider.`
				: `Consent revoked for ${CLOUD_PROVIDERS.find((p) => p.key === provider)?.label} — audio will NOT be sent.`,
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
			granted
				? "Consent granted — model downloads from HuggingFace will proceed."
				: "Consent revoked — model downloads from HuggingFace are blocked.",
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
				[provider]: "Please enter an API key first",
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
						[provider]: "Connection successful — API key is valid.",
					}));
				} else {
					setTestResults((prev) => ({
						...prev,
						[provider]: `Connection failed: ${resp.status} ${resp.statusText}`,
					}));
				}
			} else if (provider === "groq") {
				const resp = await fetch("https://api.groq.com/openai/v1/models", {
					headers: { Authorization: `Bearer ${key}` },
				});
				if (resp.ok) {
					setTestResults((prev) => ({
						...prev,
						[provider]: "Connection successful — API key is valid.",
					}));
				} else {
					setTestResults((prev) => ({
						...prev,
						[provider]: `Connection failed: ${resp.status} ${resp.statusText}`,
					}));
				}
			} else if (provider === "deepgram") {
				const resp = await fetch("https://api.deepgram.com/v1/projects", {
					headers: { Authorization: `Token ${key}` },
				});
				if (resp.ok) {
					setTestResults((prev) => ({
						...prev,
						[provider]: "Connection successful — API key is valid.",
					}));
				} else {
					setTestResults((prev) => ({
						...prev,
						[provider]: `Connection failed: ${resp.status} ${resp.statusText}`,
					}));
				}
			} else {
				setTestResults((prev) => ({
					...prev,
					[provider]:
						"API key saved — test endpoint not available for this provider.",
				}));
			}
		} catch (err) {
			setTestResults((prev) => ({
				...prev,
				[provider]: `Connection test failed: ${err}`,
			}));
		}
	};

	const runBenchmark = async () => {
		// DEAD-021-025: previously this returned a hardcoded "~2.3s".
		// We now show an honest "not implemented" message.
		setBenchmarkResult("Benchmark not yet implemented.");
	};

	const allDownloaded = models.every((m) => m.downloaded);

	const getStatusBadge = (model: ModelInfo) => {
		// UX-009: distinct colors per state so users can tell at a glance
		// which models are active, downloaded, need deps, or available.
		if (model.isActive)
			return {
				label: "Active",
				bg: "color-mix(in srgb, #22c55e 15%, transparent)",
				color: "#22c55e",
			};
		if (model.downloaded)
			return {
				label: "Downloaded",
				bg: "color-mix(in srgb, #3b82f6 15%, transparent)",
				color: "#3b82f6",
			};
		if (!model.depsOk)
			return {
				label: "Dependencies required",
				bg: "color-mix(in srgb, #f59e0b 15%, transparent)",
				color: "#f59e0b",
			};
		return {
			label: "Available",
			bg: "color-mix(in srgb, var(--text-muted) 12%, transparent)",
			color: "var(--text-muted)",
		};
	};

	if (!_cachedConfig && !config) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner />
			</div>
		);
	}

	return (
		<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
			<PageHeading
				title="Models"
				description="Configure your speech-to-text engines"
			>
				<Button
					variant="outline"
					size="sm"
					onClick={async () => {
						// Download all non-downloaded models sequentially
						const toDownload = models.filter(
							(m) => !m.downloaded && !m.alwaysAvailable,
						);
						if (toDownload.length === 0) return;
						for (const m of toDownload) {
							await downloadModel(m);
						}
					}}
					disabled={isDownloading || allDownloaded}
					title={
						allDownloaded
							? "All models already downloaded"
							: "Download all models"
					}
					// FIX: muted text/icon by default, white on hover —
					// matches the outline-button style used across other
					// page headings (Templates add, Vocabulary add, etc.).
					className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
					aria-label={
						allDownloaded ? "All models downloaded" : "Download all models"
					}
				>
					<HugeiconsIcon
						icon={Download01Icon}
						strokeWidth={2}
						className="h-4 w-4"
					/>
					{isDownloading
						? "Downloading..."
						: allDownloaded
							? "All Downloaded"
							: "Download All"}
				</Button>
			</PageHeading>

			<div className="space-y-6">
				{/* NEW-PRIV-005: HuggingFace consent banner.  Shown when the
            user has at least one uncached model AND consent hasn't
            been granted yet.  Explains why consent is needed (IP
            exposure to a US-headquartered third party) and provides
            a one-click grant button. */}
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
									HuggingFace download consent required
								</h3>
								<p className="mt-1 text-xs leading-relaxed text-(--text-muted)">
									Local Whisper models (tiny.en, small.en, medium.en) download
									weights from <strong>huggingface.co</strong> on first use.
									This download reveals your IP address to a US-headquartered
									third party (Hugging Face, Inc.). Under GDPR Art. 13/44
									(Schrems II), we need your explicit consent before initiating
									the download. Audio itself is never sent — only the model
									weights are fetched.
								</p>
								<div className="mt-3 flex items-center gap-3">
									<Button
										variant="default"
										size="sm"
										onClick={() => setHuggingFaceConsent(true)}
										aria-label="Grant HuggingFace download consent"
									>
										Grant consent
									</Button>
									<span className="text-xs text-(--text-muted)">
										Model downloads are blocked until you grant consent.
									</span>
								</div>
							</div>
						</div>
					</div>
				)}

				{/* Download Progress */}
				{isDownloading && (
					<div className="space-y-2">
						<div className="h-1.5 w-full rounded-full bg-border overflow-hidden">
							<div
								className="h-full rounded-full bg-accent transition-all duration-300"
								style={{ width: `${downloadProgress}%` }}
							/>
						</div>
						<div className="flex items-center justify-between gap-3">
							<p className="text-xs text-(--text-muted)">{downloadStatus}</p>
							{/* NEW-PRIV-011: Cancel button for in-progress downloads. */}
							<Button
								variant="outline"
								size="sm"
								onClick={async () => {
									try {
										await call("cancel_model_download");
										showSnack(
											"Download cancelled. Partial files will be reused on retry.",
											"warning",
										);
									} catch (err) {
										showSnack(`Failed to cancel: ${err}`, "error");
									}
								}}
								aria-label="Cancel model download"
								className="h-7 px-3 text-xs"
							>
								Cancel
							</Button>
						</div>
					</div>
				)}

				{/* Model Cards */}
				<div className="rounded-lg border border-border bg-(--bg-subtle) divide-y divide-border">
					{models.map((model) => {
						const badge = getStatusBadge(model);
						return (
							<div
								key={model.name}
								className="flex items-center gap-3 px-3.5 py-2.5"
							>
								<div className="flex-1 min-w-0">
									<div className="flex items-center gap-2">
										<h3 className="text-sm font-semibold text-(--text-primary) truncate">
											{model.name}
										</h3>
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
									</div>
									<p className="text-xs text-(--text-muted) mt-0.5">
										{model.name === "parakeet"
											? "NVIDIA Parakeet TDT v3  ·  "
											: ""}
										Size: {model.size}
									</p>
								</div>
								<div className="flex items-center gap-2 shrink-0">
									{model.name === "parakeet" && !model.depsOk ? (
										<Button
											variant="outline"
											size="sm"
											className="gap-1"
											onClick={() => downloadModel(model)}
											disabled={isDownloading}
											aria-label={`Download dependencies for ${model.name}`}
										>
											<HugeiconsIcon
												icon={Download01Icon}
												strokeWidth={2}
												className="h-4 w-4"
											/>
											Download Deps
										</Button>
									) : (
										<Button
											variant={model.isActive ? "secondary" : "outline"}
											size="sm"
											className="gap-1"
											onClick={() => selectModel(model)}
											disabled={
												model.isActive ||
												(!model.downloaded && !model.alwaysAvailable)
											}
											aria-label={
												model.isActive
													? `Active: ${model.name}`
													: `Use ${model.name}`
											}
										>
											<HugeiconsIcon
												icon={model.isActive ? Tick02Icon : PlayIcon}
												strokeWidth={2}
												className="h-4 w-4"
											/>
											{model.isActive ? "Active" : "Use"}
										</Button>
									)}
									{!model.alwaysAvailable && (
										<Button
											variant="ghost"
											size="icon-xs"
											onClick={() => requestDeleteModel(model)}
											disabled={model.isActive}
											className="text-(--text-muted) hover:text-destructive"
											aria-label={`Delete ${model.name}`}
											title={`Delete ${model.name}`}
										>
											<HugeiconsIcon
												icon={Delete01Icon}
												strokeWidth={2.5}
												className="h-4 w-4"
											/>
										</Button>
									)}
								</div>
							</div>
						);
					})}
				</div>

				{/* Cloud ASR Providers */}
				<div className="space-y-4">
					<h2 className="font-sans text-lg font-semibold text-(--text-primary)">
						Cloud ASR Providers
					</h2>
					<p className="text-sm text-(--text-muted) -mt-3">
						Configure cloud-based transcription services
					</p>

					<div className="space-y-4">
						{CLOUD_PROVIDERS.map((provider) => (
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
										{provider.label} Settings
									</h3>
								</div>
								<div className="mb-4">
									<label
										htmlFor="api-key-input"
										className="text-sm font-medium text-(--text-primary) mb-1.5 block"
									>
										API Key
									</label>
									<Input
										id="api-key-input"
										type="password"
										value={apiKeys[provider.key] ?? ""}
										onChange={(e) =>
											setApiKeys((prev) => ({
												...prev,
												[provider.key]: e.target.value,
											}))
										}
										placeholder="Enter your API key"
										className="w-full max-w-md"
									/>
								</div>
								<div className="flex items-center gap-3">
									<Button
										variant="default"
										size="sm"
										onClick={() => saveApiKey(provider.key)}
										aria-label={`Save ${provider.label} API key`}
									>
										Save Key
									</Button>
									<Button
										variant="outline"
										size="sm"
										className="gap-2"
										onClick={() => testConnection(provider.key)}
										aria-label={`Test ${provider.label} connection`}
									>
										<HugeiconsIcon
											icon={SparklesIcon}
											strokeWidth={2}
											className="h-4 w-4"
										/>
										Test Connection
									</Button>
									{testResults[provider.key] && (
										<span
											className={cn(
												"text-xs",
												testResults[provider.key].includes("successful")
													? "text-primary"
													: testResults[provider.key].includes("Failed") ||
															testResults[provider.key].includes("error")
														? "text-destructive"
														: "text-[(--text-muted)]",
											)}
										>
											{testResults[provider.key]}
										</span>
									)}
								</div>{" "}
								{/* NEW-PRIV-006: per-provider consent toggle.  The
                    backend CloudEngine refuses to transcribe without
                    this — without this UI, a user who pastes a key
                    would hit ConsentRequiredError at dictation time
                    with no in-app explanation.  Shown only when an
                    API key is present (no point without one). */}
								{(apiKeys[provider.key] ||
									config?.[consentKeyFor(provider.key)]) && (
									<div className="mt-4 rounded-lg border border-border bg-(--bg) p-4">
										<div className="flex items-start justify-between gap-4">
											<div className="flex-1">
												<h4 className="text-sm font-semibold text-(--text-primary)">
													Audio transmission consent
												</h4>
												<p className="mt-1 text-xs leading-relaxed text-(--text-muted)">
													When this provider is selected as the active ASR
													backend, your{" "}
													<strong>audio recordings will be sent</strong> to{" "}
													{provider.label} for transcription. The provider's
													privacy policy applies to the audio sent. Voice Typer
													never enables cloud ASR without your explicit consent
													— even if an API key is configured.
												</p>
												<p className="mt-2 text-xs text-(--text-muted)">
													Status:{" "}
													{config?.[consentKeyFor(provider.key)] ? (
														<span className="font-medium text-emerald-500">
															Consent granted — audio will be sent when this
															provider is active.
														</span>
													) : (
														<span className="font-medium text-amber-500">
															Consent not granted — this provider will refuse to
															transcribe.
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
												onCheckedChange={(checked) =>
													setCloudConsent(provider.key, checked)
												}
												aria-label={`Grant audio transmission consent for ${provider.label}`}
											/>
										</div>
									</div>
								)}
							</div>
						))}
					</div>
				</div>

				{/* Model Benchmark */}
				<div className="rounded-xl border border-border bg-(--bg-subtle) p-6">
					<h2 className="font-sans text-lg font-semibold text-(--text-primary)">
						Model Benchmark
					</h2>
					<p className="text-sm text-(--text-muted) mt-0.5 mb-4">
						Compare model performance on your system
					</p>
					<Button
						variant="default"
						className="gap-2"
						onClick={runBenchmark}
						disabled={isBenchmarking}
						aria-label="Run model benchmark"
					>
						<HugeiconsIcon icon={ZapIcon} strokeWidth={2} className="h-4 w-4" />
						{isBenchmarking ? "Running..." : "Run Benchmark"}
					</Button>
					{benchmarkResult && (
						<p className="text-sm text-(--text-muted) mt-3">
							{benchmarkResult}
						</p>
					)}
				</div>
			</div>

			{/* Snackbar */}
			<Snackbar />

			{/* #7: ConfirmDialog for model deletion */}
			<ConfirmDialog
				open={deleteModelTarget !== null}
				title="Delete Model"
				message={`Are you sure you want to delete "${deleteModelTarget?.name ?? ""}"? This action cannot be undone.`}
				confirmLabel="Delete"
				onConfirm={confirmDeleteModel}
				onCancel={() => setDeleteModelTarget(null)}
			/>
		</div>
	);
}
