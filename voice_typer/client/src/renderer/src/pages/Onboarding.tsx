// OnboardingPage, composition root for the first-run wizard.
// wizard state, and a permissions-probe lifecycle all living in one
// component. It has been decomposed into dedicated modules; the page
// now owns layout + wiring only:
//   - onboarding/hooks/useOnboardingWizard → wizard state, init effect,
//     navigation, step submission
//   - onboarding/hooks/usePermissionsProbe → test-hotkey listener
//   - onboarding/components/<Step>         → step renderers
//   - onboarding/lib/{types,constants}.ts  → shared contracts
// 2026-09-14 onboarding overhaul (user decisions):
//   - 4-step essentials flow: Welcome → Consent → Model → Hotkey.
//     The Microphone step (System Default until changed in Settings →
//     Microphone), the OS-permissions step (keyboard monitoring is
//     standard app behavior, not a consent gate) and the "You are all
//     set" summary step were removed.
//   - MANDATORY: the Skip button and its confirmation dialog are
//     removed entirely (ONB-3). The init-error branch offers Retry
//     only.
//   - Header (ONB-1): the Back button is hidden on step 1 (nowhere to
//     go back to) and appears from step 2 onward; the top-right step
//     title above the progress bar is removed (it duplicated the card
//     title). The "Step N of M" text and the aria progressbar remain.
//   - Focus (ONB-3): the parent card is wider (max-w-xl) and the
//     sidebar is hidden entirely on this page (App.tsx), including
//     under RTL (the sidebar is pinned left in App.tsx, so hiding it
//     can't shift the onboarding column).
// Cancelled-flag contract (async effects): every async effect in this
// wizard follows the canonical guard pattern so no setState lands after
// unmount. The init effect lives in `./onboarding/hooks/useOnboardingWizard.ts`.

