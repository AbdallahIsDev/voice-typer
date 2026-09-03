/**
 * test: the LocalModelsPanel renders the localized descriptive
 * subtitle under the panel heading. The `models.localModelsDescription`
 * key exists in all 8 locales — this pins that the panel CONSUMES it.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

import { LocalModelsPanel } from "@/components/models/LocalModelsPanel";
import type { ModelFamily, ModelInfo } from "@/lib/utils/models";

const model: ModelInfo = {
	name: "tiny",
	family: "openai",
	repo_id: "openai/whisper-tiny",
	depsOk: true,
} as unknown as ModelInfo;

const families: ModelFamily[] = [
	{
		id: "openai",
		name: "OpenAI",
		variants: [model],
	},
] as unknown as ModelFamily[];

const baseProps = {
	modelFamilies: families,
	modelCatalog: {},
	selectingModel: null,
	downloadingModel: null,
	downloadProgress: 0,
	downloadStatus: "",
	isPaused: false,
	downloadedBytes: null,
	totalBytes: null,
	speedBps: null,
	etaSeconds: null,
	onSelectModel: () => {},
	onDownloadModel: () => {},
	onDeleteModel: () => {},
	onInstallDeps: () => {},
	onTogglePause: () => {},
	onCancelDownload: () => {},
	diskInfo: null,
	modelsFolderSupported: false,
	onOpenModelsFolder: () => {},
};

describe("LocalModelsPanel — localized description subtitle", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the localized description under the panel heading", () => {
		render(<LocalModelsPanel {...baseProps} />);
		const subtitle = screen.getByTestId("local-models-description");
		expect(subtitle).toBeTruthy();
		// The real en.json value for models.localModelsDescription.
		expect(subtitle.textContent).toBe(
			"Run Whisper models directly on your device. No internet required after download.",
		);
	});
});
