import {
	type RefObject,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import type { VoiceTyperConfig } from "@/types/config";
import { HOTKEY_DEFAULT } from "../lib/constants";
import type { MicrophoneOption, ModelOption, StepInfo } from "../lib/types";

export type BackendChoice = "local" | "cloud";

// Map a cloud provider to its allowlisted config fields (mirrors
// `useCloudProviders.ts` — the Models page cloud tab uses the same
// mapping, so the onboarding Cloud panel persists to the SAME config
// keys the user would set there).
function cloudConsentField(provider: string): string {
	if (provider === "openai") return "cloud_openai_consent";
	if (provider === "groq") return "cloud_groq_consent";
	return "cloud_deepgram_consent";
}

function cloudApiKeyField(provider: string): string {
	if (provider === "openai") return "openai_api_key";
	if (provider === "groq") return "groq_api_key";
	return "deepgram_api_key";
}

export interface UseOnboardingWizardResult {
	loading: boolean;
	initError: string | null;
	step: StepInfo | null;
	submitting: boolean;
	applyError: boolean;
	skipConfirmOpen: boolean;
	setSkipConfirmOpen: (v: boolean) => void;
	selectedHotkey: string;
	setSelectedHotkey: (v: string) => void;
	selectedModel: string;
	setSelectedModel: (v: string) => void;
	selectedMic: string;
	setSelectedMic: (v: string) => void;
	hotkeyPresets: string[];
	modelOptions: ModelOption[];
	microphones: MicrophoneOption[];
	headingRef: RefObject<HTMLHeadingElement | null>;
	retryInit: () => void;
	refreshMics: () => void;
	handleNext: () => Promise<void>;
	handleApply: () => Promise<void>;
	handlePrev: () => Promise<void>;
	handleSkip: () => Promise<void>;
	skipOnInitError: () => Promise<void>;
	// Model step: local-vs-cloud choice + explicit download.
	selectedBackend: BackendChoice;
	setSelectedBackend: (v: BackendChoice) => void;
	hfConsent: boolean;
	setHfConsent: (v: boolean) => void;
	downloadingModel: string | null;
	downloadProgress: number;
	downloadFailed: boolean;
	handleDownload: () => Promise<void>;
	// Model step: cloud provider configuration (API key + consent).
	cloudProvider: string;
	setCloudProvider: (v: string) => void;
	cloudApiKey: string;
	setCloudApiKey: (v: string) => void;
	cloudConsent: boolean;
	setCloudConsent: (v: boolean) => void;
}

export function useOnboardingWizard(
	onComplete?: () => void,
): UseOnboardingWizardResult {
	const { call } = usePython();
	const { showSnack } = useSnackbar();

	const [loading, setLoading] = useState(true);
	const [initError, setInitError] = useState<string | null>(null);
	const [step, setStep] = useState<StepInfo | null>(null);
	const [retryCounter, setRetryCounter] = useState(0);
	const [submitting, setSubmitting] = useState(false);
	const [applyError, setApplyError] = useState(false);
	const [skipConfirmOpen, setSkipConfirmOpen] = useState(false);

	const [selectedHotkey, setSelectedHotkey] = useState(HOTKEY_DEFAULT);
	const [selectedModel, setSelectedModel] = useState("small.en");
	const [selectedMic, setSelectedMic] = useState("");
	const [hotkeyPresets, setHotkeyPresets] = useState<string[]>([]);
	const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
	const [microphones, setMicrophones] = useState<MicrophoneOption[]>([]);

	// Model step: the user chooses a local model (downloaded explicitly
	// — the app NEVER auto-downloads) or a cloud transcription API.
	const [selectedBackend, setSelectedBackend] =
		useState<BackendChoice>("local");
	// HuggingFace consent gates the EXPLICIT local-model download
	// (service.download_model requires it). Persisted via set_config.
	const [hfConsent, setHfConsent] = useState(false);
	// Explicit in-wizard download progress.
	const [downloadingModel, setDownloadingModel] = useState<string | null>(null);
	const [downloadProgress, setDownloadProgress] = useState(0);
	const [downloadFailed, setDownloadFailed] = useState(false);
	// Cloud panel: provider API key + consent (persisted via the
	// allowlisted set_config fields, mirroring the Models page).
	const [cloudProvider, setCloudProvider] = useState("openai");
	const [cloudApiKey, setCloudApiKey] = useState("");
	const [cloudConsent, setCloudConsent] = useState(false);

	const headingRef = useRef<HTMLHeadingElement | null>(null);

	const retryInit = useCallback(() => {
		setInitError(null);
		setLoading(true);
		setStep(null);
		setRetryCounter((c) => c + 1);
	}, []);

	useEffect(() => {
		void retryCounter;
		let cancelled = false;
		async function init() {
			try {
				const started = await call<StepInfo>("onboarding_start");
				if (cancelled) return;
				setStep(started);
				// Pre-fill the selections from the saved config on
				// EVERY start — first-run AND resume. A previous
				// version skipped the get_config override when
				// ``step > 0`` (the "resume" heuristic), so a wizard
				// opened mid-way (e.g. after "Re-run setup wizard", or
				// a quit mid-onboarding) showed the renderer defaults
				// instead of the user's saved hotkey/model/mic — and
				// hitting Continue then pushed those defaults back to
				// the backend, clobbering the restored selections.
				// The saved config is the best available source of the
				// user's intent: it reflects what was last applied (or
				// a previous completed run). The backend's in-memory
				// restored selections are not exposed by
				// ``onboarding_start`` regardless of step, so there is
				// no way for the renderer to prefer them. The default
				// values (HOTKEY_DEFAULT / "small.en" / "") are still
				// used when config.json has no saved value.
				try {
					const cfg = await call<VoiceTyperConfig>("get_config");
					if (cancelled) return;
					if (cfg) {
						const cfgHotkey = cfg.hotkey ?? HOTKEY_DEFAULT;
						if (cfgHotkey) setSelectedHotkey(cfgHotkey);
						const cfgModel = cfg.model_size ?? "small.en";
						if (cfgModel) setSelectedModel(cfgModel);
						setSelectedMic(cfg.microphone ?? "");
						setHfConsent(cfg.huggingface_consent === true);
						const cfgConsent = cfg.cloud_openai_consent === true;
						setCloudConsent(cfgConsent);
					}
				} catch (e) {
					console.warn(
						"[renderer:useOnboardingWizard] get_config probe failed:",
						e,
					);
				}
				const mics = await call<{
					microphones: MicrophoneOption[];
				}>("onboarding_get_microphones");
				if (cancelled) return;
				setMicrophones(mics.microphones || []);
				if (mics.microphones?.length > 0) {
					setSelectedMic((prev) => {
						if (prev && mics.microphones.some((m) => m.id === prev)) {
							return prev;
						}
						const defaultMic = mics.microphones.find((m) => m.default === true);
						// noUncheckedIndexedAccess: `mics.microphones[0]` is
						// `MicrophoneInfo | undefined`. The length guard above
						// proves the array is non-empty, but TS still widens;
						// fall back to the existing selection so the state
						// never becomes undefined.
						const fallback = mics.microphones[0];
						return (defaultMic ?? fallback)?.id ?? prev;
					});
				}
				const presets = await call<{ presets: string[] }>(
					"onboarding_get_hotkey_presets",
				);
				if (cancelled) return;
				setHotkeyPresets(presets.presets || []);
				const models = await call<{ models: ModelOption[] }>(
					"onboarding_get_model_options",
				);
				if (cancelled) return;
				setModelOptions(models.models || []);
			} catch (err) {
				if (cancelled) return;
				console.error(
					"[renderer:useOnboardingWizard] Failed to start onboarding:",
					err,
				);
				setInitError(err instanceof Error ? err.message : "Unknown error");
			} finally {
				if (!cancelled) setLoading(false);
			}
		}
		init();
		return () => {
			cancelled = true;
		};
	}, [call, retryCounter]);

	useEffect(() => {
		if (!step) return;
		queueMicrotask(() => {
			headingRef.current?.focus();
		});
	}, [step?.step_name, step]);

	// Explicit in-wizard model download. The app NEVER downloads
	// automatically — the user clicks Download on the Model step (or the
	// Models page). Progress arrives via the ``download_progress`` push
	// event; the promise resolves when the download completes.
	//
	// The HuggingFace consent checkbox on the Model step is the user's
	// explicit opt-in for this download — ``service.download_model``
	// refuses to download without ``huggingface_consent``, so it is
	// persisted here right before the download is started (and again on
	// Continue via ``handleNext`` so the choice survives the wizard even
	// if the user never downloads in-wizard).
	const handleDownload = useCallback(async () => {
		setDownloadFailed(false);
		setDownloadingModel(selectedModel);
		setDownloadProgress(0);
		try {
			if (hfConsent) {
				await call("set_config", { huggingface_consent: true });
			}
			await call("download_model", { model: selectedModel });
		} catch (err) {
			console.error(
				"[renderer:useOnboardingWizard] model download failed:",
				err,
			);
			setDownloadFailed(true);
		} finally {
			setDownloadingModel(null);
		}
	}, [call, selectedModel, hfConsent]);

	usePythonEvent(
		"download_progress",
		useCallback((data: Record<string, unknown> | undefined) => {
			if (!data) return undefined;
			if (typeof data.progress === "number") {
				setDownloadProgress(data.progress);
			}
			return undefined;
		}, []),
	);

	const handleNext = useCallback(async () => {
		setSubmitting(true);
		try {
			if (step?.step_name === "Microphone") {
				await call("onboarding_set_microphone", {
					mic_id: selectedMic || null,
				});
			} else if (step?.step_name === "Hotkey") {
				await call("onboarding_set_hotkey", { hotkey: selectedHotkey });
			} else if (step?.step_name === "Model") {
				await call("onboarding_set_model", { model: selectedModel });
				// Persist the local-vs-cloud choice (Model step).
				await call("onboarding_set_backend", { backend: selectedBackend });
				if (selectedBackend === "local") {
					// Persist the HuggingFace consent checkbox (the user's
					// explicit opt-in for local model downloads) so it
					// survives the wizard even when no download was
					// started on this step. The user can still revoke it
					// later in Settings → Privacy.
					await call("set_config", { huggingface_consent: hfConsent });
				} else if (selectedBackend === "cloud") {
					// Persist the cloud provider API key + consent through
					// the allowlisted set_config fields — mirroring the
					// Models page cloud tab.
					const updates: Record<string, unknown> = {
						[cloudConsentField(cloudProvider)]: cloudConsent,
					};
					if (cloudApiKey.trim()) {
						updates[cloudApiKeyField(cloudProvider)] = cloudApiKey.trim();
					}
					await call("set_config", updates);
				}
			}
			// Note: the Done step does NOT call onboarding_apply via
			// handleNext — the Done-step Continue button is wired to
			// `handleApply` (see Onboarding.tsx's
			// `onClick={isDoneStep ? handleApply : handleNext}`), so a
			// DONE_STEP_NAME branch here would be unreachable dead code.
			// Earlier versions kept a defensive `else if (step_name ===
			// DONE_STEP_NAME) await call("onboarding_apply")` branch
			// that could never fire; removed for clarity.
			const newStep = await call<StepInfo>("onboarding_next_step");
			setStep(newStep);
		} catch (err) {
			console.error(
				"[renderer:useOnboardingWizard] Failed to advance step:",
				err,
			);
			showSnack(t("onboarding.saveFailedSnack"), "error");
		} finally {
			setSubmitting(false);
		}
	}, [
		call,
		step?.step_name,
		selectedMic,
		selectedHotkey,
		selectedModel,
		selectedBackend,
		cloudProvider,
		cloudApiKey,
		cloudConsent,
		hfConsent,
		showSnack,
	]);

	const handleApply = useCallback(async () => {
		setApplyError(false);
		setSubmitting(true);
		try {
			void call("onboarding_apply").catch((err) => {
				console.error(
					"[renderer:useOnboardingWizard] Failed to apply onboarding (async):",
					err,
				);
			});
			// Surface a success toast so the user gets explicit
			// feedback that setup completed (the inline
			// `<output>` spinner disappears as soon as
			// `onComplete()` navigates away). The
			// `setupCompleteSnack` key is localised across all 8
			// locales; the toast persists briefly after navigation
			// so the user sees it on the Home page.
			showSnack(t("onboarding.setupCompleteSnack"), "success");
			if (onComplete) onComplete();
		} catch (err) {
			console.error(
				"[renderer:useOnboardingWizard] Failed to apply onboarding (sync):",
				err,
			);
			setApplyError(true);
			showSnack(t("onboarding.saveFailedSnack"), "error");
		} finally {
			setSubmitting(false);
		}
	}, [call, onComplete, showSnack]);

	const refreshMics = useCallback(() => {
		call<{ microphones: MicrophoneOption[] }>("onboarding_get_microphones")
			.then((mics) => {
				const list = mics?.microphones ?? [];
				setMicrophones(list);
				if (list.length > 0) {
					setSelectedMic((prev) => {
						if (prev && list.some((m) => m.id === prev)) {
							return prev;
						}
						const defaultMic = list.find((m) => m.default === true);
						// See note on the parallel branch above; the length
						// guard proves the array is non-empty, but TS still
						// widens the read under `noUncheckedIndexedAccess`.
						const fallback = list[0];
						return (defaultMic ?? fallback)?.id ?? prev;
					});
				}
			})
			.catch((err) => {
				console.error(
					"[renderer:useOnboardingWizard] Failed to refresh microphones:",
					err,
				);
				showSnack(t("onboarding.saveFailedSnack"), "error");
			});
	}, [call, showSnack]);

	const handlePrev = useCallback(async () => {
		setSubmitting(true);
		try {
			const newStep = await call<StepInfo>("onboarding_prev_step");
			setStep(newStep);
		} catch (err) {
			console.error("[renderer:useOnboardingWizard] Failed to go back:", err);
			showSnack(t("onboarding.saveFailedSnack"), "error");
		} finally {
			setSubmitting(false);
		}
	}, [call, showSnack]);

	const handleSkip = useCallback(async () => {
		setSubmitting(true);
		try {
			await call("onboarding_skip");
			showSnack(t("onboarding.skippedSnack"), "warning");
			if (onComplete) onComplete();
		} catch (err) {
			console.error(
				"[renderer:useOnboardingWizard] Failed to skip onboarding:",
				err,
			);
			showSnack(t("onboarding.saveFailedSnack"), "error");
		} finally {
			setSubmitting(false);
		}
	}, [call, showSnack, onComplete]);

	const skipOnInitError = useCallback(async () => {
		try {
			await call("onboarding_skip");
			showSnack(t("onboarding.skippedSnack"), "warning");
			if (onComplete) onComplete();
		} catch {
			if (onComplete) onComplete();
		}
	}, [call, showSnack, onComplete]);

	return {
		loading,
		initError,
		step,
		submitting,
		applyError,
		skipConfirmOpen,
		setSkipConfirmOpen,
		selectedHotkey,
		setSelectedHotkey,
		selectedModel,
		setSelectedModel,
		selectedMic,
		setSelectedMic,
		hotkeyPresets,
		modelOptions,
		microphones,
		headingRef,
		retryInit,
		refreshMics,
		handleNext,
		handleApply,
		handlePrev,
		handleSkip,
		skipOnInitError,
		selectedBackend,
		setSelectedBackend,
		hfConsent,
		setHfConsent,
		downloadingModel,
		downloadProgress,
		downloadFailed,
		handleDownload,
		cloudProvider,
		setCloudProvider,
		cloudApiKey,
		setCloudApiKey,
		cloudConsent,
		setCloudConsent,
	};
}
