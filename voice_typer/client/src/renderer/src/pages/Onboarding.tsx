import { type Ref, useCallback, useEffect, useRef, useState } from "react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
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

// PVT-053: this file was a 665-line monolith. The six per-step render
// branches are now inline sub-components (WelcomeStep … DoneStep) so the
// main orchestrator only owns state + navigation. PVT-005/007/052 + a11y
// fixes (PVT-013-style progressbar, sr-only h1, focus management) are
// applied along the way.

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

// PVT-052: backend returns i18n keys (`title_key` / `steps_keys`); the
// optional literal fields remain for backward compat with older backends.
interface PermissionsInstructions {
	title?: string;
	steps?: string[];
	title_key?: string;
	steps_keys?: string[];
	commands: string[] | null;
}

interface PermissionsResult {
	platform: "windows" | "macos" | "linux" | "unknown";
	state: "granted" | "denied" | "unknown";
	needed: boolean;
	instructions: PermissionsInstructions | null;
}

type PermissionsTestState =
	| { kind: "idle" }
	| { kind: "listening" }
	| { kind: "success" }
	| { kind: "failure" };

const DONE_STEP_NAME = "Done";
// Fix 14: localized step title for the sr-only <h1>.
const STEP_TITLE_KEY: Record<string, string> = {
	Welcome: "onboarding.welcomeTitle",
	Microphone: "onboarding.micTitle",
	Permissions: "onboarding.permissionsTitle",
	Hotkey: "onboarding.hotkeyTitle",
	Model: "onboarding.modelTitle",
	Done: "onboarding.completeTitle",
};
// Fix 17: renderer default must match `OnboardingController.selected_hotkey`
// (`<caps_lock>`) — previously `<f2>`, which silently overrode the backend.
const HOTKEY_DEFAULT = "<caps_lock>";
// Fix 10: 5s → 10s — too short for users still reading the instructions.
const TEST_HOTKEY_TIMEOUT_MS = 10_000;
const HEADING_CLASS =
	"mb-3 text-lg font-semibold text-(--text-primary) outline-none";

// ── Inline step sub-components ──────────────────────────────────────

function WelcomeStep({ headingRef }: { headingRef: Ref<HTMLHeadingElement> }) {
	return (
		<>
			<h1
				ref={headingRef}
				tabIndex={-1}
				className="mb-3 text-2xl font-bold text-(--text-primary) outline-none"
			>
				{t("onboarding.welcomeTitle")}
			</h1>
			<p className="mb-6 text-sm text-(--text-muted)">
				{t("onboarding.welcomeDescription")}
			</p>
			{/* Fix 12: render all 5 step items (was only 3). */}
			<ul className="mb-6 space-y-2 text-sm text-(--text-secondary)">
				{[1, 2, 3, 4, 5].map((n) => (
					<li key={n} className="flex items-center gap-2">
						<span className="text-accent">{n}.</span>{" "}
						{t(`onboarding.step${n}Item`)}
					</li>
				))}
			</ul>
		</>
	);
}

interface MicrophoneStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	microphones: { id: string; name: string }[];
	selectedMic: string;
	setSelectedMic: (v: string) => void;
}

function MicrophoneStep({
	headingRef,
	microphones,
	selectedMic,
	setSelectedMic,
}: MicrophoneStepProps) {
	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
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
				<p className="text-sm text-(--text-muted)">{t("onboarding.noMics")}</p>
			)}
		</>
	);
}

interface PermissionsStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	permissionsResult: PermissionsResult | null;
	permissionsLoading: boolean;
	permissionsTest: PermissionsTestState;
	onTestHotkey: () => void;
	onRefreshPermission: () => void;
}

