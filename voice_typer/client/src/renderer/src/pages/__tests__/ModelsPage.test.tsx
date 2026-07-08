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
 */

import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// ── Module-level mocks (hoisted by vitest) ────────────────────────────
//
// IMPORTANT: vi.mock() factories are HOISTED by vitest and execute before
// any module-level const/let declarations.  Use vi.hoisted() for factory
// dependencies so they're available when the factory runs.

const { mockCall, showSnack } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	showSnack: vi.fn(),
}));

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({
		children,
		icon,
		...props
	}: {
		children?: React.ReactNode;
		icon?: { name?: string };
	}) => (
		<span data-testid="hugeicon" data-name={icon?.name} {...props}>
			{children}
		</span>
	),
}));

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		Alert02Icon: make("Alert02Icon"),
		Delete01Icon: make("Delete01Icon"),
		Download01Icon: make("Download01Icon"),
		Folder02Icon: make("Folder02Icon"),
		PauseIcon: make("PauseIcon"),
		PlayIcon: make("PlayIcon"),
		Shield01Icon: make("Shield01Icon"),
		SparklesIcon: make("SparklesIcon"),
		Tick02Icon: make("Tick02Icon"),
		ZapIcon: make("ZapIcon"),
	};
});

// Mock the Python IPC bridge — mockCall is created via vi.hoisted() so
// it's defined when this hoisted factory runs.
vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: () => {},
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({
		showSnack,
		Snackbar: () => null,
	}),
}));

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

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function t(key: string): string {
	return EN_KEYS.get(key) ?? key;
}

// ── Mock config returned by get_config IPC call ────────────────────────

const MOCK_CONFIG = {
	asr_backend: "whisper",
	model_size: "small.en",
	huggingface_consent: true,
	openai_api_key: "",
	groq_api_key: "",
	deepgram_api_key: "",
	cloud_openai_consent: false,
	cloud_groq_consent: false,
	cloud_deepgram_consent: false,
};

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
	delete (window as Record<string, unknown>).window_;
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

		render(<ModelsPage />);

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

	it("opens the Electron folder dialog when clicked", async () => {
		mockDialogResult({ canceled: false, path: "/tmp/models" });

		await renderPage();

		const button = screen.getByRole("button", {
			name: t("models.import.title"),
		});
		fireEvent.click(button);

		await waitFor(() => {
			expect((window as Record<string, unknown>).window_).toBeDefined();
			const api = (window as Record<string, unknown>).window_ as {
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
			imported: ["tiny.en"],
			found: ["tiny.en"],
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
					imported: ["tiny.en", "small.en"],
					found: ["tiny.en", "small.en"],
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
				expect.stringContaining("Imported 2 model(s)"),
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
					found: ["tiny.en", "small.en"],
					errors: [
						{ model: "tiny.en", error: "Disk full" },
						{ model: "small.en", error: "Read-only filesystem" },
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
					imported: ["tiny.en"],
					found: ["tiny.en"],
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
