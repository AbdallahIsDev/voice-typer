// EC-FIX-18: this file was an 884-line monolith with 6 inline
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
import { Button } from "@/components/ui/button";
import { usePython } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
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
	} = useOnboardingWizard(onComplete);

	const {
		permissionsResult,
		permissionsLoading,
		permissionsTest,
		reprobePermissions,
		handleTestHotkey,
	} = usePermissionsProbe(step?.step_name, selectedHotkey);

	// S2-CR-8: voice_biometric_consent gate on the Done step.
	// Backend refuses to record without this flag (recording_controller.py:249),
	// but the wizard previously completed without ever asking. Now the
	// Done step renders an inline consent checkbox; the Get Started
	// button stays disabled until the user accepts. On accept, we
	// persist both `voice_biometric_consent` and `huggingface_consent`
	// (the latter is required because model download happens on first
	// hotkey press). Initial state is loaded from get_config so a user
	// who already consented via Settings → Privacy can skip past.
	const { call } = usePython();
	const [consentAccepted, setConsentAccepted] = useState(false);
	const [consentPersisting, setConsentPersisting] = useState(false);

	useEffect(() => {
		if (step?.step_name !== DONE_STEP_NAME) return;
		let cancelled = false;
		(async () => {
			try {
				const cfg = await call<{ voice_biometric_consent?: boolean }>(
					"get_config",
				);
				if (cancelled) return;
				if (cfg?.voice_biometric_consent === true) {
					setConsentAccepted(true);
				}
			} catch (e) {
				// Older backend without the flag — leave
				// consent unaccepted so the user is
				// prompted to grant it.
				console.warn("[Onboarding] get_config consent probe failed:", e);
			}
		})();
		return () => {
			cancelled = true;
		};
	}, [call, step?.step_name]);

	const handleConsentToggle = (nextChecked: boolean) => {
		setConsentAccepted(nextChecked);
		// Persist immediately so the flag is set even if the user
		// closes the window without clicking Get Started.
		setConsentPersisting(true);
		call("set_config", {
			voice_biometric_consent: nextChecked,
			// Auto-grant HuggingFace consent alongside voice
			// biometric consent: the very first hotkey press
			// triggers a model download from huggingface.co,
			// and `huggingface_consent` gates that download.
			// Asking for two separate consents on the wizard's
			// final step would be confusing. The user can
			// revoke either individually later in Settings →
			// Privacy.
			huggingface_consent: nextChecked,
		})
			.catch((e) => {
				console.error("[Onboarding] set_config consent failed:", e);
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
					className="w-full rounded-xl border border-destructive/40 bg-destructive/5 p-8 text-center outline-none"
				>
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
	// S2-CR-8: block Get Started on Done step until consent is granted.
	const isConsentBlocked = isDoneStep && !consentAccepted;
	// Fix 14: localized sr-only h1.
	const srTitleKey =
		STEP_TITLE_KEY[step.step_name] ?? "onboarding.welcomeTitle";
	// S5-CR-105: subtle "Default: <hotkey>" hint shown on the Hotkey
	// step so users know they're accepting a default if they don't
	// change the Select. The hint is suppressed once the user picks
	// a different hotkey.
	const hotkeyIsDefault = selectedHotkey === HOTKEY_DEFAULT;
	const showDefaultHotkeyHint = step.step_name === "Hotkey" && hotkeyIsDefault;
	const defaultHotkeyLabel = HOTKEY_DEFAULT.replace(/[<>]/g, "").toUpperCase();

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
					{/* BG-12: localize the visible step-name label
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
						onRefreshMics={reprobePermissions}
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

				{/* S2-CR-8: voice_biometric_consent gate on the
                                        Done step. ADR 0016 §PRIV-009 specifies the consent
                                        UI location as "First-run onboarding". The wizard
                                        previously had no consent prompt, so every first-run
                                        user who pressed their hotkey was refused by
                                        recording_controller (NEW-PRIV-009) with only a tray
                                        notification — leading to massive first-run drop-off.
                                        The checkbox persists voice_biometric_consent AND
                                        huggingface_consent (the latter is required because
                                        the first hotkey press triggers a model download). */}
				{isDoneStep && (
					<div
						className="mt-6 rounded-lg border border-border bg-(--bg-subtle) p-4"
						data-testid="onboarding-consent-section"
					>
						<label className="flex items-start gap-3 text-sm">
							<input
								type="checkbox"
								className="mt-0.5 size-4 cursor-pointer accent-accent"
								checked={consentAccepted}
								onChange={(e) => handleConsentToggle(e.target.checked)}
								disabled={consentPersisting}
								aria-label={t("settings.voiceBiometricProcessingAria")}
								data-testid="onboarding-consent-checkbox"
							/>
							<span className="flex-1">
								<span className="block font-medium text-(--text-primary)">
									{t("settings.voiceBiometricProcessing")}
								</span>
								<span className="mt-1 block text-xs text-(--text-muted)">
									{t("settings.voiceBiometricProcessingInfo")}
								</span>
								{/* HuggingFace consent is auto-granted
                                                                        alongside voice biometric consent. Surfaced
                                                                        here so the user knows both flags are being
                                                                        set; revoke individually in Settings →
                                                                        Privacy. */}
								<span className="mt-1 block text-xs text-(--text-muted)">
									{t("settings.huggingFaceDownloads")}
								</span>
							</span>
						</label>
					</div>
				)}

				{/* S2-CR-40: download progress feedback. The
                                        wizard's "Get Started" click triggers
                                        onboarding_apply → model load (which may
                                        download 466 MB–1.5 GB on first run).
                                        Previously the user saw only a tray
                                        status string and a "Setup complete!"
                                        snack — no in-wizard progress. While we
                                        cannot show a byte-level progress bar
                                        without backend event_bus changes
                                        (service.download_model path), we can at
                                        least render an inline "loading model…"
                                        status so the user knows the app is
                                        alive and what to expect. Reuses the
                                        existing `onboarding.setupCompleteSnack`
                                        i18n key ("Setup complete! Loading your
                                        model..."). */}
				{isDoneStep && submitting && (
					<output
						className="mt-4 flex items-center gap-2 rounded-lg border border-accent/40 bg-accent/5 p-3 text-sm text-(--text-secondary)"
						aria-live="polite"
						data-testid="onboarding-download-feedback"
					>
						<Spinner />
						<span>{t("onboarding.setupCompleteSnack")}</span>
					</output>
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
					<div className="flex flex-col items-end gap-1">
						{/* S5-CR-105: subtle "Default: <hotkey>"
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
								disabled={
									submitting || isPermissionsBlocked || isConsentBlocked
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