import { useEffect, useRef } from "react";
import { Spinner } from "@/components/feedback/Spinner";
import { formatHotkey } from "@/components/hotkey/hotkey-format";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import ConsentStep from "./onboarding/components/ConsentStep";
import HotkeyStep from "./onboarding/components/HotkeyStep";
import ModelStep from "./onboarding/components/ModelStep";
import WelcomeStep from "./onboarding/components/WelcomeStep";
import { useOnboardingWizard } from "./onboarding/hooks/useOnboardingWizard";
import { usePermissionsProbe } from "./onboarding/hooks/usePermissionsProbe";
import {
	FINAL_STEP_NAME,
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
		applyError,
		selectedHotkey,
		setSelectedHotkey,
		selectedModel,
		setSelectedModel,
		hotkeyPresets,
		modelOptions,
		headingRef,
		retryInit,
		handleNext,
		handleApply,
		handlePrev,
		// Model step: local-vs-cloud choice + explicit per-model download.
		selectedBackend,
		setSelectedBackend,
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

	const { permissionsTest, handleTestHotkey } =
		usePermissionsProbe(selectedHotkey);

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
	// Onboarding is MANDATORY (ONB-3): no Skip escape hatch here. The
	// only paths are Retry (re-run the init probes) or fixing the
	// backend connection — a broken IPC bridge must not silently
	// complete setup with defaults.
	if (initError) {
		return (
			<div className="mx-auto flex min-h-full w-full max-w-xl flex-col items-center justify-center px-6">
				{/* theme tokens (--destructive, not raw red-*) so the error card
				    follows theme overrides (Dracula, Catppuccin, etc.). */}
				<div
					ref={initErrorRef}
					tabIndex={-1}
					className="flex w-full flex-col gap-4 rounded-xl border border-destructive/40 bg-destructive/5 p-8 text-center outline-hidden focus-visible:ring-1 focus-visible:ring-ring"
				>
					<h2 className="text-lg font-semibold text-(--text-primary)">
						{t("errorBoundary.title")}
					</h2>
					<p className="text-sm text-(--text-muted)">{initError}</p>
					<div className="flex items-center justify-center gap-3">
						<Button variant="default" onClick={retryInit}>
							{t("errorBoundary.tryAgain")}
						</Button>
					</div>
				</div>
			</div>
		);
	}

	if (!step) return null;

	const isFinalStep = step.step_name === FINAL_STEP_NAME;
	const progress = ((step.step + 1) / step.total_steps) * 100;
	// ONB-1: Back is hidden on the FIRST step (nothing to go back to)
	// and appears from step 2 onward.
	const showBack = step.step > 0;
	// Localized sr-only h1 + aria-live step announcements.
	const srTitleKey =
		STEP_TITLE_KEY[step.step_name] ?? "onboarding.welcomeTitle";
	// Subtle "Default: <hotkey>" hint on the final step so users know
	// they're accepting the default hotkey if they don't change the
	// Select (suppressed once they pick a different one). Rendered via
	// the canonical formatter so the label is localized ("Caps Lock").
	const showDefaultHotkeyHint =
		isFinalStep && selectedHotkey === HOTKEY_DEFAULT;
	//block the final step's Finish button until the wizard collected
	// every mandatory selection (no Skip exists to bypass them).
	const isModelStepBlocked = step.step_name === "Model" && !selectedModel;

	return (
		<div className="mx-auto flex min-h-full w-full max-w-xl flex-col items-center gap-8 px-6 pt-28 pb-6">
			{/* Progress header (ONB-1): "Step N of M" text + bar only — the
			    step-title span that used to sit at the top-right duplicated
			    the card's own <h2> heading and was removed. */}
			<div className="flex w-full flex-col gap-2">
				<div className="text-xs text-(--text-muted)">
					<span>
						{t("onboarding.stepProgress", {
							current: String(step.step + 1),
							total: String(step.total_steps),
						})}
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

			{/* sr-only page heading + aria-live step-change announcements
			    (WCAG 4.1.3). Uses the localized step title. */}
			<h1 className="sr-only">
				{t("onboarding.stepProgress", {
					current: String(step.step + 1),
					total: String(step.total_steps),
				})}
				: {t(srTitleKey)}
			</h1>
			<div aria-live="polite" className="sr-only">
				{t("onboarding.stepProgress", {
					current: String(step.step + 1),
					total: String(step.total_steps),
				})}
				: {t(srTitleKey)}
			</div>

			{/* Parent card (ONB-3): widened max-w-lg → max-w-xl for
			    breathing room around the consent rows and the model
			    accordion. */}
			<div className="flex w-full flex-col gap-6 rounded-xl border border-border/5 bg-(--bg) p-8">
				{step.step_name === "Welcome" && (
					<WelcomeStep headingRef={headingRef} />
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
				{step.step_name === FINAL_STEP_NAME && (
					<HotkeyStep
						headingRef={headingRef}
						hotkeyPresets={hotkeyPresets}
						selectedHotkey={selectedHotkey}
						setSelectedHotkey={setSelectedHotkey}
						onTestHotkey={handleTestHotkey}
						permissionsTest={permissionsTest}
					/>
				)}

				{/* Inline apply-failure alert on the final step.
				    `handleApply` awaits `onboarding_apply`; when it rejects,
				    applyError flips and this alert explains why setup didn't
				    finish while Get Started stays available as the retry
				    affordance. There is no Skip escape hatch (ONB-3): the
				    user retries the apply. */}
				{isFinalStep && applyError && (
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
						{/* ONB-1: the Back button is hidden on step 1 and shown
						    from step 2 onward. */}
						{showBack && (
							<Button
								type="button"
								variant="ghost"
								onClick={handlePrev}
								disabled={submitting}
								aria-label={t("onboarding.backAria")}
							>
								{t("onboarding.back")}
							</Button>
						)}
					</div>
					<div className="flex flex-col items-end gap-1">
						{showDefaultHotkeyHint && (
							<span
								className="text-xs text-(--text-muted)"
								data-testid="onboarding-default-hotkey-hint"
							>
								{t("theme.preset.default")}: {formatHotkey(HOTKEY_DEFAULT)}
							</span>
						)}
						<div className="flex items-center gap-2">
							{/* No Skip button anywhere (ONB-3): onboarding is
							    mandatory; the only exits are completing the flow
							    or the init-error Retry. */}
							<Button
								type="button"
								variant="default"
								onClick={isFinalStep ? handleApply : handleNext}
								disabled={submitting || isModelStepBlocked}
								aria-label={
									isFinalStep
										? t("onboarding.getStartedAria")
										: t("onboarding.continueAria")
								}
							>
								{isFinalStep
									? t("onboarding.getStarted")
									: t("onboarding.continue")}
							</Button>
						</div>
					</div>
				</div>
			</div>
		</div>
	);
}
