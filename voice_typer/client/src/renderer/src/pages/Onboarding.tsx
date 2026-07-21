import { useCallback, useEffect, useRef, useState } from "react";
import { Spinner } from "@/components/feedback/Spinner";
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
import type { VoiceTyperConfig } from "@/types/config";

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

// CR-6 (UX-4 / UX-27): renderer was previously out of sync with the
// server's 6-step wizard — it branched on the numeric `step.step` index
// (0/1/2/3/4) and never rendered a Permissions step. The server (see
// `voice_typer/server/onboarding.py:124-141`) declares 6 steps with
// names [Welcome, Microphone, Permissions, Hotkey, Model, Done]. When
// the server reported step=2 with step_name="Permissions", the renderer
// fell through to the Hotkey branch and silently skipped the
// keyboard-permission probe.
//
// To prevent recurrence (a new step added server-side would silently
// fall through to "render nothing" again), we now branch on
// `step.step_name` (the stable string identifier) rather than the
// numeric index. The numeric index is only used for the progress bar
// and the Skip-button guard (which compares against the LAST step
// name, "Done", so adding steps doesn't require touching the guard).
const DONE_STEP_NAME = "Done";

// IPC response shape for `onboarding_check_permissions` (mirrors
// `OnboardingController.check_permissions` in
// `voice_typer/server/onboarding.py:218-314`). `instructions` is null
// on Windows / unknown platforms or when permission is already
// granted; on macOS / Linux with `needed === true` it carries a
// platform-specific setup walkthrough.
interface PermissionsResult {
	platform: "windows" | "macos" | "linux" | "unknown";
	state: "granted" | "denied" | "unknown";
	needed: boolean;
	instructions: {
		title: string;
		steps: string[];
		commands: string[] | null;
	} | null;
}

type PermissionsTestState =
	| { kind: "idle" }
	| { kind: "listening" }
	| { kind: "success" }
	| { kind: "failure" };

