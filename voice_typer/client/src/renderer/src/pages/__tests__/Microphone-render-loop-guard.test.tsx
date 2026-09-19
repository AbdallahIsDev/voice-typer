import { makeConfig } from "@/__tests__/helpers/fixtures";
import {
	type GuardCommand,
	renderLoopGuard,
} from "@/__tests__/helpers/renderLoopGuard";

const commands: GuardCommand[] = [
	{ name: "get_microphones", response: [] },
	{ name: "get_config", response: makeConfig({}) },
];

renderLoopGuard({
	id: "microphone",
	page: () => import("@/pages/Microphone"),
	commands,
	// The page heading settles in place of the loading spinner once the
	// initial load lands.
	settle: (s) => s.getByText("Microphone") != null,
});
