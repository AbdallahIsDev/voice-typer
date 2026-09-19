import {
	type GuardCommand,
	renderLoopGuard,
} from "@/__tests__/helpers/renderLoopGuard";

/** Seed: 2 misspellings + 1 phrase correction (flat list, 3 rows). */
const seedData = {
	misspellings: { recieve: "receive", teh: "the" },
	phrase_corrections: [["i am going to", "I'm going to"]],
};

const commands: GuardCommand[] = [
	{ name: "get_vocabulary", response: seedData },
	// The usage snapshot is a single progressive-enhancement fetch.
	{ name: "get_correction_usage", response: { version: 1, entries: {} } },
];

renderLoopGuard({
	id: "vocabulary",
	page: () => import("@/pages/Vocabulary"),
	commands,
	// Initial load lands: the flat list renders.
	settle: (s) => s.getByText("recieve") != null,
	// This page settles at ~6 commits; pin tighter than the default.
	maxCommits: 15,
});