export default function OnboardingPage({
	onComplete,
}: {
	onComplete?: () => void;
}) {
	const { call } = usePython();
	const { showSnack } = useSnackbar();

	// ── State ──────────────────────────────────────────────────────

	const [loading, setLoading] = useState(true);
	const [initError, setInitError] = useState<string | null>(null);
	const [step, setStep] = useState<StepInfo | null>(null);
	const [retryCounter, setRetryCounter] = useState(0);

	// Wizard selections
	const [selectedHotkey, setSelectedHotkey] = useState("<f2>");
	const [selectedModel, setSelectedModel] = useState("small.en");
	const [selectedMic, setSelectedMic] = useState("");
	const [hotkeyPresets, setHotkeyPresets] = useState<string[]>([]);
	const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
	const [microphones, setMicrophones] = useState<
		{ id: string; name: string }[]
	>([]);

	// CR-6: Permissions step state.
	const [permissionsResult, setPermissionsResult] =
		useState<PermissionsResult | null>(null);
	const [permissionsLoading, setPermissionsLoading] = useState(false);
	const [permissionsTest, setPermissionsTest] = useState<PermissionsTestState>({
		kind: "idle",
	});
	const permissionsTestTimeoutRef = useRef<
		ReturnType<typeof setTimeout> | undefined
	>(undefined);
	const permissionsHeadingRef = useRef<HTMLHeadingElement | null>(null);

	// ── Init effect ────────────────────────────────────────────────

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
						const cfgHotkey = cfg.hotkey ?? "<f2>";
						if (cfgHotkey) setSelectedHotkey(cfgHotkey);
						const cfgModel = cfg.model_size ?? "small.en";
						if (cfgModel) setSelectedModel(cfgModel);
						const cfgMic = cfg.microphone ?? "";
						setSelectedMic(cfgMic);
					}
				} catch {
					// Older backend without get_config — fall back to defaults.
				}

				const mics = await call<{
					microphones: { id: string; name: string }[];
				}>("onboarding_get_microphones");
				if (cancelled) return;
				setMicrophones(mics.microphones || []);
				if (mics.microphones?.length > 0) {
					setSelectedMic((prev) => {
						if (prev && mics.microphones.some((m) => m.id === prev)) {
							return prev;
						}
						return mics.microphones[0].id;
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

	// ── Permissions probe effect ───────────────────────────────────

	useEffect(() => {
		if (step?.step_name !== "Permissions") {
			setPermissionsResult(null);
			setPermissionsTest({ kind: "idle" });
			return;
		}
		let cancelled = false;
		setPermissionsLoading(true);
		setPermissionsResult(null);
		setPermissionsTest({ kind: "idle" });
		queueMicrotask(() => {
			permissionsHeadingRef.current?.focus();
		});
		call<PermissionsResult>("onboarding_check_permissions")
			.then((result) => {
				if (cancelled) return;
				setPermissionsResult(result);
			})
			.catch((err) => {
				if (cancelled) return;
				console.error("Failed to check permissions:", err);
				setPermissionsResult({
					platform: "unknown",
					state: "unknown",
					needed: false,
					instructions: null,
				});
			})
			.finally(() => {
				if (!cancelled) setPermissionsLoading(false);
			});
		return () => {
			cancelled = true;
			if (permissionsTestTimeoutRef.current) {
				clearTimeout(permissionsTestTimeoutRef.current);
				permissionsTestTimeoutRef.current = undefined;
			}
		};
	}, [call, step?.step_name]);

	// ── Hotkey normalizer ──────────────────────────────────────────

	const normalizeHotkey = useCallback((raw: string): string => {
		return raw.replace(/[<>]/g, "").replace(/_/g, "").toLowerCase();
	}, []);

	// ── Test hotkey button handler ─────────────────────────────────

	const handleTestHotkey = useCallback(() => {
		if (permissionsTest.kind === "listening") return;
		setPermissionsTest({ kind: "listening" });
		const target = normalizeHotkey(selectedHotkey);
		const onKeyDown = (e: KeyboardEvent) => {
			const pressed = normalizeHotkey(e.key);
			if (pressed && pressed === target) {
				window.removeEventListener("keydown", onKeyDown);
				if (permissionsTestTimeoutRef.current) {
					clearTimeout(permissionsTestTimeoutRef.current);
					permissionsTestTimeoutRef.current = undefined;
				}
				setPermissionsTest({ kind: "success" });
			}
		};
		window.addEventListener("keydown", onKeyDown);
		permissionsTestTimeoutRef.current = setTimeout(() => {
			window.removeEventListener("keydown", onKeyDown);
			setPermissionsTest({ kind: "failure" });
			permissionsTestTimeoutRef.current = undefined;
		}, 5_000);
	}, [normalizeHotkey, selectedHotkey, permissionsTest.kind]);

	// ── Handle Next (persist selection and advance) ────────────────

	const handleNext = useCallback(async () => {
		try {
			// Persist the current step's selection before advancing.
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
		}
	}, [call, step?.step_name, selectedMic, selectedHotkey, selectedModel]);

	// ── Handle Apply (apply all settings and complete) ─────────────

	const handleApply = useCallback(async () => {
		try {
			await call("onboarding_apply");
			if (onComplete) onComplete();
		} catch (err) {
			console.error("Failed to apply onboarding:", err);
		}
	}, [call, onComplete]);

	// ── Handle Prev / Skip ─────────────────────────────────────────

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
			if (onComplete) onComplete();
		} catch (err) {
			console.error("Failed to skip onboarding:", err);
		}
	}, [call, showSnack, onComplete]);

	// ── Render: loading ────────────────────────────────────────────

	if (loading) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner />
			</div>
		);
	}

	// ── Render: init error ─────────────────────────────────────────

	if (initError) {
		return (
			<div className="mx-auto flex min-h-full w-full max-w-lg flex-col items-center justify-center px-6">
				<div className="w-full rounded-xl border border-red-400/40 bg-red-50 dark:bg-red-950/20 p-8 text-center">
					<h2 className="mb-2 text-lg font-semibold text-(--text-primary)">
						{t("errorBoundary.title")}
					</h2>
					<p className="mb-4 text-sm text-(--text-muted)">{initError}</p>
					<div className="flex items-center justify-center gap-3">
						<Button variant="default" onClick={retryInit}>
							{t("errorBoundary.tryAgain")}
						</Button>
						<Button
							variant="ghost"
							onClick={async () => {
								try {
									await call("onboarding_skip");
									showSnack(t("onboarding.skippedSnack"), "warning");
									if (onComplete) onComplete();
								} catch {
									if (onComplete) onComplete();
								}
							}}
							aria-label={t("onboarding.skipAria")}
						>
							{t("onboarding.skip")}
						</Button>
					</div>
				</div>
			</div>
		);
	}

	if (!step) return null;

	const progress = ((step.step + 1) / step.total_steps) * 100;
	const isDoneStep = step.step_name === DONE_STEP_NAME;

	// ── Render: wizard ─────────────────────────────────────────────

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

			{/* Screen-reader-only page heading */}
			<h1 className="sr-only">{step.step_name}</h1>

			{/* Step content */}
			<div className="w-full rounded-xl border border-border bg-(--bg) p-8">
				{step.step_name === "Welcome" && (
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

				{step.step_name === "Microphone" && (
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

				{/* CR-6: Permissions step */}
				{step.step_name === "Permissions" && (
					<>
						<h2
							ref={permissionsHeadingRef}
							tabIndex={-1}
							className="mb-3 outline-none text-lg font-semibold text-(--text-primary)"
						>
							{t("onboarding.permissionsTitle")}
						</h2>
						<p className="mb-4 text-sm text-(--text-muted)">
							{t("onboarding.permissionsDescription")}
						</p>

						<div
							aria-live="polite"
							aria-busy={permissionsLoading}
							className="mb-4"
						>
							{permissionsLoading && (
								<div className="flex items-center gap-2 text-sm text-(--text-muted)">
									<Spinner />
									<span>{t("onboarding.permissionsLoading")}</span>
								</div>
							)}

							{!permissionsLoading &&
								permissionsResult &&
								(permissionsResult.needed ? (
									<output className="rounded-lg border border-amber-400/40 bg-amber-50 dark:bg-amber-950/20 p-4 text-sm">
										<p className="mb-2 font-medium text-(--text-primary)">
											{t("onboarding.permissionsNeeded")}
										</p>
										{permissionsResult.instructions && (
											<div className="space-y-2">
												<p className="text-xs font-semibold uppercase tracking-wide text-(--text-muted)">
													{permissionsResult.instructions.title}
												</p>
												<ol className="ml-4 list-decimal space-y-1 text-xs text-(--text-secondary)">
													{permissionsResult.instructions.steps.map((s) => (
														<li key={s}>{s}</li>
													))}
												</ol>
												{permissionsResult.instructions.commands &&
													permissionsResult.instructions.commands.length >
														0 && (
														<pre className="mt-2 overflow-x-auto rounded bg-(--bg-subtle) p-2 text-xs text-(--text-secondary)">
															{permissionsResult.instructions.commands.join(
																"\n",
															)}
														</pre>
													)}
											</div>
										)}
									</output>
								) : permissionsResult.state === "granted" ? (
									<p className="text-sm text-(--text-secondary)">
										{t("onboarding.permissionsOk")}
									</p>
								) : (
									<p className="text-sm text-(--text-secondary)">
										{t("onboarding.permissionsNoneNeeded")}
									</p>
								))}
						</div>

						{/* "Test hotkey" button */}
						<div className="space-y-2">
							<Button
								type="button"
								variant="outline"
								onClick={handleTestHotkey}
								disabled={
									permissionsLoading || permissionsTest.kind === "listening"
								}
								aria-label={t("onboarding.permissionsTestButton")}
							>
								{t("onboarding.permissionsTestButton")}
							</Button>
							<div aria-live="polite" className="text-xs text-(--text-muted)">
								{permissionsTest.kind === "listening" && (
									<span>{t("onboarding.permissionsTestLabel")}</span>
								)}
								{permissionsTest.kind === "success" && (
									<span className="text-green-600 dark:text-green-400">
										{t("onboarding.permissionsTestSuccess")}
									</span>
								)}
								{permissionsTest.kind === "failure" && (
									<span className="text-red-600 dark:text-red-400">
										{t("onboarding.permissionsTestFailure")}
									</span>
								)}
							</div>
						</div>
					</>
				)}

				{step.step_name === "Hotkey" && (
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

				{step.step_name === "Model" && (
					<>
						<h2 className="mb-3 text-lg font-semibold text-(--text-primary)">
							{t("onboarding.modelTitle")}
						</h2>
						<p className="mb-4 text-sm text-(--text-muted)">
							{t("onboarding.modelDescription")}
						</p>
						<Select value={selectedModel} onValueChange={setSelectedModel}>
							<SelectTrigger
								className="w-full"
								aria-label={t("onboarding.modelSelectAria")}
							>
								<SelectValue placeholder={t("onboarding.modelSelectAria")} />
							</SelectTrigger>
							<SelectContent>
								{modelOptions.map((m) => (
									<SelectItem key={m.name} value={m.name}>
										{m.description} — {m.size} ({m.speed})
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</>
				)}

				{step.step_name === DONE_STEP_NAME && (
					<>
						<h2 className="mb-3 text-lg font-semibold text-(--text-primary)">
							{t("onboarding.completeTitle")}
						</h2>
						<div className="mb-6 space-y-2 text-sm text-(--text-secondary)">
							<p>
								{t("onboarding.doneHotkey")}{" "}
								<strong>
									{selectedHotkey.replace(/[<>]/g, "").toUpperCase()}
								</strong>
							</p>
							<p>
								{t("onboarding.doneModel")} <strong>{selectedModel}</strong>
							</p>
							{selectedMic && (
								<p>
									{t("onboarding.doneMic")}{" "}
									<strong>
										{microphones.find((m) => m.id === selectedMic)?.name ??
											selectedMic}
									</strong>
								</p>
							)}
						</div>
					</>
				)}

				{/* Navigation buttons */}
				<div className="mt-8 flex items-center justify-between gap-4">
					<div>
						{!isDoneStep && (
							<Button
								type="button"
								variant="ghost"
								onClick={handlePrev}
								disabled={step.step === 0}
								aria-label={t("onboarding.backAria")}
							>
								{t("onboarding.back")}
							</Button>
						)}
					</div>
					<div className="flex items-center gap-2">
						{!isDoneStep && (
							<Button
								type="button"
								variant="ghost"
								onClick={handleSkip}
								aria-label={t("onboarding.skipAria")}
							>
								{t("onboarding.skip")}
							</Button>
						)}
						<Button
							type="button"
							variant="default"
							onClick={isDoneStep ? handleApply : handleNext}
							aria-label={
								isDoneStep
									? t("onboarding.getStartedAria")
									: t("onboarding.continueAria")
							}
						>
							{isDoneStep
								? t("onboarding.getStarted")
								: t("onboarding.continue")}
						</Button>
					</div>
				</div>
			</div>
		</div>
	);
}
