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

// Renderer default model — re-exported from its canonical home in
// `lib/utils/models.ts` (lib must never import from pages — inverted
// layering). Existing importers of this module keep compiling unchanged;
// new importers should import `MODEL_DEFAULT` from `@/lib/utils/models`
// directly. The value's lockstep contract with the backend's
// `DEFAULT_MODEL_SIZE` is documented at the canonical definition.
export { MODEL_DEFAULT } from "@/lib/utils/models";

// Fix 10: 5s → 10s — too short for users still reading the instructions.
export const TEST_HOTKEY_TIMEOUT_MS = 10_000;

export const HEADING_CLASS =
	"text-lg font-semibold text-(--text-primary) outline-none";
