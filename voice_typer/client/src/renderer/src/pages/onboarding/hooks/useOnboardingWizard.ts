import {
	type RefObject,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import type { VoiceTyperConfig } from "@/types/config";
import { DONE_STEP_NAME, HOTKEY_DEFAULT } from "../lib/constants";
import type { MicrophoneOption, ModelOption, StepInfo } from "../lib/types";

export interface UseOnboardingWizardResult {
	loading: boolean;
	initError: string | null;
	step: StepInfo | null;
	submitting: boolean;
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
	handleNext: () => Promise<void>;
	handleApply: () => Promise<void>;
	handlePrev: () => Promise<void>;
	handleSkip: () => Promise<void>;
	skipOnInitError: () => Promise<void>;
}

/**
 * PVT-053 / EC-FIX-18: state + IPC orchestration for the Onboarding wizard,
 * extracted from the Onboarding.tsx monolith. Owns loading/initError/step/
 * retryCounter/submitting/skipConfirmOpen state, the selectedHotkey/
 * selectedModel/selectedMic selection state, the init effect, focus
 * management, and the four navigation handlers (next/apply/prev/skip).
 */
export function useOnboardingWizard(
	onComplete?: () => void,
): UseOnboardingWizardResult {
	const { call } = usePython();
	const { showSnack } = useSnackbar();

	const [loading, setLoading] = useState(true);
	const [initError, setInitError] = useState<string | null>(null);
	const [step, setStep] = useState<StepInfo | null>(null);
	const [retryCounter, setRetryCounter] = useState(0);
	// Fix 11: `submitting` disables nav buttons during IPC calls.
	const [submitting, setSubmitting] = useState(false);
	// Fix 4: skip-confirmation dialog state.
	const [skipConfirmOpen, setSkipConfirmOpen] = useState(false);

	const [selectedHotkey, setSelectedHotkey] = useState(HOTKEY_DEFAULT);
	const [selectedModel, setSelectedModel] = useState("small.en");
	const [selectedMic, setSelectedMic] = useState("");
	const [hotkeyPresets, setHotkeyPresets] = useState<string[]>([]);
	const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
	const [microphones, setMicrophones] = useState<MicrophoneOption[]>([]);

	// Fix 15: shared heading ref — focused on every step change.
	const headingRef = useRef<HTMLHeadingElement | null>(null);

	const retryInit = useCallback(() => {
		setInitError(null);
		setLoading(true);
		setStep(null);
		setRetryCounter((c) => c + 1);
	}, []);

	// ── Init effect ────────────────────────────────────────────────
	useEffect(() => {
		void retryCounter;
		let cancelled = false;
		async function init() {
			try {
				const started = await call<StepInfo>("onboarding_start");
				if (cancelled) return;
				setStep(started);
				try {
					const cfg = await call<VoiceTyperConfig>("get_config");
					if (cancelled) return;
					if (cfg) {
						const cfgHotkey = cfg.hotkey ?? HOTKEY_DEFAULT;
						if (cfgHotkey) setSelectedHotkey(cfgHotkey);
						const cfgModel = cfg.model_size ?? "small.en";
						if (cfgModel) setSelectedModel(cfgModel);
						setSelectedMic(cfg.microphone ?? "");
					}
				} catch (e) {
					/* older backend without get_config */
					console.warn("[useOnboardingWizard] get_config probe failed:", e);
				}
				const mics = await call<{
					microphones: MicrophoneOption[];
				}>("onboarding_get_microphones");
				if (cancelled) return;
				setMicrophones(mics.microphones || []);
				// S2-CR-39: prefer the OS default input device
				// (the backend marks it with `default: true` in
				// `list_microphones()`). Previously the wizard
				// unconditionally fell back to
				// `mics.microphones[0].id` — which is just the
				// first in sounddevice's enumeration order and is
				// often NOT the system default (especially on
				// Windows where WASAPI ordering differs from the
				// OS default). Falling back to `[0]` only when no
				// device is flagged `default` preserves the prior
				// behaviour for backends/mocks that don't set the
				// flag.
				if (mics.microphones?.length > 0) {
					setSelectedMic((prev) => {
						if (prev && mics.microphones.some((m) => m.id === prev)) {
							return prev;
						}
						const defaultMic = mics.microphones.find(
							(m) => m.default === true,
						);
						return (defaultMic ?? mics.microphones[0]).id;
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
				console.error("Failed to start onboarding:", err);
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

	// ── Focus management (Fix 15) ──────────────────────────────────
	useEffect(() => {
		if (!step) return;
		queueMicrotask(() => {
			headingRef.current?.focus();
		});
	}, [step?.step_name, step]);

	// ── Navigation handlers (Fix 11: submitting + error snacks) ────
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
			} else if (step?.step_name === DONE_STEP_NAME) {
				await call("onboarding_apply");
			}
			const newStep = await call<StepInfo>("onboarding_next_step");
			setStep(newStep);
		} catch (err) {
			console.error("Failed to advance step:", err);
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
		showSnack,
	]);

	const handleApply = useCallback(async () => {
		setSubmitting(true);
		try {
			await call("onboarding_apply");
			if (onComplete) onComplete();
		} catch (err) {
			console.error("Failed to apply onboarding:", err);
			showSnack(t("onboarding.saveFailedSnack"), "error");
		} finally {
			setSubmitting(false);
		}
	}, [call, onComplete, showSnack]);

	const handlePrev = useCallback(async () => {
		setSubmitting(true);
		try {
			const newStep = await call<StepInfo>("onboarding_prev_step");
			setStep(newStep);
		} catch (err) {
			console.error("Failed to go back:", err);
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
			console.error("Failed to skip onboarding:", err);
			showSnack(t("onboarding.saveFailedSnack"), "error");
		} finally {
			setSubmitting(false);
		}
	}, [call, showSnack, onComplete]);

	// Init-error Skip button — best-effort escape: even on failure, close
	// the wizard so the user is not trapped on the error screen.
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
		handleNext,
		handleApply,
		handlePrev,
		handleSkip,
		skipOnInitError,
	};
}
