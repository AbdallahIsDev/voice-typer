import {
	type GuardCommand,
	renderLoopGuard,
} from "@/__tests__/helpers/renderLoopGuard";
import { modelsConfigMock } from "@/__tests__/helpers/stableMocks";

// The canonical minimal models-page config shape (one source of truth
// in helpers/stableMocks.tsx, shared with ModelsPage.test.tsx,
// ModelsPage-cancel-download-reset and the data-pages live-region guards).
const MOCK_CONFIG = modelsConfigMock();

const commands: GuardCommand[] = [
	{ name: "get_config", response: MOCK_CONFIG },
	{ name: "get_model_status", response: {} },
	{ name: "get_model_catalog", response: { models: [] } },
];

renderLoopGuard({
	id: "models",
	page: () => import("@/pages/Models"),
	commands,
	// The page heading settles in place of the loading spinner once the
	// initial load lands.
	settle: (s) => s.getByRole("heading", { name: /Models/i }) != null,
});
