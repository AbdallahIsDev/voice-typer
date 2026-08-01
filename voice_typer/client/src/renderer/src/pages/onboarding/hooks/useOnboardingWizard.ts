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
					console.warn("[useOnboardingWizard] get_config probe failed:", e);
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

	useEffect(() => {
		if (!step) return;
		queueMicrotask(() => {
			headingRef.current?.focus();
		});
	}, [step?.step_name, step]);

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
		setApplyError(false);
		setSubmitting(true);
		try {
			void call("onboarding_apply").catch((err) => {
				console.error("Failed to apply onboarding (async):", err);
			});
			if (onComplete) onComplete();
		} catch (err) {
			console.error("Failed to apply onboarding (sync):", err);
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
				console.error("Failed to refresh microphones:", err);
				showSnack(t("onboarding.saveFailedSnack"), "error");
			});
	}, [call, showSnack]);

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
	};
}
