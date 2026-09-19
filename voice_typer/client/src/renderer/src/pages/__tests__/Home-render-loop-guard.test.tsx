import { makeConfig } from "@/__tests__/helpers/fixtures";
import {
	type GuardCommand,
	renderLoopGuard,
} from "@/__tests__/helpers/renderLoopGuard";

const commands: GuardCommand[] = [
	{ name: "get_config", response: makeConfig({}) },
	{
		name: "get_today_stats",
		response: { count: 1, chars: 10, word_count: 2, duration: 1.5 },
	},
	{ name: "get_history", response: [] },
];

renderLoopGuard({
	id: "home",
	page: () => import("@/pages/Home"),
	commands,
	// The record button rendered in place of the loading spinner, the
	// page actually settled into its real UI. The button is icon-only:
	// its label lives in aria-label/title (no visible text), so query by
	// accessible name.
	settle: (s) => s.getByRole("button", { name: "Start dictation" }) != null,
});
