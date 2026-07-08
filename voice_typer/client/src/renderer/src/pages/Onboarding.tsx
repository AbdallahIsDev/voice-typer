import { useCallback, useEffect, useState } from "react";
import { Spinner } from "@/components/Spinner";
import { Button } from "@/components/ui/button";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

interface StepInfo {
	step: number;
	total_steps: number;
	step_name: string;
}

interface ModelOption {
	name: string;
	size: string;
	speed: string;
	description: string;
}

// #8: Optional callback fired after the user finishes the wizard
// (either by completing all steps or by skipping). App.tsx wires this
// to navigate back to the home page and reload the config so the rest
// of the UI picks up the user's onboarding choices.
interface OnboardingPageProps {
	onComplete?: () => void;
}

export default function OnboardingPage({ onComplete }: OnboardingPageProps) {
	const { call } = usePython();
	const { showSnack, Snackbar } = useSnackbar();
	const [step, setStep] = useState<StepInfo | null>(null);
	const [loading, setLoading] = useState(true);
	const [microphones, setMicrophones] = useState<
		{ id: string; name: string }[]
	>([]);
	const [selectedMic, setSelectedMic] = useState<string>("");
	const [hotkeyPresets, setHotkeyPresets] = useState<string[]>([]);
	const [selectedHotkey, setSelectedHotkey] = useState("<f2>");
	const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
	const [selectedModel, setSelectedModel] = useState("small.en");

	useEffect(() => {
		async function init() {
			try {
				const started = await call<StepInfo>("onboarding_start");
				setStep(started);

				const mics = await call<{
					microphones: { id: string; name: string }[];
				}>("onboarding_get_microphones");
				setMicrophones(mics.microphones || []);
				if (mics.microphones?.length > 0)
					setSelectedMic(mics.microphones[0].id);

				const presets = await call<{ presets: string[] }>(
					"onboarding_get_hotkey_presets",
				);
				setHotkeyPresets(presets.presets || []);

				const models = await call<{ models: ModelOption[] }>(
					"onboarding_get_model_options",
				);
				setModelOptions(models.models || []);
			} catch (err) {
				console.error("Failed to start onboarding:", err);
			} finally {
				setLoading(false);
			}
		}
		init();
	}, [call]);

	const handleNext = useCallback(async () => {
		try {
			// Save current step's selection before advancing
			if (step?.step === 1) {
				await call("onboarding_set_microphone", {
					mic_id: selectedMic || null,
				});
			} else if (step?.step === 2) {
				await call("onboarding_set_hotkey", { hotkey: selectedHotkey });
			} else if (step?.step === 3) {
				await call("onboarding_set_model", { model: selectedModel });
			} else if (step?.step === 4) {
				await call("onboarding_apply");
				showSnack(t("onboarding.setupCompleteSnack"), "success");
				// #8: wizard finished — hand control back to App.tsx so it can
				// navigate to home and reload the config.
				if (onComplete) onComplete();
				return;
			}
			const newStep = await call<StepInfo>("onboarding_next_step");
			setStep(newStep);
		} catch (err) {
			console.error("Failed to advance step:", err);
			showSnack(t("onboarding.saveFailedSnack"), "error");
		}
	}, [
		call,
		step,
		selectedMic,
		selectedHotkey,
		selectedModel,
		showSnack,
		onComplete,
	]);

	const handlePrev = useCallback(async () => {
		try {
			const newStep = await call<StepInfo>("onboarding_prev_step");
			setStep(newStep);
		} catch (err) {
			console.error("Failed to go back:", err);
		}
	}, [call]);

	const handleSkip = useCallback(async () => {
		try {
			await call("onboarding_skip");
			showSnack(t("onboarding.skippedSnack"), "warning");
			// #8: wizard skipped — hand control back to App.tsx.
			if (onComplete) onComplete();
		} catch (err) {
			console.error("Failed to skip onboarding:", err);
		}
	}, [call, showSnack, onComplete]);

	if (loading) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner />
			</div>
		);
	}

	if (!step) return null;

	const progress = ((step.step + 1) / step.total_steps) * 100;

	return (
		<div className="mx-auto flex min-h-full w-full max-w-lg flex-col items-center px-6 pt-28 pb-6">
			{/* Progress bar */}
			<div className="mb-8 w-full">
				<div className="mb-2 flex items-center justify-between text-xs text-(--text-muted)">
					<span>
						{t("onboarding.stepProgress", {
							current: String(step.step + 1),
							total: String(step.total_steps),
						})}
					</span>
					<span>{step.step_name}</span>
				</div>
				<div className="h-1.5 w-full rounded-full bg-(--bg-subtle)">
					<div
						className="h-1.5 rounded-full bg-accent transition-all duration-300"
						style={{ width: `${progress}%` }}
					/>
				</div>
			</div>

			{/* Step content */}
			<div className="w-full rounded-xl border border-border bg-(--bg) p-8">
				{step.step === 0 && (
					<>
						<h1 className="mb-3 text-2xl font-bold text-(--text-primary)">
							{t("onboarding.welcomeTitle")}
						</h1>
						<p className="mb-6 text-sm text-(--text-muted)">
							{t("onboarding.welcomeDescription")}
						</p>
						<ul className="mb-6 space-y-2 text-sm text-(--text-secondary)">
							<li className="flex items-center gap-2">
								<span className="text-accent">1.</span>{" "}
								{t("onboarding.step1Item")}
							</li>
							<li className="flex items-center gap-2">
								<span className="text-accent">2.</span>{" "}
								{t("onboarding.step2Item")}
							</li>
							<li className="flex items-center gap-2">
								<span className="text-accent">3.</span>{" "}
								{t("onboarding.step3Item")}
							</li>
						</ul>
					</>
				)}

				{step.step === 1 && (
					<>
						<h2 className="mb-3 text-lg font-semibold text-(--text-primary)">
							{t("onboarding.micTitle")}
						</h2>
						<p className="mb-4 text-sm text-(--text-muted)">
							{t("onboarding.micDescription")}
						</p>
						{microphones.length > 0 ? (
							<Select value={selectedMic} onValueChange={setSelectedMic}>
								<SelectTrigger
									className="w-full"
									aria-label={t("onboarding.micSelectAria")}
								>
									<SelectValue placeholder={t("onboarding.micSelectAria")} />
								</SelectTrigger>
								<SelectContent>
									{microphones.map((mic) => (
										<SelectItem key={mic.id} value={mic.id}>
											{mic.name}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
						) : (
							<p className="text-sm text-(--text-muted)">
								{t("onboarding.noMics")}
							</p>
						)}
					</>
				)}

				{step.step === 2 && (
					<>
						<h2 className="mb-3 text-lg font-semibold text-(--text-primary)">
							{t("onboarding.hotkeyTitle")}
						</h2>
						<p className="mb-4 text-sm text-(--text-muted)">
							{t("onboarding.hotkeyDescription")}
						</p>
						<Select value={selectedHotkey} onValueChange={setSelectedHotkey}>
							<SelectTrigger
								className="w-full"
								aria-label={t("onboarding.hotkeySelectAria")}
							>
								<SelectValue placeholder={t("onboarding.hotkeySelectAria")} />
							</SelectTrigger>
							<SelectContent>
								{hotkeyPresets.map((hk) => (
									<SelectItem key={hk} value={hk}>
										{hk.replace(/[<>]/g, "").toUpperCase()}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</>
				)}

				{step.step === 3 && (
					<>
						<h2 className="mb-3 text-lg font-semibold text-(--text-primary)">
							{t("onboarding.modelTitle")}
						</h2>
						<p className="mb-4 text-sm text-(--text-muted)">
							{t("onboarding.modelDescription")}
						</p>
						<div className="space-y-3">
							{modelOptions.map((m) => {
								const handleModelSelect = () => setSelectedModel(m.name);
								return (
									<button
										type="button"
										key={m.name}
										onClick={handleModelSelect}
										className={cn(
											"w-full rounded-lg border p-4 text-left transition-colors",
											selectedModel === m.name
												? "border-accent bg-accent/10"
												: "border-border hover:border-accent/50",
										)}
										aria-label={t("onboarding.modelSelectAria", {
											name: m.name,
										})}
										aria-pressed={selectedModel === m.name}
									>
										<div className="flex items-center justify-between">
											<span className="text-sm font-medium text-(--text-primary)">
												{m.name}
											</span>
											<span className="text-xs text-(--text-muted)">
												{m.size}
											</span>
										</div>
										<div className="mt-1 flex items-center justify-between">
											<span className="text-xs text-(--text-muted)">
												{m.description}
											</span>
											<span className="text-xs text-accent">{m.speed}</span>
										</div>
									</button>
								);
							})}
						</div>
					</>
				)}

				{step.step === 4 && (
					<>
						<h2 className="mb-3 text-lg font-semibold text-(--text-primary)">
							{t("onboarding.completeTitle")}
						</h2>
						<p className="mb-4 text-sm text-(--text-muted)">
							{t("onboarding.completeDescription", {
								hotkey: selectedHotkey.replace(/[<>]/g, "").toUpperCase(),
							})}
						</p>
						<div className="rounded-lg bg-(--bg-subtle) p-4 text-xs text-(--text-muted)">
							<p>
								<strong>{t("onboarding.summaryMic")}</strong>{" "}
								{selectedMic || t("onboarding.defaultMic")}
							</p>
							<p>
								<strong>{t("onboarding.summaryHotkey")}</strong>{" "}
								{selectedHotkey.replace(/[<>]/g, "").toUpperCase()}
							</p>
							<p>
								<strong>{t("onboarding.summaryModel")}</strong> {selectedModel}
							</p>
						</div>
					</>
				)}

				{/* Navigation */}
				<div className="mt-6 flex items-center justify-between">
					<div>
						{step.step > 0 && (
							<Button
								variant="ghost"
								onClick={handlePrev}
								aria-label={t("onboarding.backAria")}
							>
								{t("onboarding.back")}
							</Button>
						)}
					</div>
					<div className="flex items-center gap-2">
						{step.step < 4 && (
							<Button
								variant="ghost"
								onClick={handleSkip}
								aria-label={t("onboarding.skipAria")}
							>
								{t("onboarding.skip")}
							</Button>
						)}
						<Button
							variant="default"
							onClick={handleNext}
							aria-label={
								step.step === 4
									? t("onboarding.getStartedAria")
									: t("onboarding.continueAria")
							}
						>
							{step.step === 4
								? t("onboarding.getStarted")
								: t("onboarding.continue")}
						</Button>
					</div>
				</div>
			</div>

			<Snackbar />
		</div>
	);
}
