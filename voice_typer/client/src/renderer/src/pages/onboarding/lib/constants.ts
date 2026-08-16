//shared constants extracted from Onboarding.tsx.

export const DONE_STEP_NAME = "Done";

// Fix 14: localized step title for the sr-only <h1>.
export const STEP_TITLE_KEY: Record<string, string> = {
	Welcome: "onboarding.welcomeTitle",
	Microphone: "onboarding.micTitle",
	Permissions: "onboarding.permissionsTitle",
	Hotkey: "onboarding.hotkeyTitle",
	Consent: "onboarding.consentTitle",
	Model: "onboarding.modelTitle",
	Done: "onboarding.completeTitle",
};

// Fix 17: renderer default must match `OnboardingController.selected_hotkey`
// (`<caps_lock>`) — previously `<f2>`, which silently overrode the backend.
// The constant itself now lives in `components/hotkey/hotkey-utils.ts`
// (next to `formatHotkey` and `configHotkeyLabels`, so config-driven
// hotkey defaults and their label computation share one module);
// re-exported here so existing importers (App, Home, the wizard) and
// the lockstep comment history stay intact.
export { HOTKEY_DEFAULT } from "@/components/hotkey/hotkey-utils";

// Renderer default model must match the backend's canonical default
// `DEFAULT_MODEL_SIZE` (`voice_typer/server/model_registry.py`) so the
// wizard can detect "user is accepting the default" and show a
// "Default: <name>" hint next to the Continue button — mirroring the
// HOTKEY_DEFAULT hint pattern. Change the default in the backend's
// `DEFAULT_MODEL_SIZE` (ONE place); keep THIS value in lockstep — the
// Python test `tests/test_default_model_sync.py` asserts the two match.
export const MODEL_DEFAULT = "tiny";

// Fix 10: 5s → 10s — too short for users still reading the instructions.
export const TEST_HOTKEY_TIMEOUT_MS = 10_000;

export const HEADING_CLASS =
	"mb-3 text-lg font-semibold text-(--text-primary) outline-none";

// Duration (in seconds) of the onboarding "Test microphone" recording.
// Shorter than the full Microphone page's test (10s) because the wizard
// only needs enough audio to show a live input level — the user hasn't
// read the full instructions yet.
export const ONBOARDING_MIC_TEST_DURATION_SEC = 5;
