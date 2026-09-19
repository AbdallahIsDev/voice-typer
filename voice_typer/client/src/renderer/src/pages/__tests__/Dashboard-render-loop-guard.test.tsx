import {
	type GuardCommand,
	renderLoopGuard,
} from "@/__tests__/helpers/renderLoopGuard";
import { modelsConfigMock } from "@/__tests__/helpers/stableMocks";

// The canonical minimal models-page config shape (one source of truth
// in helpers/stableMocks.tsx). The dashboard reads model/device/
// language for the stat cards + asr_backend for the share-stats
// summary; missing fields render as "Unknown", fine for a settling
// guard.
const MOCK_CONFIG = modelsConfigMock();

const commands: GuardCommand[] = [
	{ name: "get_config", response: MOCK_CONFIG },
	// One history record so totalCount > 0 and the analytics view (stat
	// cards + chart) renders instead of the first-run empty state.
	{
		name: "get_history",
		response: [
			{
				id: 1,
				timestamp: "2026-08-16T12:00:00Z",
				char_count: 10,
				word_count: 2,
				duration: 1.5,
				favorite: 0,
			},
		],
	},
	{ name: "get_history_count", response: { count: 1 } },
	{ name: "get_status", response: { config_dir: "" } },
	// Empty correction-usage snapshot, the corrections card renders an
	// empty state instead of blocking the page.
	{ name: "get_correction_usage", response: { version: 1, entries: {} } },
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
	id: "dashboard",
	page: () => import("@/pages/Dashboard"),
	commands,
	// The analytics view (with the stat cards) settles in place of the
	// skeleton once the first refresh lands.
	settle: (s) => s.getByText(/^Total Dictations/) != null,
});
