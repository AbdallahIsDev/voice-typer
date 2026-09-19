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
	// KeyboardPermissionBanner's mount probe, granted, so the banner
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
