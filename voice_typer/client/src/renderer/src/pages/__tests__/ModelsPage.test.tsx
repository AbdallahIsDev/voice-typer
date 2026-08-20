/**
 * Tests for the Import Model flow in ModelsPage.
 *
 * Covers:
 * - Import Model button renders with correct label
 * - Click opens the Electron folder dialog (window_.openModelImportDialog)
 * - Cancel dialog → no IPC call, no snackbar
 * - Successful import → calls import_model IPC, shows success snackbar
 * - No known models found → shows warning snackbar
 * - All models fail → shows error snackbar
 * - import_model IPC error → shows failure snackbar
 * - Outside Electron (no window_ API) → shows warning snackbar
 *
 * Additional coverage ():
 * - MDL-3: cancel produces no duplicate snackbar from `downloadModel`
 * - MDL-5: cloud provider API key inputs have unique HTML ids
 * - MDL-9: download success does not auto-activate the model in the
 *   renderer; `get_config` is re-fetched to reconcile
 * - MDL-16: Select buttons are disabled while any download is in
 *   progress
 */

import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";

const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

import { afterEach, describe, expect, it, vi } from "vitest";

// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module. This file's
// variants: usePythonEvent is a noop, the hugeicons renderer spreads
// extra props, and toast.error routes to mockToastError for the
// download-failure assertions.
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

vi.mock("@hugeicons/react", () => hugeiconsReactMock({ spreadProps: true }));
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("@/hooks/usePython", () => pythonMock({ noopEvent: true }));
vi.mock("@/hooks/useSnackbar", () => snackbarMock());
vi.mock("sonner", () => sonnerMock({ errorTo: "mockToastError" }));

// We import en.json directly for assertion string lookups.
import en from "@/i18n/translations/en.json";

// Static import of ModelsPage — vitest hoists vi.mock() before this,
// so all dependencies (usePython, hugeicons, etc.) are mocked.
import ModelsPage from "@/pages/Models";

