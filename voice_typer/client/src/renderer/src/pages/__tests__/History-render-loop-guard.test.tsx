import {
	type GuardCommand,
	renderLoopGuard,
} from "@/__tests__/helpers/renderLoopGuard";

const commands: GuardCommand[] = [
	{ name: "get_history", response: [] },
	{
		name: "get_today_stats",
		response: { count: 0, chars: 0, word_count: 0, duration: 0 },
	},
];

renderLoopGuard({
	id: "history",
	page: () => import("@/pages/History"),
	commands,
	// The empty-state settles in place of the loading spinner once the
	// initial load lands.
	settle: (s) => s.getByText("No dictations yet") != null,
});
