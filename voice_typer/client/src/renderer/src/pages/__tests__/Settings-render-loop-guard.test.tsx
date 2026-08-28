/**
 * Regression guard for the page-level infinite render loop class of bug
 * that OOM'd a vitest worker during the axe-core scans (FATAL heap
 * OOM, whole suite died at ~356 files).
 *
 * Root cause (fixed in `components/settings/useSettingsConfig.ts` via
 * the `callRef` mirror + in `hooks/useTheme.ts`): a test mock (or
 * future code) that hands out a FRESH `call` identity on every render
 * re-fires any effect listing `call` in its deps — each run re-fetches
 * get_config (loadConfig) + the theme-singleton reload + the
 * keyboard-permission probe and stores fresh state → render → new
 * `call` → … → unbounded render loop until the heap is exhausted.
 *
 * The harness below (shared `renderLoopGuard` helper) drives the page
 * with the SAME worst-case mock shape — a NEW `call` per render — and
 * asserts the page still settles: the mount load fires EXACTLY `expected`
 * times per command and the committed render count stays bounded. If
 * future code puts an unstable value in an effect dep (or re-introduces
 * `call` directly), the load re-fires and/or the render count explodes
 * and this test fails fast — instead of the worker OOMing.
 */

import { makeConfig } from "@/__tests__/helpers/fixtures";
import {
	type GuardCommand,
	renderLoopGuard,
} from "@/__tests__/helpers/renderLoopGuard";

const commands: GuardCommand[] = [
	// get_config fires twice on mount: once from
	// useSettingsConfig.loadConfig and once from the useTheme singleton
	// reload (themeInitStarted initOnce guard).
	{ name: "get_config", response: makeConfig({}), expected: 2 },
	// KeyboardPermissionBanner's mount probe — granted, so the banner
	// stays hidden.
	{
		name: "onboarding_check_permissions",
		response: {
			platform: "windows",
			state: "granted",
			needed: false,
			instructions: null,
		},
	},
];

renderLoopGuard({
	id: "settings",
	page: () => import("@/pages/Settings"),
	commands,
	// The page renders its chrome (title) immediately; the
	// load-completion signal is the exactly-once counter assertion.
	settle: (s) => s.getByText("Settings") != null,
});