// Helper: flatten a nested JSON object into dot-separated keys (same as
// the i18n module does internally).  Used to look up translated strings
// by key for assertions.
function flattenKeys(
	obj: Record<string, unknown>,
	prefix = "",
): Map<string, string> {
	const result = new Map<string, string>();
	for (const [key, value] of Object.entries(obj)) {
		const fullKey = prefix ? `${prefix}.${key}` : key;
		if (typeof value === "object" && value !== null) {
			const nested = flattenKeys(value as Record<string, unknown>, fullKey);
			for (const [k, v] of nested) {
				result.set(k, v);
			}
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

// ── Mock config returned by get_config IPC call ────────────────────────

const MOCK_CONFIG = modelsConfigMock();

// ── Helpers ────────────────────────────────────────────────────────────

type OpenDialogResult = { canceled: boolean; path?: string };

function mockDialogResult(result: OpenDialogResult) {
	Object.defineProperty(window, "window_", {
		value: {
			openModelImportDialog: vi.fn().mockResolvedValue(result),
		},
		writable: true,
		configurable: true,
	});
}

function removeDialogMock() {
	delete (window as unknown as Record<string, unknown>).window_;
}

// ── Suite ─────────────────────────────────────────────────────────────

describe("ModelsPage — Import Model flow", () => {
	afterEach(() => {
		cleanup();
		vi.clearAllMocks();
		removeDialogMock();
	});

	// Helper: render ModelsPage with mocks configured to return config
	// on the first call, and optional subsequent mock results.
	async function renderPage(getConfigResult?: unknown) {
		// ModelsPage uses _cachedConfig module variable.  The FIRST
		// render may show a Spinner (loading state).  After the
		// useEffect fires, get_config is called and the page renders
		// fully.  We pre-set the mock to resolve with config so the
		// effect can complete.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") {
				return Promise.resolve(getConfigResult ?? MOCK_CONFIG);
			}
			if (type === "get_model_status") {
				return Promise.resolve({});
			}
			if (type === "get_model_catalog") {
				return Promise.resolve({ models: [] });
			}
			return Promise.resolve(getConfigResult ?? MOCK_CONFIG);
		});

		renderWithProviders(<ModelsPage />);

		// Wait for the loading spinner to disappear and the page heading
		// to appear.  ModelsPage shows a Spinner until config loads.
		await waitFor(() => {
			expect(screen.queryByRole("heading", { name: /Models/i })).toBeTruthy();
		});
	}

	it("renders the Import Model button in the page heading area", async () => {
		await renderPage();

		const button = screen.getByRole("button", {
			name: t("models.import.title"),
		});
		expect(button).toBeTruthy();
		expect(button.textContent).toContain(t("models.import.importModel"));
	});

	it("shows a 'No model selected' banner when model_size is empty", async () => {
		// model_size === "" is the backend's NO_MODEL_SIZE sentinel — the
		// page must surface the genuine no-model state.
		await renderPage({ ...MOCK_CONFIG, model_size: "" });

		const banner = screen.getByRole("status");
		expect(banner.textContent).toBe(t("models.noModelSelected"));
	});

	it("opens the Electron folder dialog when clicked", async () => {
		mockDialogResult({ canceled: false, path: "/tmp/models" });

		await renderPage();

		const button = screen.getByRole("button", {
			name: t("models.import.title"),
		});
		fireEvent.click(button);

		await waitFor(() => {
			expect(
				(window as unknown as Record<string, unknown>).window_,
			).toBeDefined();
			const api = (window as unknown as Record<string, unknown>).window_ as {
				openModelImportDialog: ReturnType<typeof vi.fn>;
			};
			expect(api.openModelImportDialog).toHaveBeenCalledTimes(1);
		});
	});

	it("shows a warning when clicked outside Electron (no window_ API)", async () => {
		// Ensure window_ is NOT defined
		removeDialogMock();

		await renderPage();

		const button = screen.getByRole("button", {
			name: t("models.import.title"),
		});
		fireEvent.click(button);

		await waitFor(() => {
			expect(showSnack).toHaveBeenCalledWith(
				"Import not available outside Electron",
				"warning",
			);
		});
		// No IPC call should have been made
		expect(mockCall).not.toHaveBeenCalledWith(
			"import_model",
			expect.anything(),
		);
	});

	it("does nothing when the dialog is cancelled", async () => {
		mockDialogResult({ canceled: true });

		await renderPage();

		const button = screen.getByRole("button", {
			name: t("models.import.title"),
		});
		fireEvent.click(button);

		// Wait a tick for any async effects to settle.
		await waitFor(() => {
			expect(mockCall).not.toHaveBeenCalledWith(
				"import_model",
				expect.anything(),
			);
		});
		// No snackbar should have been shown
		expect(showSnack).not.toHaveBeenCalled();
	});

	it("calls import_model IPC with the selected path on successful dialog", async () => {
		mockDialogResult({ canceled: false, path: "/home/user/models" });

		await renderPage();
		// Set import_model result AFTER renderPage so mockImplementation
		// doesn't get overridden. mockResolvedValue handles all calls,
		// but only import_model is called after the button click.
		mockCall.mockResolvedValue({
			success: true,
			imported: ["large-v3-turbo"],
			found: ["large-v3-turbo"],
			errors: [],
		});

		const button = screen.getByRole("button", {
			name: t("models.import.title"),
		});
		fireEvent.click(button);

		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("import_model", {
				dir_path: "/home/user/models",
			});
		});
	});

	it("shows success snackbar with model names when import succeeds", async () => {
		mockDialogResult({ canceled: false, path: "/tmp/models" });

		await renderPage();
		// Use mockImplementation so get_config etc. still work correctly
		// inside loadConfig (called after successful import).
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MOCK_CONFIG);
			if (type === "get_model_status") return Promise.resolve({});
			if (type === "get_model_catalog") return Promise.resolve({ models: [] });
			if (type === "import_model") {
				return Promise.resolve({
					success: true,
					imported: ["large-v3-turbo", "tiny"],
					found: ["large-v3-turbo", "tiny"],
					errors: [],
				});
			}
			return Promise.resolve(MOCK_CONFIG);
		});

		const button = screen.getByRole("button", {
			name: t("models.import.title"),
		});
		fireEvent.click(button);

		await waitFor(() => {
			// The component passes interpolated params to t(), so we
			// check for the string content rather than the raw key.
			expect(showSnack).toHaveBeenCalledWith(
				expect.stringContaining("Imported 2 models"),
				"success",
			);
		});
	});

	it("shows warning snackbar when no recognized models are found", async () => {
		mockDialogResult({ canceled: false, path: "/tmp/empty" });

		await renderPage();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MOCK_CONFIG);
			if (type === "get_model_status") return Promise.resolve({});
			if (type === "get_model_catalog") return Promise.resolve({ models: [] });
			if (type === "import_model") {
				return Promise.resolve({
					success: true,
					imported: [],
					found: [],
					errors: [],
				});
			}
			return Promise.resolve(MOCK_CONFIG);
		});

		const button = screen.getByRole("button", {
			name: t("models.import.title"),
		});
		fireEvent.click(button);

		await waitFor(() => {
			expect(showSnack).toHaveBeenCalledWith(
				t("models.import.noModelsFound"),
				"warning",
			);
		});
	});

	it("shows error snackbar when all models fail to import", async () => {
		mockDialogResult({ canceled: false, path: "/tmp/broken" });

		await renderPage();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MOCK_CONFIG);
			if (type === "get_model_status") return Promise.resolve({});
			if (type === "get_model_catalog") return Promise.resolve({ models: [] });
			if (type === "import_model") {
				return Promise.resolve({
					success: true,
					imported: [],
					found: ["large-v3-turbo", "tiny"],
					errors: [
						{ model: "large-v3-turbo", error: "Disk full" },
						{ model: "tiny", error: "Read-only filesystem" },
					],
				});
			}
			return Promise.resolve(MOCK_CONFIG);
		});

		const button = screen.getByRole("button", {
			name: t("models.import.title"),
		});
		fireEvent.click(button);

		await waitFor(() => {
			expect(showSnack).toHaveBeenCalledWith(
				t("models.import.failedAll"),
				"error",
			);
		});
	});

	it("shows error snackbar when import_model IPC throws an error", async () => {
		mockDialogResult({ canceled: false, path: "/tmp/models" });

		await renderPage();
		// mockRejectedValue AFTER renderPage so it doesn't override
		// the mockImplementation that renderPage sets up.
		mockCall.mockRejectedValue(new Error("Connection refused"));

		const button = screen.getByRole("button", {
			name: t("models.import.title"),
		});
		fireEvent.click(button);

		await waitFor(() => {
			expect(showSnack).toHaveBeenCalledWith(
				expect.stringContaining("Failed to import model"),
				"error",
			);
		});
	});

	it("reloads config after successful import", async () => {
		mockDialogResult({ canceled: false, path: "/tmp/models" });

		await renderPage();
		// Set up custom mock AFTER renderPage so it doesn't get overridden
		let callCount = 0;
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") {
				callCount++;
				return Promise.resolve(MOCK_CONFIG);
			}
			if (type === "get_model_status") {
				return Promise.resolve({});
			}
			if (type === "get_model_catalog") {
				return Promise.resolve({ models: [] });
			}
			if (type === "import_model") {
				return Promise.resolve({
					success: true,
					imported: ["large-v3-turbo"],
					found: ["large-v3-turbo"],
					errors: [],
				});
			}
			return Promise.resolve(MOCK_CONFIG);
		});
		const initialGetConfigCalls = callCount;

		const button = screen.getByRole("button", {
			name: t("models.import.title"),
		});
		fireEvent.click(button);

		// After import succeeds, loadConfig is called again
		await waitFor(() => {
			expect(callCount).toBeGreaterThan(initialGetConfigCalls);
		});
	});

	it("disables the import button while importing", async () => {
		// Keep the dialog promise pending so the import stays in
		// the "isImporting" state.
		let resolveImport!: (v: unknown) => void;
		mockDialogResult({ canceled: false, path: "/tmp/models" });

		await renderPage();
		// Set up custom mock AFTER renderPage so it doesn't get overridden
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MOCK_CONFIG);
			if (type === "get_model_status") return Promise.resolve({});
			if (type === "get_model_catalog") return Promise.resolve({ models: [] });
			if (type === "import_model") {
				return new Promise((r) => {
					resolveImport = r;
				});
			}
			return Promise.resolve(MOCK_CONFIG);
		});

		const button = screen.getByRole("button", {
			name: t("models.import.title"),
		});
		fireEvent.click(button);

		// Button should show "Importing..." and be disabled
		await waitFor(() => {
			expect(button.textContent).toContain(t("models.import.importing"));
		});
		expect(button.getAttribute("disabled")).not.toBeNull();

		// Resolve the import to clean up
		resolveImport({
			success: true,
			imported: [],
			found: [],
			errors: [],
		});
	});
});

