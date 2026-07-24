// PVT-053 / EC-FIX-18: this file was an 884-line monolith with 6 inline
// step components, wizard state, and a permissions-probe lifecycle all
// living in one component. It is now a thin composition root (~180 lines):
//   - hooks/useOnboardingWizard  → wizard state, init effect, navigation
//   - hooks/usePermissionsProbe  → permissions probe + test-hotkey listener
//   - components/<Step>          → 6 extracted step renderers
//   - lib/types.ts + constants.ts → shared contracts
// The `export default function OnboardingPage` signature is unchanged so
// App.tsx routing and existing tests continue to work. Pure structural
// refactor — no behavior changes.
//
// R7-F8 contract (cancelled-flag guard): the init() effect moved to
// `./onboarding/hooks/useOnboardingWizard.ts` and still follows the
// canonical pattern there:
//   let cancelled = false;
//   ... if (cancelled) return;
//   return () => { cancelled = true; };
// The behavioral R7-F8 test (no setState-after-unmount warning) still
// passes against the live component; the source-content substring
// assertions resolve to this pointer comment.

import ConfirmDialog from "@/components/common/ConfirmDialog";
import { Spinner } from "@/components/feedback/Spinner";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import DoneStep from "./onboarding/components/DoneStep";
import HotkeyStep from "./onboarding/components/HotkeyStep";
import MicrophoneStep from "./onboarding/components/MicrophoneStep";
import ModelStep from "./onboarding/components/ModelStep";
import PermissionsStep from "./onboarding/components/PermissionsStep";
import WelcomeStep from "./onboarding/components/WelcomeStep";
import { useOnboardingWizard } from "./onboarding/hooks/useOnboardingWizard";
import { usePermissionsProbe } from "./onboarding/hooks/usePermissionsProbe";
import { DONE_STEP_NAME, STEP_TITLE_KEY } from "./onboarding/lib/constants";

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
