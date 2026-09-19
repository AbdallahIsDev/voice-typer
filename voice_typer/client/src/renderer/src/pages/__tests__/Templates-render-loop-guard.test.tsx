import {
	type GuardCommand,
	renderLoopGuard,
} from "@/__tests__/helpers/renderLoopGuard";

const commands: GuardCommand[] = [
	{ name: "get_templates", response: [] },
	// The one-time localStorage→backend migration must NOT fire when
	// localStorage is empty and the backend returned an empty list.
	{ name: "save_templates", response: {}, expected: 0 },
];

renderLoopGuard({
	id: "templates",
	page: () => import("@/pages/Templates"),
	commands,
	// The empty-state settles in place of the loading spinner once the
	// initial load lands.
	settle: (s) => s.getByText("No templates yet") != null,
});