function PermissionsStep({
	headingRef,
	permissionsResult,
	permissionsLoading,
	permissionsTest,
	onTestHotkey,
	onRefreshPermission,
}: PermissionsStepProps) {
	// PVT-052: prefer i18n keys; fall back to literals for legacy backends.
	const instr = permissionsResult?.instructions ?? null;
	const titleText = instr?.title_key ? t(instr.title_key) : instr?.title;
	const stepTexts = instr?.steps_keys
		? instr.steps_keys.map((k) => t(k))
		: (instr?.steps ?? []);
	// Fix 10: branch failure message on permission state.
	const failureMessage =
		permissionsResult?.needed === true
			? t("onboarding.permissionsTestFailureBlocked")
			: t("onboarding.permissionsTestFailure");

	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
				{t("onboarding.permissionsTitle")}
			</h2>
			<p className="mb-4 text-sm text-(--text-muted)">
				{t("onboarding.permissionsDescription")}
			</p>
			<div aria-live="polite" aria-busy={permissionsLoading} className="mb-4">
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
							{titleText && stepTexts.length > 0 && (
								<div className="space-y-2">
									<p className="text-xs font-semibold uppercase tracking-wide text-(--text-muted)">
										{titleText}
									</p>
									<ol className="ml-4 list-decimal space-y-1 text-xs text-(--text-secondary)">
										{stepTexts.map((s) => (
											<li key={s}>{s}</li>
										))}
									</ol>
									{instr?.commands && instr.commands.length > 0 && (
										<pre className="mt-2 overflow-x-auto rounded bg-(--bg-subtle) p-2 text-xs text-(--text-secondary)">
											{instr.commands.join("\n")}
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
			{/* PVT-007: refresh-permission button — re-probes after granting. */}
			{permissionsResult?.needed === true && !permissionsLoading && (
				<div className="mb-4">
					<Button
						type="button"
						variant="outline"
						onClick={onRefreshPermission}
						aria-label={t("onboarding.permissionsRefreshAria")}
					>
						{t("onboarding.permissionsRefresh")}
					</Button>
				</div>
			)}
			<div className="space-y-2">
				<Button
					type="button"
					variant="outline"
					onClick={onTestHotkey}
					disabled={permissionsLoading || permissionsTest.kind === "listening"}
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
							{failureMessage}
						</span>
					)}
				</div>
			</div>
		</>
	);
}

interface HotkeyStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	hotkeyPresets: string[];
	selectedHotkey: string;
	setSelectedHotkey: (v: string) => void;
}

function HotkeyStep({
	headingRef,
	hotkeyPresets,
	selectedHotkey,
	setSelectedHotkey,
}: HotkeyStepProps) {
	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
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
	);
}

interface ModelStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	modelOptions: ModelOption[];
	selectedModel: string;
	setSelectedModel: (v: string) => void;
}

function ModelStep({
	headingRef,
	modelOptions,
	selectedModel,
	setSelectedModel,
}: ModelStepProps) {
	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
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
	);
}

interface DoneStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	selectedHotkey: string;
	selectedModel: string;
	selectedMic: string;
	microphones: { id: string; name: string }[];
}

function DoneStep({
	headingRef,
	selectedHotkey,
	selectedModel,
	selectedMic,
	microphones,
}: DoneStepProps) {
	// PVT-005: use existing `summaryHotkey`/`summaryMic`/`summaryModel`.
	// The old `doneHotkey`/`doneMic`/`doneModel` keys never existed in any
	// locale, so the Done step rendered raw key strings instead of labels.
	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
				{t("onboarding.completeTitle")}
			</h2>
			<div className="mb-6 space-y-2 text-sm text-(--text-secondary)">
				<p>
					{t("onboarding.summaryHotkey")}{" "}
					<strong>{selectedHotkey.replace(/[<>]/g, "").toUpperCase()}</strong>
				</p>
				<p>
					{t("onboarding.summaryModel")} <strong>{selectedModel}</strong>
				</p>
				{selectedMic && (
					<p>
						{t("onboarding.summaryMic")}{" "}
						<strong>
							{microphones.find((m) => m.id === selectedMic)?.name ??
								selectedMic}
						</strong>
					</p>
				)}
			</div>
		</>
	);
}

// ── Main wizard page ────────────────────────────────────────────────

