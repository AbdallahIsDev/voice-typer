/**
 * Tests for the Models page —  (cancel-download state reset).
 *
 * Scenario under test: clicking the Cancel button in the download
 * progress bar fires `handleCancelDownload`, which previously:
 *   1. Awaited `cancel_model_download` IPC.
 *   2. Showed a "cancelled" snackbar.
 *   3. BUT did NOT reset the local `downloadingModel` /
 *      `downloadProgress` / `isPaused` state — the model card kept
 *      showing the progress bar / Pause / Cancel buttons until either
 *      the backend pushed a terminal `download_progress` event or the
 *      user navigated away. If the backend's cancel ack raced with
 *      the WS frame (or the frame was dropped), the UI stayed stuck
 *      mid-download indefinitely.
 *
 * The fix calls `setDownloadingModel(null)` + `resetProgress()` in
 * BOTH the success and catch branches of `handleCancelDownload` so
 * the UI reflects the user's intent to cancel regardless of whether
 * the IPC succeeded.
 *
 * The test seeds a pending download (download_model never resolves),
 * clicks the Cancel button, asserts `cancel_model_download` IPC was
 * called + the cancelled snackbar fired + the Cancel button itself
 * is GONE from the DOM (only possible if the optimistic state reset
 * ran synchronously after the IPC resolved).
 *
 * A second test verifies the catch-branch reset: when
 * `cancel_model_download` REJECTS, the Cancel button must still
 * disappear (because the user has signalled intent to cancel — the
 * UI must not stay stuck mid-download).
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module.
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	modelsConfigMock,
	pythonMock,
	snackbarMock,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockCall, showSnack, mockToastError } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock({ noopEvent: true }));
vi.mock("@/hooks/useSnackbar", () => snackbarMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock({ spreadProps: true }));
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock({ errorTo: "mockToastError" }));

import en from "@/i18n/translations/en.json";
import ModelsPage from "@/pages/Models";

function flattenKeys(
	obj: Record<string, unknown>,
	prefix = "",
): Map<string, string> {
	const result = new Map<string, string>();
	for (const [key, value] of Object.entries(obj)) {
		const fullKey = prefix ? `${prefix}.${key}` : key;
		if (typeof value === "object" && value !== null) {
			const nested = flattenKeys(value as Record<string, unknown>, fullKey);
			for (const [k, v] of nested) result.set(k, v);
		} else if (typeof value === "string") {
			result.set(fullKey, value);
		}
	}
	return result;
}

const EN_KEYS = flattenKeys(en as never as Record<string, unknown>);
function t(key: string): string {
	return EN_KEYS.get(key) ?? key;
}

const MOCK_CONFIG = modelsConfigMock();

async function renderPage() {
	mockCall.mockImplementation((type: string) => {
		if (type === "get_config") return Promise.resolve(MOCK_CONFIG);
		if (type === "get_model_status") return Promise.resolve({});
		if (type === "get_model_catalog") return Promise.resolve({ models: [] });
		return Promise.resolve(MOCK_CONFIG);
	});
	render(<ModelsPage />);
	await waitFor(() => {
		expect(screen.queryByRole("heading", { name: /Models/i })).toBeTruthy();
	});
}

describe("ModelsPage — NH-29 cancel-download state reset", () => {
	beforeEach(() => {
		mockCall.mockReset();
		showSnack.mockClear();
		mockToastError.mockClear();
	});

	afterEach(() => {
		cleanup();
	});

	it("resets local download state on cancel-model-download success (UI not stuck)", async () => {
		// 1. Render the page; the "Download tiny.en" button is visible
		//    (tiny.en is not active — small.en is — and not downloaded).
		await renderPage();
		const downloadButton = screen.getByRole("button", {
			name: t("models.card.downloadAria").replace("{name}", "large-v3-turbo"),
		});

		// 2. Start a download on tiny.en. download_model never resolves
		//    so the only way the UI can leave the "downloading" state
		//    is if the user clicks Cancel and the cancel handler resets
		//    state optimistically.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MOCK_CONFIG);
			if (type === "get_model_status") return Promise.resolve({});
			if (type === "get_model_catalog") return Promise.resolve({ models: [] });
			if (type === "download_model") return new Promise(() => {});
			if (type === "cancel_model_download") return Promise.resolve({});
			return Promise.resolve(MOCK_CONFIG);
		});
		fireEvent.click(downloadButton);

		// 3. Wait for the Cancel button to appear in the download
		//    progress bar (proves the download started and the UI is
		//    in the "downloading" state).
		const cancelButton = await waitFor(() =>
			screen.getByRole("button", {
				name: t("models.download.cancelAria"),
			}),
		);
		expect(cancelButton).toBeTruthy();

		// 4. Click Cancel.
		fireEvent.click(cancelButton);

		// 5. cancel_model_download IPC was called.
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("cancel_model_download");
		});

		// 6. The cancelled snackbar fired.
		await waitFor(() => {
			expect(showSnack).toHaveBeenCalledWith(
				t("models.snack.cancelled"),
				"warning",
			);
		});

		// 7. The Cancel button is GONE from the DOM — only possible if
		//    `setDownloadingModel(null)` + `resetProgress()` ran after
		//the IPC resolved. Before  the UI would have stayed
		//    stuck mid-download until the backend's terminal
		//    download_progress event arrived.
		await waitFor(() => {
			expect(
				screen.queryByRole("button", {
					name: t("models.download.cancelAria"),
				}),
			).toBeNull();
		});
	});

	it("resets local download state even when cancel_model_download IPC fails (intent honoured)", async () => {
		// Seed a pending download, then make cancel_model_download REJECT.
		// The catch branch must still clear local state so the user is
		// not stuck looking at a half-finished progress bar.
		await renderPage();
		const downloadButton = screen.getByRole("button", {
			name: t("models.card.downloadAria").replace("{name}", "large-v3-turbo"),
		});

		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MOCK_CONFIG);
			if (type === "get_model_status") return Promise.resolve({});
			if (type === "get_model_catalog") return Promise.resolve({ models: [] });
			if (type === "download_model") return new Promise(() => {});
			if (type === "cancel_model_download") {
				return Promise.reject(new Error("backend unreachable"));
			}
			return Promise.resolve(MOCK_CONFIG);
		});
		fireEvent.click(downloadButton);

		// Cancel button appears (download started).
		const cancelButton = await waitFor(() =>
			screen.getByRole("button", {
				name: t("models.download.cancelAria"),
			}),
		);
		fireEvent.click(cancelButton);

		// cancel_model_download IPC was called.
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("cancel_model_download");
		});

		// The cancelFailed snackbar fired.
		await waitFor(() => {
			expect(showSnack).toHaveBeenCalledWith(
				expect.stringContaining(
					t("models.snack.cancelFailed").split("{")[0] ?? "",
				),
				"error",
			);
		});

		// Despite the IPC failure, the Cancel button is GONE — the
		// user has signalled intent to cancel and the UI must reflect
		//that. Before  the UI stayed stuck mid-download even
		// after the user clicked Cancel, because the catch branch did
		// not reset local state.
		await waitFor(() => {
			expect(
				screen.queryByRole("button", {
					name: t("models.download.cancelAria"),
				}),
			).toBeNull();
		});
	});
});
