import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	pythonMock,
	snackbarMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockCall, showSnack: mockShowSnack } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useSnackbar", () => snackbarMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());

import { LocalModelsPanel } from "@/components/models/LocalModelsPanel";
import { ModelStorageCard } from "@/components/models/ModelStorageCard";
import type { ModelStorageSummary } from "@/types/ipc";

const STORAGE: ModelStorageSummary = {
	used_bytes: 2_147_483_648,
	hub_path: "/data/app/huggingface/hub",
	config_dir: "/data/app",
};

beforeEach(() => {
	mockCall.mockReset();
	mockShowSnack.mockReset();
	// Default promise so mount-time hooks (download-queue hydration)
	// never see `undefined.then`.
	mockCall.mockResolvedValue({});
});

afterEach(() => {
	vi.clearAllMocks();
});

describe("ModelStorageCard", () => {
	it("renders nothing while storage is unknown (older backends)", () => {
		render(<ModelStorageCard storage={null} />);
		expect(screen.queryByTestId("model-storage-card")).toBeNull();
	});

	it("shows used bytes, hub path, hint, and the open-folder button", () => {
		render(<ModelStorageCard storage={STORAGE} />);
		const card = screen.getByTestId("model-storage-card");
		expect(card.textContent).toContain(STORAGE.hub_path);
		expect(card.textContent).toContain(
			"All downloaded models share this cache folder.",
		);
		expect(
			screen.getByRole("button", { name: "Open Data Folder" }),
		).toBeTruthy();
	});

	it("opens the data folder via the open_data_folder IPC (silent on success)", async () => {
		mockCall.mockResolvedValue({ opened: true, path: "/data/app" });
		render(<ModelStorageCard storage={STORAGE} />);
		fireEvent.click(screen.getByRole("button", { name: "Open Data Folder" }));
		await waitFor(() =>
			expect(mockCall).toHaveBeenCalledWith("open_data_folder"),
		);
		expect(mockShowSnack).not.toHaveBeenCalled();
	});

	it("snacks on opened:false without leaking backend detail", async () => {
		mockCall.mockResolvedValue({
			opened: false,
			path: "/data/app",
			reason: "not_found",
		});
		render(<ModelStorageCard storage={STORAGE} />);
		fireEvent.click(screen.getByRole("button", { name: "Open Data Folder" }));
		await waitFor(() =>
			expect(mockShowSnack).toHaveBeenCalledWith("Unknown error", "error"),
		);
	});

	it("snacks with the error message when the IPC rejects", async () => {
		mockCall.mockRejectedValue(new Error("boom"));
		render(<ModelStorageCard storage={STORAGE} />);
		fireEvent.click(screen.getByRole("button", { name: "Open Data Folder" }));
		await waitFor(() =>
			expect(mockShowSnack).toHaveBeenCalledWith(
				expect.stringContaining("boom"),
				"error",
			),
		);
	});
});

describe("LocalModelsPanel storage wiring", () => {
	const noop = () => {};
	const basePanelProps = {
		modelFamilies: [],
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
		onSelectModel: noop,
		onDownloadModel: noop,
		onDeleteModel: noop,
		onTogglePause: noop,
		onCancelDownload: noop,
		diskInfo: null,
		modelsFolderSupported: false,
		onOpenModelsFolder: noop,
	};

	it("shows the storage card when storage is provided", () => {
		render(<LocalModelsPanel {...basePanelProps} storage={STORAGE} />);
		expect(screen.getByTestId("model-storage-card")).toBeTruthy();
	});

	it("hides the storage card when storage is unknown", () => {
		render(<LocalModelsPanel {...basePanelProps} storage={null} />);
		expect(screen.queryByTestId("model-storage-card")).toBeNull();
	});
});
