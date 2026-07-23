// PVT-053 / EC-FIX-18: shared constants extracted from Onboarding.tsx.

export const DONE_STEP_NAME = "Done";

// Fix 14: localized step title for the sr-only <h1>.
export const STEP_TITLE_KEY: Record<string, string> = {
	Welcome: "onboarding.welcomeTitle",
	Microphone: "onboarding.micTitle",
	Permissions: "onboarding.permissionsTitle",
	Hotkey: "onboarding.hotkeyTitle",
	Model: "onboarding.modelTitle",
	Done: "onboarding.completeTitle",
};

// Fix 17: renderer default must match `OnboardingController.selected_hotkey`
// (`<caps_lock>`) — previously `<f2>`, which silently overrode the backend.
export const HOTKEY_DEFAULT = "<caps_lock>";

// Fix 10: 5s → 10s — too short for users still reading the instructions.
export const TEST_HOTKEY_TIMEOUT_MS = 10_000;

export const HEADING_CLASS =
	"mb-3 text-lg font-semibold text-(--text-primary) outline-none";
