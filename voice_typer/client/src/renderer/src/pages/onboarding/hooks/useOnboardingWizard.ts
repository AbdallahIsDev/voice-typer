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
import { HOTKEY_DEFAULT, MODEL_DEFAULT } from "../lib/constants";
import type { MicrophoneOption, ModelOption, StepInfo } from "../lib/types";

// The six consent flags surfaced on the consolidated Consent step
// (voice biometric, HuggingFace, OpenAI / Groq / Deepgram cloud ASR,
// LLM polish). Module-level so useCallbacks/effects can list it as a
// stable dep without re-creating the array every render.
const CONSENT_FIELDS = [
	"voice_biometric_consent",
	"huggingface_consent",
	"cloud_openai_consent",
	"cloud_groq_consent",
	"cloud_deepgram_consent",
	"llm_polish_consent",
] as const;

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
	// Consent step: consolidated grant of every consent flag.
	consents: Record<string, boolean>;
	setConsentField: (field: string, value: boolean) => void;
	handleAgreeToAll: () => void;
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

	// Ref mirror of `call` so the init effect depends only on
	// `retryCounter`. Test mocks may return a FRESH call per render — an
	// effect dep on it re-fires init() (onboarding_start/get_config/… →
	// setState → re-render → new call → loop → worker OOM). Same
	// pattern as useVocabulary.ts.
	const callRef = useRef(call);
	useEffect(() => {
		callRef.current = call;
	}, [call]);

	const [loading, setLoading] = useState(true);
	const [initError, setInitError] = useState<string | null>(null);
	const [step, setStep] = useState<StepInfo | null>(null);
	const [retryCounter, setRetryCounter] = useState(0);
	const [submitting, setSubmitting] = useState(false);
	const [applyError, setApplyError] = useState(false);
	const [skipConfirmOpen, setSkipConfirmOpen] = useState(false);

	const [selectedHotkey, setSelectedHotkey] = useState(HOTKEY_DEFAULT);
	const [selectedModel, setSelectedModel] = useState(MODEL_DEFAULT);
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

	// Consent step: the six consent flags shown on the consolidated
	// Consent step (see module-level CONSENT_FIELDS). Keyed by config
	// field; initial state loaded from get_config (so a re-run or a
	// user who already granted via Settings → Privacy sees the real
	// state).
	const [consents, setConsents] = useState<Record<string, boolean>>({});

	// Persist a single consent toggle immediately (mirrors the
	// Done-step consent checkbox contract: optimistic set + revert on
	// persistence failure so the UI never claims a grant that wasn't
	// saved).
	const setConsentField = useCallback(
		(field: string, value: boolean) => {
			setConsents((prev) => ({ ...prev, [field]: value }));
			call("set_config", { [field]: value }).catch((e) => {
				console.error(
					"[renderer:useOnboardingWizard] set_config consent failed:",
					e,
				);
				// Revert on failure so the UI doesn't claim a grant
				// that wasn't persisted.
				setConsents((prev) => ({ ...prev, [field]: !value }));
			});
		},
		[call],
	);

	// Grant every consent at once (single batched set_config, same
	// six fields as the Settings Privacy page's "Agree to All").
	// CONSENT_FIELDS is a module-level const — a stable dep.
	const handleAgreeToAll = useCallback(() => {
		const all = Object.fromEntries(CONSENT_FIELDS.map((f) => [f, true]));
		setConsents((prev) => ({ ...prev, ...all }));
		call("set_config", all).catch((e) => {
			console.error(
				"[renderer:useOnboardingWizard] set_config agree-to-all failed:",
				e,
			);
		});
		// CONSENT_FIELDS is a module-level const — biome treats it
		// as stable and flags listing it as a dep as unnecessary.
	}, [call]);

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
				const started = await callRef.current<StepInfo>("onboarding_start");
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
				// values (HOTKEY_DEFAULT / MODEL_DEFAULT / "") are still
				// used when config.json has no saved value.
				try {
					const cfg = await callRef.current<VoiceTyperConfig>("get_config");
					if (cancelled) return;
					if (cfg) {
						const cfgHotkey = cfg.hotkey ?? HOTKEY_DEFAULT;
						if (cfgHotkey) setSelectedHotkey(cfgHotkey);
						const cfgModel = cfg.model_size ?? MODEL_DEFAULT;
						if (cfgModel) setSelectedModel(cfgModel);
						setSelectedMic(cfg.microphone ?? "");
						setHfConsent(cfg.huggingface_consent === true);
						const cfgConsent = cfg.cloud_openai_consent === true;
						setCloudConsent(cfgConsent);
						// Pre-fill the consolidated consent step from the
						// saved config (re-run / already-granted users).
						const savedConsents: Record<string, boolean> = {};
						for (const f of CONSENT_FIELDS) {
							// `CONSENT_FIELDS` is `as const` — every literal is a
							// real VoiceTyperConfig boolean field, so index it
							// directly (no unsafe Record cast).
							savedConsents[f] = cfg[f] === true;
						}
						setConsents(savedConsents);
					}
				} catch (e) {
					console.warn(
						"[renderer:useOnboardingWizard] get_config probe failed:",
						e,
					);
				}
				const mics = await callRef.current<{
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
				const presets = await callRef.current<{ presets: string[] }>(
					"onboarding_get_hotkey_presets",
				);
				if (cancelled) return;
				setHotkeyPresets(presets.presets || []);
				const models = await callRef.current<{ models: ModelOption[] }>(
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
	}, [retryCounter]);

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
			} else if (step?.step_name === "Consent") {
				// The consent toggles persist IMMEDIATELY on toggle
				// (setConsentField), so Continue has nothing new to save
				// — re-persist anyway so a mid-wizard quit after toggling
				// but before Continue still leaves the grants durable
				// (idempotent; mirrors the Model-step pattern).
				const toPersist: Record<string, unknown> = {};
				for (const f of CONSENT_FIELDS) {
					toPersist[f] = consents[f] ?? false;
				}
				await call("set_config", toPersist);
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
		consents,
		showSnack,
	]);

	const handleApply = useCallback(async () => {
		setApplyError(false);
		setSubmitting(true);
		try {
			// Await the backend apply so success is only claimed when the
			// settings actually persisted. The previous fire-and-forget
			// form (`void call(...).catch(...)`) showed the success snack
			// and navigated away even when `onboarding_apply` rejected,
			// silently skipping setup.
			await call("onboarding_apply");
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
				"[renderer:useOnboardingWizard] Failed to apply onboarding:",
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
		consents,
		setConsentField,
		handleAgreeToAll,
	};
}