export default function OnboardingPage({
	onComplete,
}: {
	onComplete?: () => void;
}) {
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
	const [microphones, setMicrophones] = useState<
		{ id: string; name: string }[]
	>([]);

	const [permissionsResult, setPermissionsResult] =
		useState<PermissionsResult | null>(null);
	const [permissionsLoading, setPermissionsLoading] = useState(false);
	const [permissionsTest, setPermissionsTest] = useState<PermissionsTestState>({
		kind: "idle",
	});

	// Fix 9: keydown listener + timeout stored in refs so the effect
	// cleanup can tear them down deterministically (was leaking on unmount).
	const permissionsTestTimeoutRef = useRef<
		ReturnType<typeof setTimeout> | undefined
	>(undefined);
	const permissionsTestKeydownRef = useRef<((e: KeyboardEvent) => void) | null>(
		null,
	);
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
				} catch {
					/* older backend without get_config */
				}
				const mics = await call<{
					microphones: { id: string; name: string }[];
				}>("onboarding_get_microphones");
				if (cancelled) return;
				setMicrophones(mics.microphones || []);
				if (mics.microphones?.length > 0) {
					setSelectedMic((prev) =>
						prev && mics.microphones.some((m) => m.id === prev)
							? prev
							: mics.microphones[0].id,
					);
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
	const reprobePermissions = useCallback(() => {
		setPermissionsLoading(true);
		setPermissionsResult(null);
		setPermissionsTest({ kind: "idle" });
		call<PermissionsResult>("onboarding_check_permissions")
			.then((result) => setPermissionsResult(result))
			.catch((err) => {
				console.error("Failed to check permissions:", err);
				setPermissionsResult({
					platform: "unknown",
					state: "unknown",
					needed: false,
					instructions: null,
				});
			})
			.finally(() => setPermissionsLoading(false));
	}, [call]);

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
		call<PermissionsResult>("onboarding_check_permissions")
			.then((result) => {
				if (!cancelled) setPermissionsResult(result);
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
			if (permissionsTestKeydownRef.current) {
				window.removeEventListener(
					"keydown",
					permissionsTestKeydownRef.current,
				);
				permissionsTestKeydownRef.current = null;
			}
		};
	}, [call, step?.step_name]);

	// ── Focus management (Fix 15) ──────────────────────────────────
	useEffect(() => {
		if (!step) return;
		queueMicrotask(() => {
			headingRef.current?.focus();
		});
	}, [step?.step_name, step]);

	const normalizeHotkey = useCallback((raw: string): string => {
		return raw.replace(/[<>]/g, "").replace(/_/g, "").toLowerCase();
	}, []);

	// ── Test hotkey handler (Fix 9: ref-tracked listener; Fix 10: 10s) ─
	const handleTestHotkey = useCallback(() => {
		if (permissionsTest.kind === "listening") return;
		setPermissionsTest({ kind: "listening" });
		const target = normalizeHotkey(selectedHotkey);
		const onKeyDown = (e: KeyboardEvent) => {
			const pressed = normalizeHotkey(e.key);
			if (pressed && pressed === target) {
				window.removeEventListener("keydown", onKeyDown);
				if (permissionsTestKeydownRef.current === onKeyDown)
					permissionsTestKeydownRef.current = null;
				if (permissionsTestTimeoutRef.current) {
					clearTimeout(permissionsTestTimeoutRef.current);
					permissionsTestTimeoutRef.current = undefined;
				}
				setPermissionsTest({ kind: "success" });
			}
		};
		permissionsTestKeydownRef.current = onKeyDown;
		window.addEventListener("keydown", onKeyDown);
		permissionsTestTimeoutRef.current = setTimeout(() => {
			window.removeEventListener("keydown", onKeyDown);
			if (permissionsTestKeydownRef.current === onKeyDown)
				permissionsTestKeydownRef.current = null;
			setPermissionsTest({ kind: "failure" });
			permissionsTestTimeoutRef.current = undefined;
		}, TEST_HOTKEY_TIMEOUT_MS);
	}, [normalizeHotkey, selectedHotkey, permissionsTest.kind]);

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
	// PVT-007: gate advancement when OS keyboard permission is required.
	const isPermissionsBlocked =
		step.step_name === "Permissions" && permissionsResult?.needed === true;
	// Fix 14: localized sr-only h1.
	const srTitleKey =
		STEP_TITLE_KEY[step.step_name] ?? "onboarding.welcomeTitle";

	return (
		<div className="mx-auto flex min-h-full w-full max-w-lg flex-col items-center px-6 pt-28 pb-6">
			{/* Fix 13: progressbar role + aria attributes. */}
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
				<div
					className="h-1.5 w-full rounded-full bg-(--bg-subtle)"
					role="progressbar"
					aria-valuenow={step.step + 1}
					aria-valuemin={1}
					aria-valuemax={step.total_steps}
					aria-label={t("onboarding.progressAria")}
				>
					<div
						className="h-1.5 rounded-full bg-accent transition-all duration-300"
						style={{ width: `${progress}%` }}
					/>
				</div>
			</div>

			{/* Fix 14: sr-only page heading. Uses the localized step title
				(was raw `step.step_name` like "Permissions"). The step-
				progress prefix keeps this text distinct from the visible
				per-step heading so `getByText` in tests resolves to a
				single element, and gives screen readers the step context. */}
			<h1 className="sr-only">
				{t("onboarding.stepProgress", {
					current: String(step.step + 1),
					total: String(step.total_steps),
				})}
				: {t(srTitleKey)}
			</h1>

			<div className="w-full rounded-xl border border-border bg-(--bg) p-8">
				{step.step_name === "Welcome" && (
					<WelcomeStep headingRef={headingRef} />
				)}
				{step.step_name === "Microphone" && (
					<MicrophoneStep
						headingRef={headingRef}
						microphones={microphones}
						selectedMic={selectedMic}
						setSelectedMic={setSelectedMic}
					/>
				)}
				{step.step_name === "Permissions" && (
					<PermissionsStep
						headingRef={headingRef}
						permissionsResult={permissionsResult}
						permissionsLoading={permissionsLoading}
						permissionsTest={permissionsTest}
						onTestHotkey={handleTestHotkey}
						onRefreshPermission={reprobePermissions}
					/>
				)}
				{step.step_name === "Hotkey" && (
					<HotkeyStep
						headingRef={headingRef}
						hotkeyPresets={hotkeyPresets}
						selectedHotkey={selectedHotkey}
						setSelectedHotkey={setSelectedHotkey}
					/>
				)}
				{step.step_name === "Model" && (
					<ModelStep
						headingRef={headingRef}
						modelOptions={modelOptions}
						selectedModel={selectedModel}
						setSelectedModel={setSelectedModel}
					/>
				)}
				{step.step_name === DONE_STEP_NAME && (
					<DoneStep
						headingRef={headingRef}
						selectedHotkey={selectedHotkey}
						selectedModel={selectedModel}
						selectedMic={selectedMic}
						microphones={microphones}
					/>
				)}

				<div className="mt-8 flex items-center justify-between gap-4">
					<div>
						{/* Fix 16: Back button shown on Done step too (was hidden). */}
						<Button
							type="button"
							variant="ghost"
							onClick={handlePrev}
							disabled={step.step === 0 || submitting}
							aria-label={t("onboarding.backAria")}
						>
							{t("onboarding.back")}
						</Button>
					</div>
					<div className="flex items-center gap-2">
						{!isDoneStep && (
							<Button
								type="button"
								variant="ghost"
								onClick={() => setSkipConfirmOpen(true)}
								disabled={submitting}
								aria-label={t("onboarding.skipAria")}
							>
								{t("onboarding.skip")}
							</Button>
						)}
						<Button
							type="button"
							variant="default"
							onClick={isDoneStep ? handleApply : handleNext}
							disabled={submitting || isPermissionsBlocked}
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

			{/* Fix 4: skip confirmation dialog (existing i18n keys). */}
			<ConfirmDialog
				open={skipConfirmOpen}
				title={t("onboarding.skipConfirmTitle")}
				message={t("onboarding.skipConfirmMessage")}
				confirmLabel={t("onboarding.skipConfirmLabel")}
				variant="warning"
				onConfirm={() => {
					setSkipConfirmOpen(false);
					void handleSkip();
				}}
				onCancel={() => setSkipConfirmOpen(false)}
			/>
		</div>
	);
}
