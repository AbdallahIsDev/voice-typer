// Fix 14: localized step title for the sr-only <h1>.
// 4-step essentials flow (2026-09-14): Welcome → Consent → Model →
// Hotkey. The Microphone / Permissions / Done steps were removed; the
// final Hotkey step's Continue acts as Finish (apply + complete).
export const STEP_TITLE_KEY: Record<string, string> = {
	Welcome: "onboarding.welcomeTitle",
	Consent: "onboarding.consentTitle",
	Model: "onboarding.modelTitle",
	Hotkey: "onboarding.hotkeyTitle",
};

// The final wizard step. Its primary button reads "Get started" and
// its click runs the apply flow (onboarding_apply) instead of
// advancing to a removed summary step.
export const FINAL_STEP_NAME = "Hotkey";

// Fix 17: renderer default must match `OnboardingController.selected_hotkey`
// The constant itself now lives in `components/hotkey/hotkey-utils.ts`
// (next to `formatHotkey` and `configHotkeyLabels`, so config-driven
// hotkey defaults and their label computation share one module);
// re-exported here so existing importers (App, Home, the wizard) and
// the lockstep comment history stay intact.
export { HOTKEY_DEFAULT } from "@/components/hotkey/hotkey-utils";

// Renderer default model, re-exported from its canonical home in
// `lib/utils/models.ts` (lib must never import from pages, inverted
// layering). Existing importers of this module keep compiling unchanged;
// new importers should import `MODEL_DEFAULT` from `@/lib/utils/models`
// directly. The value's lockstep contract with the backend's
// `DEFAULT_MODEL_SIZE` is documented at the canonical definition.
export { MODEL_DEFAULT } from "@/lib/utils/models";

// Fix 10: 5s → 10s, too short for users still reading the instructions.
export const TEST_HOTKEY_TIMEOUT_MS = 10_000;
export const HEADING_CLASS =
	"text-lg font-semibold text-foreground outline-none";
