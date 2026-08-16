/**
 * Regression guard for the page-level infinite render loop class of bug
 * that OOM'd a vitest worker during the axe-core scans (FATAL heap
 * OOM, whole suite died at ~356 files).
 *
 * Root cause (fixed in `pages/onboarding/hooks/useOnboardingWizard.ts`
 * via the `callRef` mirror): a test mock (or future code) that hands
 * out a FRESH `call` identity on every render re-fires any effect
 * listing `call` in its deps — each run re-runs init()
 * (onboarding_start + get_config + onboarding_get_microphones +
 * onboarding_get_hotkey_presets + onboarding_get_model_options) and
 * stores fresh state → render → new `call` → … → unbounded render loop
 * until the heap is exhausted.
 *
 * The harness below (shared `renderLoopGuard` helper) drives the page
 * with the SAME worst-case mock shape — a NEW `call` per render — and
 * asserts the page still settles: the init() load fires EXACTLY once
 * per command and the committed render count stays bounded. If future
 * code puts an unstable value in an effect dep (or re-introduces `call`
 * directly), the load re-fires and/or the render count explodes and
 * this test fails fast — instead of the worker OOMing.
 */

import { makeConfig } from "@/__tests__/helpers/fixtures";
import {
	type GuardCommand,
	renderLoopGuard,
} from "@/__tests__/helpers/renderLoopGuard";

const commands: GuardCommand[] = [
	// Wizard starts on the Welcome step — no permission probe.
	{
		name: "onboarding_start",
		response: { step: 0, total_steps: 6, step_name: "Welcome" },
	},
	{ name: "get_config", response: makeConfig({}) },
	{ name: "onboarding_get_microphones", response: { microphones: [] } },
	{ name: "onboarding_get_hotkey_presets", response: { presets: ["F2"] } },
	{ name: "onboarding_get_model_options", response: { models: [] } },
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
