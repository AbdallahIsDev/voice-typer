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

import { DiagnosticsSettingsSection } from "@/components/settings/DiagnosticsSettingsSection";

function mockBackend() {
	mockCall.mockImplementation(async (cmd: string) => {
		if (cmd === "get_status")
			return { status: "idle", config_dir: "/data/app", loaded_via: "" };
		if (cmd === "get_config") return { model_size: "", device: "" };
		if (cmd === "get_model_status") return {};
		throw new Error(`unexpected command: ${cmd}`);
	});
}

beforeEach(() => {
	mockCall.mockReset();
	mockShowSnack.mockReset();
	mockBackend();
});

afterEach(() => {
	vi.clearAllMocks();
});

describe("DiagnosticsSettingsSection, Open Data Folder button", () => {
	it("renders alongside Copy Diagnostics", async () => {
		render(<DiagnosticsSettingsSection isVisible={() => true} />);
		await waitFor(() =>
			expect(screen.getByText("Copy diagnostics")).toBeTruthy(),
		);
		expect(
			screen.getByRole("button", { name: "Open Data Folder" }),
		).toBeTruthy();
	});

	it("opens the data folder via the open_data_folder IPC (silent on success)", async () => {
		mockCall.mockImplementation(async (cmd: string) => {
			if (cmd === "open_data_folder")
				return { opened: true, path: "/data/app" };
			if (cmd === "get_status")
				return { status: "idle", config_dir: "/data/app", loaded_via: "" };
			if (cmd === "get_config") return { model_size: "", device: "" };
			if (cmd === "get_model_status") return {};
			throw new Error(`unexpected command: ${cmd}`);
		});
		render(<DiagnosticsSettingsSection isVisible={() => true} />);
		fireEvent.click(screen.getByRole("button", { name: "Open Data Folder" }));
		await waitFor(() =>
			expect(mockCall).toHaveBeenCalledWith("open_data_folder"),
		);
		expect(mockShowSnack).not.toHaveBeenCalled();
	});

	it("snacks when the IPC rejects", async () => {
		mockCall.mockImplementation(async (cmd: string) => {
			if (cmd === "open_data_folder") throw new Error("no manager");
			if (cmd === "get_status")
				return { status: "idle", config_dir: "/data/app", loaded_via: "" };
			if (cmd === "get_config") return { model_size: "", device: "" };
			if (cmd === "get_model_status") return {};
			throw new Error(`unexpected command: ${cmd}`);
		});
		render(<DiagnosticsSettingsSection isVisible={() => true} />);
		fireEvent.click(screen.getByRole("button", { name: "Open Data Folder" }));
		await waitFor(() =>
			expect(mockShowSnack).toHaveBeenCalledWith(
				expect.stringContaining("no manager"),
				"error",
			),
		);
	});
});
