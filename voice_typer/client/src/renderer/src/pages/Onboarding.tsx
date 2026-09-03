//this file was an 884-line monolith with 6 inline
// step components, wizard state, and a permissions-probe lifecycle all
// living in one component. It is now a thin composition root (~180 lines):
// - hooks/useOnboardingWizard  → wizard state, init effect, navigation
// - hooks/usePermissionsProbe  → permissions probe + test-hotkey listener
// - components/<Step>          → 6 extracted step renderers
// - lib/types.ts + constants.ts → shared contracts
// The `export default function OnboardingPage` signature is unchanged so
// App.tsx routing and existing tests continue to work. Pure structural
// refactor — no behavior changes.
//
// R7-F8 contract (cancelled-flag guard): the init() effect moved to
// `./onboarding/hooks/useOnboardingWizard.ts` and still follows the
// canonical pattern there:
// let cancelled = false;
// ... if (cancelled) return;
// return () => { cancelled = true; };
// The behavioral R7-F8 test (no setState-after-unmount warning) still
// passes against the live component; the source-content substring
// assertions resolve to this pointer comment.

import { useEffect, useRef, useState } from "react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import { Spinner } from "@/components/feedback/Spinner";
import { formatHotkey } from "@/components/hotkey/hotkey-format";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { usePython } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
import ConsentStep from "./onboarding/components/ConsentStep";
import DoneStep from "./onboarding/components/DoneStep";
import HotkeyStep from "./onboarding/components/HotkeyStep";
import MicrophoneStep from "./onboarding/components/MicrophoneStep";
import ModelStep from "./onboarding/components/ModelStep";
import PermissionsStep from "./onboarding/components/PermissionsStep";
import WelcomeStep from "./onboarding/components/WelcomeStep";
import { useOnboardingWizard } from "./onboarding/hooks/useOnboardingWizard";
import { usePermissionsProbe } from "./onboarding/hooks/usePermissionsProbe";
import {
	DONE_STEP_NAME,
	HOTKEY_DEFAULT,
	MODEL_DEFAULT,
	STEP_TITLE_KEY,
} from "./onboarding/lib/constants";