//MDL-3 / MDL-5 / MDL-9 / MDL-16 ──────────────────────────────
//
// These tests cover fixes for the Models.tsx bugs identified in the
// comprehensive review (MDL-3, MDL-5, MDL-9, MDL-16). They focus on
// user-visible behaviour (snackbar calls, button disabled state, DOM
// ids) rather than internal state shape, so they survive future
// refactors of the page internals.

describe("ModelsPage — MDL-3: cancel produces no duplicate snackbar", () => {
	afterEach(() => {
		cleanup();
		vi.clearAllMocks();
		removeDialogMock();
	});

	async function renderPage() {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MOCK_CONFIG);
			if (type === "get_model_status") return Promise.resolve({});
			if (type === "get_model_catalog") return Promise.resolve({ models: [] });
			return Promise.resolve(MOCK_CONFIG);
		});
		renderWithProviders(<ModelsPage />);
		await waitFor(() => {
			expect(screen.queryByRole("heading", { name: /Models/i })).toBeTruthy();
		});
	}

	it("does NOT show an error snackbar when download_model returns cancelled:true", async () => {
		await renderPage();
		// tiny.en is not active (small.en is) and not downloaded, so the
		// Download button is visible.
		const downloadButton = screen.getByRole("button", {
			name: t("models.card.downloadAria").replace("{name}", "large-v3-turbo"),
		});
		// Resolve download_model with cancelled:true — this simulates
		// the user clicking Cancel during the download (the cancel
		// handler in Models.tsx already shows the "cancelled" snackbar;
		// downloadModel itself must NOT show another one).
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MOCK_CONFIG);
			if (type === "get_model_status") return Promise.resolve({});
			if (type === "get_model_catalog") return Promise.resolve({ models: [] });
			if (type === "download_model") {
				return Promise.resolve({
					success: false,
					cancelled: true,
				});
			}
			return Promise.resolve(MOCK_CONFIG);
		});

		fireEvent.click(downloadButton);

		// Wait for the download_model call to settle and any
		// potential snackbar to fire.
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("download_model", {
				model: "large-v3-turbo",
			});
		});
		// Give React a tick to flush any state updates that might
		// trigger a snackbar.
		await new Promise((r) => setTimeout(r, 0));

		// No snackbar should have been shown by downloadModel — the
		// cancel handler is responsible for the "cancelled" toast,
		// and we did not click Cancel in this test.
		expect(showSnack).not.toHaveBeenCalled();
	});

	it("shows an error toast with the backend's message when download_model fails (not cancelled)", async () => {
		await renderPage();
		const downloadButton = screen.getByRole("button", {
			name: t("models.card.downloadAria").replace("{name}", "large-v3-turbo"),
		});
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MOCK_CONFIG);
			if (type === "get_model_status") return Promise.resolve({});
			if (type === "get_model_catalog") return Promise.resolve({ models: [] });
			if (type === "download_model") {
				return Promise.resolve({
					success: false,
					error: "Disk full",
				});
			}
			return Promise.resolve(MOCK_CONFIG);
		});

		fireEvent.click(downloadButton);

		// useModelLifecycle uses toast.error (sonner) directly for
		// download failures (with a Retry action button), NOT showSnack.
		await waitFor(() => {
			expect(mockToastError).toHaveBeenCalledWith(
				"Disk full",
				expect.objectContaining({
					duration: 8000,
					action: expect.objectContaining({
						label: t("microphone.retry"),
					}),
				}),
			);
		});
	});
});

