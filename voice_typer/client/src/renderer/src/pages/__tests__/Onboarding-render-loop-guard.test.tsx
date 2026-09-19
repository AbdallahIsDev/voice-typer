import { makeConfig } from "@/__tests__/helpers/fixtures";
import {
	type GuardCommand,
	renderLoopGuard,
} from "@/__tests__/helpers/renderLoopGuard";

const commands: GuardCommand[] = [
	// Wizard starts on the Welcome step (4-step essentials flow,
	// 2026-09-14), no permission probe.
	{
		name: "onboarding_start",
		response: { step: 0, total_steps: 4, step_name: "Welcome" },
	},
	{ name: "get_config", response: makeConfig({}) },
	// NOT probe microphones anymore (expected 0 pins the removal).
	{
		name: "onboarding_get_microphones",
		response: { microphones: [] },
		expected: 0,
	},
	{ name: "onboarding_get_hotkey_presets", response: { presets: ["F2"] } },
	{ name: "onboarding_get_model_options", response: { models: [] } },
	{ name: "get_model_catalog", response: { models: [] } },
];

renderLoopGuard({
	id: "onboarding",
	page: () => import("@/pages/Onboarding"),
	props: { onComplete: () => {} },
	commands,
	// The Welcome step settles in place of the loading spinner once
	// init() lands. The title renders in the sr-only step heading AND
	// the visible h2, so match any instance (getAllByText).
	settle: (s) => s.getAllByText(/Welcome to/).length > 0,
});