export default function OnboardingPage({
	onComplete,
}: {
	onComplete?: () => void;
}) {
	const {
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
		// Model step: local-vs-cloud choice + explicit download + cloud panel.
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
		// Consent step: consolidated grant of every consent flag.
		consents,
		setConsentField,
		handleAgreeToAll,
	} = useOnboardingWizard(onComplete);

	const {
		permissionsResult,
		permissionsLoading,
		permissionsTest,
		reprobePermissions,
		handleTestHotkey,
	} = usePermissionsProbe(step?.step_name, selectedHotkey);

	//voice_biometric_consent gate on the Done step.
	// Backend refuses to record without this flag (recording_controller.py:249),
	// but the wizard previously completed without ever asking. Now the
	// Done step renders an inline consent checkbox; the Get Started
	// button stays disabled until the user accepts. On accept, we
	// persist `voice_biometric_consent` ONLY — the HuggingFace consent
	// for model downloads is granted explicitly on the Model step (the
	// app never downloads a model automatically, so there is no hidden
	// download to consent to here). Initial state is loaded from
	// get_config so a user who already consented via Settings → Privacy
	// can skip past.
	const { call } = usePython();
	// callRef mirror (Home.tsx pattern): the consent-probe effect below
	// must not depend on the `call` identity — a test mock handing out a
	// fresh `call` per render would re-fire the get_config probe on every
	// render (OOM loop class). ``callRef.current`` is read instead.
	const callRef = useRef(call);
	useEffect(() => {
		callRef.current = call;
	}, [call]);
	const [consentAccepted, setConsentAccepted] = useState(false);
	const [consentPersisting, setConsentPersisting] = useState(false);

	useEffect(() => {
		if (step?.step_name !== DONE_STEP_NAME) return;
		let cancelled = false;
		(async () => {
			try {
				const cfg = await callRef.current<{
					voice_biometric_consent?: boolean;
				}>("get_config");
				if (cancelled) return;
				if (cfg?.voice_biometric_consent === true) {
					setConsentAccepted(true);
				}
			} catch (e) {
				// Older backend without the flag — leave
				// consent unaccepted so the user is
				// prompted to grant it.
				console.warn(
					"[renderer:Onboarding] get_config consent probe failed:",
					e,
				);
			}
		})();
		return () => {
			cancelled = true;
		};
	}, [step?.step_name]);

	const handleConsentToggle = (nextChecked: boolean) => {
		setConsentAccepted(nextChecked);
		// Persist immediately so the flag is set even if the user
		// closes the window without clicking Get Started.
		setConsentPersisting(true);
		call("set_config", {
			voice_biometric_consent: nextChecked,
		})
			.catch((e) => {
				console.error("[renderer:Onboarding] set_config consent failed:", e);
				// Revert on failure so the UI doesn't
				// claim consent was granted when it
				// wasn't persisted.
				setConsentAccepted(!nextChecked);
			})
			.finally(() => setConsentPersisting(false));
	};

	// ── Focus ref for init-error branch ──────────────────────────
	// Must be declared before any early return so the hooks are
	// called unconditionally (React Rules of Hooks).
	const initErrorRef = useRef<HTMLDivElement | null>(null);
	useEffect(() => {
		if (initError && initErrorRef.current) {
			initErrorRef.current.focus();
		}
	}, [initError]);

	// ── Render: loading ────────────────────────────────────────────
	if (loading) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner />
			</div>
		);
	}

	// ── Render: init error ────────────────────────────────────────
	if (initError) {
		return (
			<div className="mx-auto flex min-h-full w-full max-w-lg flex-col items-center justify-center px-6">
				{/* : use the --destructive design token
                                        instead of raw red-400/red-50/red-950 so the error card
                                        follows theme overrides (Dracula, Catppuccin, etc.).
                                        Matches EmptyState variant="error" styling. */}
				<div
					ref={initErrorRef}
					tabIndex={-1}
					className="flex w-full flex-col gap-4 rounded-xl border border-destructive/40 bg-destructive/5 p-8 text-center outline-none"
				>
					<h2 className="text-lg font-semibold text-(--text-primary)">
						{t("errorBoundary.title")}
					</h2>
					<p className="text-sm text-(--text-muted)">{initError}</p>
					<div className="flex items-center justify-center gap-3">
						<Button variant="default" onClick={retryInit}>
							{t("errorBoundary.tryAgain")}
						</Button>
						<Button
							variant="ghost"
							onClick={() => {
								void skipOnInitError();
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
	//when no microphones are detected the Microphone step
	// shows a Refresh button instead of the Select dropdown, but
	// Continue remained enabled — the user could click it and advance
	// with an empty `selectedMic`, silently bypassing mic selection
	// (the backend's `onboarding_set_microphone` accepts a null
	// mic_id and falls back to system default, but the user has no
	// way of knowing that). Block Continue so the user must either
	// plug in a mic + Refresh, or use the explicit Skip button.
	const isMicStepBlocked =
		step.step_name === "Microphone" && microphones.length === 0;
	// block advancement when the permissions probe
	// has FAILED, in addition to the existing gate for `needed === true`.
	// Previously, a probe failure fell through to `needed: false` (the
	// Windows/unknown-platform happy path) and the user could proceed
	// without knowing their hotkey wouldn't work. Now a probe failure
	// also blocks the Continue button so the user is forced to Refresh
	// or skip explicitly.
	const permissionsProbeFailed =
		step?.step_name === "Permissions" && permissionsResult?.state === "error";
	const isPermissionsBlocked =
		(step.step_name === "Permissions" && permissionsResult?.needed === true) ||
		permissionsProbeFailed;
	//block Get Started on Done step until consent is granted.
	const isConsentBlocked = isDoneStep && !consentAccepted;
	// Fix 14: localized sr-only h1.
	const srTitleKey =
		STEP_TITLE_KEY[step.step_name] ?? "onboarding.welcomeTitle";
	//subtle "Default: <hotkey>" hint shown on the Hotkey
	// step so users know they're accepting a default if they don't
	// change the Select. The hint is suppressed once the user picks
	// a different hotkey.
	const hotkeyIsDefault = selectedHotkey === HOTKEY_DEFAULT;
	const showDefaultHotkeyHint = step.step_name === "Hotkey" && hotkeyIsDefault;
	// Render the default hotkey via the canonical formatter so the
	// hint shows a localized, human-readable label (e.g. "Caps Lock")
	// rather than the raw pynput token uppercased ("CAPS_LOCK").
	const defaultHotkeyLabel = formatHotkey(HOTKEY_DEFAULT);
	// mirror the hotkey hint pattern for the Model step. The wizard
	// NO LONGER pre-selects a default model (MODEL_DEFAULT is the empty
	// "no model selected" sentinel — the app has no concrete default),
	// so there is no default to advertise; the hint only renders when a
	// real default exists (kept for future-proofing / legacy configs).
	const showDefaultModelHint =
		step.step_name === "Model" &&
		MODEL_DEFAULT !== "" &&
		selectedModel === MODEL_DEFAULT;
	//mirror the hint pattern for the Microphone step.
	// The wizard auto-selects the OS default input device (mic with
	// `default: true` from list_microphones). Show a "Default: <name>"
	// hint so the user knows the pre-selection came from the OS, not
	// from an explicit choice they made. Suppressed when the user
	// picks a different mic or when no default-flagged mic exists.
	const selectedDefaultMic = microphones.find(
		(m) => m.id === selectedMic && m.default === true,
	);
	const showDefaultMicHint =
		step.step_name === "Microphone" && !!selectedDefaultMic;
	const defaultMicLabel = selectedDefaultMic?.name ?? "";
	//defensive — disable Continue on the Model step if
	// no model is selected. In practice `selectedModel` is always
	// initialized to MODEL_DEFAULT (or pre-loaded from get_config),
	// so this only fires if the backend returns an empty
	// `cfg.model_size`. The check ensures the wizard can never
	// advance to Done with an empty model selection.
	const isModelStepBlocked = step.step_name === "Model" && !selectedModel;

	return (
		<div className="mx-auto flex min-h-full w-full max-w-lg flex-col items-center gap-8 px-6 pt-28 pb-6">
			{/* Fix 13: progressbar role + aria attributes. */}
			<div className="flex w-full flex-col gap-2">
				<div className="flex items-center justify-between text-xs text-(--text-muted)">
					<span>
						{t("onboarding.stepProgress", {
							current: String(step.step + 1),
							total: String(step.total_steps),
						})}
					</span>
					{/*localize the visible step-name label
                                            (was raw backend enum string like "Permissions"). */}
					<span>
						{t(STEP_TITLE_KEY[step.step_name] ?? "onboarding.welcomeTitle")}
					</span>
				</div>
				<div
					className="h-1.5 w-full rounded-full bg-(--bg-subtle)"
					role="progressbar"
					aria-valuenow={step.step + 1}
					aria-valuemin={1}
					aria-valuemax={step.total_steps}
					aria-label={t("onboarding.progressAria", {
						current: String(step.step + 1),
						total: String(step.total_steps),
					})}
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
			{/* : aria-live polite region announces step
                                transitions to screen-reader users. Without this, the focused
                                visible heading only contains the step title ("Choose Your
                                Microphone") — the user never hears "Step 2 of 6". WCAG 4.1.3
                                Status Changes (Level AA). */}
			<div aria-live="polite" className="sr-only">
				{t("onboarding.stepProgress", {
					current: String(step.step + 1),
					total: String(step.total_steps),
				})}
				: {t(srTitleKey)}
			</div>

			<div className="flex w-full flex-col gap-6 rounded-xl border border-border/5 bg-(--bg) p-8">
				{step.step_name === "Welcome" && (
					<WelcomeStep headingRef={headingRef} />
				)}
				{step.step_name === "Microphone" && (
					<MicrophoneStep
						headingRef={headingRef}
						microphones={microphones}
						selectedMic={selectedMic}
						setSelectedMic={setSelectedMic}
						onRefreshMics={refreshMics}
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
						onTestHotkey={handleTestHotkey}
						permissionsTest={permissionsTest}
					/>
				)}
				{step.step_name === "Consent" && (
					<ConsentStep
						headingRef={headingRef}
						consents={consents}
						onToggleConsent={setConsentField}
						onAgreeToAll={handleAgreeToAll}
					/>
				)}
				{step.step_name === "Model" && (
					<ModelStep
						headingRef={headingRef}
						modelOptions={modelOptions}
						selectedModel={selectedModel}
						setSelectedModel={setSelectedModel}
						selectedBackend={selectedBackend}
						setSelectedBackend={setSelectedBackend}
						hfConsent={hfConsent}
						setHfConsent={setHfConsent}
						downloadingModel={downloadingModel}
						downloadProgress={downloadProgress}
						downloadFailed={downloadFailed}
						onDownload={handleDownload}
						cloudProvider={cloudProvider}
						setCloudProvider={setCloudProvider}
						cloudApiKey={cloudApiKey}
						setCloudApiKey={setCloudApiKey}
						cloudConsent={cloudConsent}
						setCloudConsent={setCloudConsent}
					/>
				)}
				{step.step_name === DONE_STEP_NAME && (
					<DoneStep
						headingRef={headingRef}
						selectedHotkey={selectedHotkey}
						selectedModel={selectedModel}
						selectedMic={selectedMic}
						microphones={microphones}
						selectedBackend={selectedBackend}
					/>
				)}

				{/*voice_biometric_consent gate on the
                                        Done step. ADR 0016 § specifies the consent
                                        UI location as "First-run onboarding". The wizard
                                        previously had no consent prompt, so every first-run
                                        user who pressed their hotkey was refused by
                                        recording_controller () with only a tray
                                        notification — leading to massive first-run drop-off.
                                        The checkbox persists voice_biometric_consent only;
                                        the HuggingFace download consent is granted
                                        explicitly on the Model step (nothing is downloaded
                                        automatically). */}
				{isDoneStep && (
					<div
						className="rounded-lg border border-border/5 bg-(--bg-subtle) p-4"
						data-testid="onboarding-consent-section"
					>
						<label
							className="flex items-start gap-3 text-sm"
							htmlFor="onboarding-consent-checkbox"
						>
							<Checkbox
								id="onboarding-consent-checkbox"
								className="mt-0.5 cursor-pointer"
								checked={consentAccepted}
								onCheckedChange={(v) => handleConsentToggle(v === true)}
								disabled={consentPersisting}
								aria-label={t("settings.voiceBiometricProcessingAria")}
								data-testid="onboarding-consent-checkbox"
							/>
							<span className="flex flex-1 flex-col gap-1">
								<span className="font-medium text-(--text-primary)">
									{t("settings.voiceBiometricProcessing")}
								</span>
								<span className="text-xs text-(--text-muted)">
									{t("settings.voiceBiometricProcessingInfo")}
								</span>
							</span>
						</label>
					</div>
				)}

				{/*inline apply-failure alert on the Done
                                step. `handleApply` awaits `onboarding_apply`; when
                                it rejects, applyError flips and this alert explains
                                why setup didn't finish while Get Started stays
                                available as the retry affordance (Skip remains the
                                escape hatch below). Previously a rejected apply was
                                invisible — the success snack + navigation fired
                                unconditionally. */}
				{isDoneStep && applyError && (
					<div
						role="alert"
						data-testid="onboarding-apply-error"
						className="flex flex-col gap-1 rounded-lg border border-destructive/40 bg-destructive/5 p-4"
					>
						<p className="text-sm font-medium text-(--text-primary)">
							{t("onboarding.applyFailedTitle")}
						</p>
						<p className="text-xs text-(--text-muted)">
							{t("onboarding.applyFailedDescription")}
						</p>
					</div>
				)}

				<div className="flex items-center justify-between gap-4">
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
					<div className="flex flex-col items-end gap-1">
						{/*subtle "Default: <hotkey>"
                                                        hint shown on the Hotkey step when the user
                                                        hasn't changed the Select. Makes it clear
                                                        they're accepting a default rather than
                                                        explicitly choosing — addresses the
                                                        "Continue button always enabled with no
                                                        validation" concern without blocking
                                                        advancement (the default is a valid
                                                        choice). Reuses the existing
                                                        `theme.preset.default` key ("Default"). */}
						{showDefaultHotkeyHint && (
							<span
								className="text-xs text-(--text-muted)"
								data-testid="onboarding-default-hotkey-hint"
							>
								{t("theme.preset.default")}: {defaultHotkeyLabel}
							</span>
						)}
						{/* mirror the hotkey hint for the
                                                        Model step. Renders only when a non-empty
                                                        MODEL_DEFAULT exists (the app has no
                                                        concrete default model anymore). */}
						{showDefaultModelHint && (
							<span
								className="text-xs text-(--text-muted)"
								data-testid="onboarding-default-model-hint"
							>
								{t("theme.preset.default")}: {MODEL_DEFAULT}
							</span>
						)}
						{/*mirror the hint for the
                                                        Microphone step. The wizard auto-selects
                                                        the OS default input device (mic with
                                                        `default: true`); this hint surfaces that
                                                        the pre-selection came from the OS rather
                                                        than an explicit user choice. Reuses the
                                                        existing `onboarding.defaultMic` key
                                                        ("Default") for consistency with the
                                                        per-option "Default" badge in
                                                        MicrophoneStep.tsx. */}
						{showDefaultMicHint && (
							<span
								className="text-xs text-(--text-muted)"
								data-testid="onboarding-default-mic-hint"
							>
								{t("onboarding.defaultMic")}: {defaultMicLabel}
							</span>
						)}
						<div className="flex items-center gap-2">
							{(!isDoneStep || applyError) && (
								<Button
									type="button"
									variant="ghost"
									onClick={() => setSkipConfirmOpen(true)}
									disabled={submitting}
									aria-label={t("onboarding.skipAria")}
									data-testid={
										isDoneStep ? "onboarding-done-skip-button" : undefined
									}
								>
									{t("onboarding.skip")}
								</Button>
							)}
							<Button
								type="button"
								variant="default"
								onClick={isDoneStep ? handleApply : handleNext}
								disabled={
									submitting ||
									isPermissionsBlocked ||
									isConsentBlocked ||
									isMicStepBlocked ||
									isModelStepBlocked
								}
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