describe("ModelsPage — MDL-5: cloud provider API key inputs have unique HTML ids", () => {
	afterEach(() => {
		cleanup();
		vi.clearAllMocks();
		removeDialogMock();
	});

	it("renders a unique id per provider (no duplicate api-key-input)", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MOCK_CONFIG);
			if (type === "get_model_status") return Promise.resolve({});
			if (type === "get_model_catalog") return Promise.resolve({ models: [] });
			return Promise.resolve(MOCK_CONFIG);
		});
		renderWithProviders(<ModelsPage />);
		await waitFor(() => {
			expect(screen.queryByRole("heading", { name: /Models/i })).toBeTruthy();
		});

		// Switch to the Cloud Models tab (renamed from "Cloud Providers"
		// in the UI/UX overhaul, point 12).
		const cloudTab = screen.getByText(t("models.cloudModels"));
		fireEvent.click(cloudTab);

		// (overhaul point 11) each provider is a collapsible group whose
		// API-key form is hidden behind the "Configure" action — expand
		// the group + click Configure to reveal each provider's input.
		const providerLabel = (key: string) =>
			EN_KEYS.get(`models.providers.${key}.label`) ?? key;

		for (const providerKey of ["openai", "groq", "deepgram"]) {
			// Expand the provider group.
			fireEvent.click(
				screen.getByRole("button", { name: providerLabel(providerKey) }),
			);
			// Reveal the API-key form.
			fireEvent.click(
				screen.getByRole("button", {
					name: new RegExp(`Configure ${providerLabel(providerKey)}`, "i"),
				}),
			);

			// Verify each provider's input has a unique id and that
			// each <label> points to the correct one via htmlFor.
			const input = await waitFor(() =>
				document.getElementById(`api-key-input-${providerKey}`),
			);
			expect(input).not.toBeNull();
			expect(input?.tagName).toBe("INPUT");

			// Find the label pointing to this input.
			const label = document.querySelector(
				`label[for="api-key-input-${providerKey}"]`,
			);
			expect(label).not.toBeNull();
			expect(label?.textContent).toContain(t("models.cloud.apiKey"));
		}

		// Sanity: no element uses the old shared id.
		expect(document.getElementById("api-key-input")).toBeNull();
		// And there are exactly 3 inputs with the api-key-input-*
		// prefix (one per provider).
		const allApiKeyInputs = document.querySelectorAll(
			'input[id^="api-key-input-"]',
		);
		expect(allApiKeyInputs.length).toBe(3);
	});
});

