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
export const HOTKEY_DEFAULT = "<caps_lock>";

//renderer default model must match
// `OnboardingController.selected_model` ("small.en") so the wizard can
// detect "user is accepting the default" and show a "Default: small.en"
// hint next to the Continue button — mirroring the HOTKEY_DEFAULT hint
// pattern. If the backend changes its default, this must be updated in
// lock-step (the Python test `test_onboarding.py::TestOnboardingSelections`
// asserts the backend default is "small.en").
export const MODEL_DEFAULT = "small.en";

// Fix 10: 5s → 10s — too short for users still reading the instructions.
export const TEST_HOTKEY_TIMEOUT_MS = 10_000;

export const HEADING_CLASS =
	"mb-3 text-lg font-semibold text-(--text-primary) outline-none";

// Duration (in seconds) of the onboarding "Test microphone" recording.
// Shorter than the full Microphone page's test (10s) because the wizard
// only needs enough audio to show a live input level — the user hasn't
// read the full instructions yet.
export const ONBOARDING_MIC_TEST_DURATION_SEC = 5;