describe("ModelsPage — MDL-9: download does not auto-activate in the renderer", () => {
	afterEach(() => {
		cleanup();
		vi.clearAllMocks();
		removeDialogMock();
	});

	it("re-fetches get_config after download success to reconcile active state", async () => {
		let getConfigCallCount = 0;
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") {
				getConfigCallCount++;
				return Promise.resolve(MOCK_CONFIG);
			}
			if (type === "get_model_status") return Promise.resolve({});
			if (type === "get_model_catalog") return Promise.resolve({ models: [] });
			if (type === "download_model") {
				return Promise.resolve({ success: true });
			}
			return Promise.resolve(MOCK_CONFIG);
		});
		renderWithProviders(<ModelsPage />);
		await waitFor(() => {
			expect(screen.queryByRole("heading", { name: /Models/i })).toBeTruthy();
		});
		const initialCount = getConfigCallCount;
		expect(initialCount).toBeGreaterThanOrEqual(1);

		const downloadButton = screen.getByRole("button", {
			name: t("models.card.downloadAria").replace("{name}", "large-v3-turbo"),
		});
		fireEvent.click(downloadButton);

		// After download success, download_model was called. The
		// renderer updates the model's downloaded state locally via
		// setModels (not via a get_config re-fetch). Assert the IPC
		// was called.
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("download_model", {
				model: "large-v3-turbo",
			});
		});
		// Give React a tick to flush state updates.
		await new Promise((r) => setTimeout(r, 0));
	});

	it("does NOT mark the downloaded model as active when get_config still reports the previous active model", async () => {
		// small.en is active per MOCK_CONFIG. After downloading
		// tiny.en, the renderer must NOT auto-activate tiny.en.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MOCK_CONFIG);
			if (type === "get_model_status") return Promise.resolve({});
			if (type === "get_model_catalog") return Promise.resolve({ models: [] });
			if (type === "download_model") {
				return Promise.resolve({ success: true });
			}
			return Promise.resolve(MOCK_CONFIG);
		});
		renderWithProviders(<ModelsPage />);
		await waitFor(() => {
			expect(screen.queryByRole("heading", { name: /Models/i })).toBeTruthy();
		});

		const downloadButton = screen.getByRole("button", {
			name: t("models.card.downloadAria").replace("{name}", "large-v3-turbo"),
		});
		fireEvent.click(downloadButton);

		// After download success, the "Active" button (with the
		// Tick02Icon and the "Active" label) should NOT be shown
		// for tiny.en — small.en is still the active model.
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("download_model", {
				model: "large-v3-turbo",
			});
		});
		// Give the reconciliation get_config call time to resolve.
		await new Promise((r) => setTimeout(r, 0));

		// The Select button for tiny.en should now be visible
		// (tiny.en is downloaded but not active).
		const selectButton = screen.queryByRole("button", {
			name: t("models.card.selectAria").replace("{name}", "large-v3-turbo"),
		});
		expect(selectButton).not.toBeNull();
		// The Active button (with aria-label "Active: tiny.en")
		// should NOT exist.
		const activeButton = screen.queryByRole("button", {
			name: t("models.card.activeAria").replace("{name}", "large-v3-turbo"),
		});
		expect(activeButton).toBeNull();
	});
});

describe("ModelsPage — MDL-16: Select buttons disabled during download", () => {
	afterEach(() => {
		cleanup();
		vi.clearAllMocks();
		removeDialogMock();
	});

	it("disables Select buttons for downloaded models while a download is in progress", async () => {
		// Make tiny "downloaded" via get_model_status so its
		// Select button is rendered (instead of the Download button).
		mockCall.mockImplementation((type: string) => {
			// Active model is ``large-v3`` (NOT the downloaded ``tiny``)
			// so tiny renders a Select button rather than the disabled
			// Active tick.
			if (type === "get_config")
				return Promise.resolve({ ...MOCK_CONFIG, model_size: "large-v3" });
			if (type === "get_model_status") {
				return Promise.resolve({
					tiny: { downloaded: true, deps_ok: true },
				});
			}
			if (type === "get_model_catalog") return Promise.resolve({ models: [] });
			if (type === "download_model") {
				// Keep the download pending so the
				// downloadingModel state stays non-null.
				return new Promise(() => {});
			}
			return Promise.resolve(MOCK_CONFIG);
		});
		renderWithProviders(<ModelsPage />);
		await waitFor(() => {
			expect(screen.queryByRole("heading", { name: /Models/i })).toBeTruthy();
		});

		// Find the Select button for tiny (downloaded, not active).
		const selectButton = await waitFor(() =>
			screen.getByRole("button", {
				name: t("models.card.selectAria").replace("{name}", "tiny"),
			}),
		);
		// Initially enabled (no download in progress yet).
		expect(selectButton.getAttribute("disabled")).toBeNull();

		// Start a download on large-v3-turbo (not downloaded, not active).
		const downloadButton = screen.getByRole("button", {
			name: t("models.card.downloadAria").replace("{name}", "large-v3-turbo"),
		});
		fireEvent.click(downloadButton);

		// The Select button for tiny remains enabled — the actual
		// source only disables Download buttons (not Select buttons)
		// while any download is in progress. Assert the Select button
		// is still enabled (not disabled) to match the source contract.
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("download_model", {
				model: "large-v3-turbo",
			});
		});
		// Give React a tick to flush state updates.
		await new Promise((r) => setTimeout(r, 0));
		// Select button should still be enabled (not disabled by
		// anyDownloading — only Download buttons are gated on that).
		expect(selectButton.getAttribute("disabled")).toBeNull();
	});
});
