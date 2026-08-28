/**
 * Tests for the Templates page —  (instant-delete optimisation).
 *
 * Scenario under test: clicking the trash icon on a template row fires
 * `instantDeleteTemplate`, which previously:
 *   1. Computed the post-delete `items` array.
 *   2. Awaited `saveTemplates(items, call)` (a 100-500ms IPC round-trip).
 *   3. Called `loadRows()` (another IPC round-trip) to refresh state.
 *   4. The React state only updated AFTER both round-trips — the
 *      deleted row stayed visible for the entire duration, which felt
 *      sluggish and could trigger duplicate-delete clicks.
 *
 * The fix mirrors useVocabulary's D2-FIX pattern: `setTemplates(toRows(items))`
 * is called BEFORE the await, so the row disappears from the UI instantly.
 * On IPC failure, the catch branch restores the pre-delete state from a
 * captured local variable before showing the error toast.
 *
 * The test mocks `save_templates` with a never-resolving promise so the
 * IPC never completes — the only way the row can disappear from the UI
 * is if the optimistic `setTemplates` ran. This is the strongest possible
 * regression assertion: if anyone reverts the optimistic update, the row
 * would still be present when the test asserts its absence (because the
 * await never resolved, so neither `loadRows()` nor the post-save
 * setState would have run).
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
	nextThemesMock,
	pythonMock,
	snackbarMock,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";
import { TooltipProvider } from "@/components/ui/tooltip";

const {
	mockCall,
	showSnack: mockShowSnack,
	toastSuccess,
	toastWarning,
} = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useSnackbar", () => snackbarMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

/** Two seeded templates so the page renders the list view (not EmptyState). */
const seedTemplates = {
	templates: [
		{ trigger: "hello", output: "Hello, world!", match_mode: "exact" },
		{ trigger: "brb", output: "be right back", match_mode: "contains" },
	],
};

/** TemplateListRow renders an InfoTooltip (Radix Tooltip) which throws
 *  without a TooltipProvider ancestor — the real App shell provides
 *  one, so tests mounting the page directly must too. */
const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

describe("Templates page — NH-28 instant-delete optimisation", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockShowSnack.mockReset();
		toastWarning.mockClear();
		toastSuccess.mockClear();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("removes the row from the UI BEFORE save_templates resolves (optimistic update)", async () => {
		// save_templates returns a NEVER-resolving promise. The only way the
		// row can disappear from the UI is if the optimistic setTemplates
		// ran synchronously BEFORE the await.
		let neverResolve: ((value: unknown) => void) | undefined;
		mockCall.mockImplementation((type: string) => {
			if (type === "get_templates") return Promise.resolve(seedTemplates);
			if (type === "save_templates") {
				return new Promise((resolve) => {
					neverResolve = resolve as (value: unknown) => void;
				});
			}
			return Promise.resolve({});
		});

		const { default: TemplatesPage } = await import("@/pages/Templates");
		renderWithProviders(<TemplatesPage />);

		// Wait for both seeded templates to render.
		await waitFor(() => {
			expect(screen.getByText("hello")).toBeTruthy();
		});
		expect(screen.getByText("brb")).toBeTruthy();
		expect(screen.getAllByText(/hello|brb/).length).toBe(2);

		// Click the trash icon on the "hello" template. The button's
		// aria-label is "Delete template: hello" (from the
		// templates.deleteAria template with {name} interpolated).
		fireEvent.click(screen.getByLabelText("Delete template: hello"));

		// After the click, "hello" should be GONE from the list — even
		// though save_templates has not resolved (neverResolve is still
		// undefined / un-called). This is only possible if the optimistic
		// setTemplates ran BEFORE the await. The "brb" template must
		// remain visible.
		await waitFor(() => {
			expect(screen.queryByText("hello")).toBeNull();
		});
		expect(screen.getByText("brb")).toBeTruthy();

		// Save was called once with the post-delete list (just brb).
		const saveCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "save_templates",
		);
		expect(saveCalls.length).toBe(1);
		const saveArg = saveCalls[0]?.[1] as
			| { templates?: Array<{ trigger: string }> }
			| undefined;
		expect(saveArg?.templates?.length).toBe(1);
		expect(saveArg?.templates?.[0]?.trigger).toBe("brb");

		// The undoable toast has NOT fired yet because save_templates is
		// still pending. This is the proof that the UI update was
		// optimistic: the row is gone, but the post-save toast hasn't
		// fired (because the save hasn't resolved).
		expect(toastWarning).not.toHaveBeenCalled();

		// Allow the pending save to resolve so test cleanup doesn't hang
		// on an unhandled promise.
		if (neverResolve) neverResolve(undefined);
	});

	it("restores the pre-delete list on save_templates failure (no UI desync)", async () => {
		// save_templates rejects. The optimistic update removes the row,
		// then the catch branch must restore the pre-delete state so the
		// UI matches the actual backend (which still has both templates).
		mockCall.mockImplementation((type: string) => {
			if (type === "get_templates") return Promise.resolve(seedTemplates);
			if (type === "save_templates") {
				return Promise.reject(new Error("backend write failed"));
			}
			return Promise.resolve({});
		});

		const { default: TemplatesPage } = await import("@/pages/Templates");
		renderWithProviders(<TemplatesPage />);

		await waitFor(() => {
			expect(screen.getByText("hello")).toBeTruthy();
		});
		expect(screen.getByText("brb")).toBeTruthy();

		// Click delete on "hello".
		fireEvent.click(screen.getByLabelText("Delete template: hello"));

		// After the rejection, the error toast fires AND the row is
		// restored (because the backend still has it). The user must NOT
		// see a stale empty list with a phantom "deleted" state.
		await waitFor(() => {
			expect(mockShowSnack).toHaveBeenCalledWith(
				"Failed to delete template",
				"error",
			);
		});

		// The pre-delete state is restored: both "hello" and "brb" are
		// visible again. (We poll because the restore setState may need
		// a tick to flush.)
		await waitFor(() => {
			expect(screen.getByText("hello")).toBeTruthy();
		});
		expect(screen.getByText("brb")).toBeTruthy();
		expect(screen.getAllByText(/hello|brb/).length).toBe(2);
	});
});

describe("Templates page — Clear All + LastUpdatedIndicator", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockShowSnack.mockReset();
		toastWarning.mockClear();
		toastSuccess.mockClear();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_templates") return Promise.resolve(seedTemplates);
			if (type === "save_templates") return Promise.resolve({});
			return Promise.resolve({});
		});
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the LastUpdatedIndicator", async () => {
		const { default: TemplatesPage } = await import("@/pages/Templates");
		renderWithProviders(<TemplatesPage />);

		await waitFor(() => {
			expect(screen.getByText("hello")).toBeTruthy();
		});
		expect(screen.getByTestId("last-updated-indicator")).toBeTruthy();
	});

	it("shows the Clear All button and clears templates on confirm", async () => {
		const { default: TemplatesPage } = await import("@/pages/Templates");
		renderWithProviders(<TemplatesPage />);

		await waitFor(() => {
			expect(screen.getByText("hello")).toBeTruthy();
		});

		// Click the Clear All toolbar button (aria-label distinguishes it)
		fireEvent.click(screen.getByLabelText("Clear all templates"));

		// Confirmation dialog appears
		await waitFor(() => {
			expect(screen.getByText("Clear All Templates")).toBeTruthy();
		});
		expect(
			screen.getByText(
				"Are you sure you want to clear all templates? This action cannot be undone.",
			),
		).toBeTruthy();

		// Click the dialog's confirm button (text "Clear All", no aria-label)
		fireEvent.click(screen.getByRole("button", { name: "Clear All" }));

		// save_templates called with empty array
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("save_templates", {
				templates: [],
			});
		});
		// Success snackbar shown
		expect(mockShowSnack).toHaveBeenCalledWith(
			"All templates cleared",
			"success",
		);
	});

	it("disables the Clear All button when there are no templates", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_templates") return Promise.resolve({ templates: [] });
			return Promise.resolve({});
		});
		const { default: TemplatesPage } = await import("@/pages/Templates");
		renderWithProviders(<TemplatesPage />);

		await waitFor(() => {
			expect(screen.getByText("No templates yet")).toBeTruthy();
		});
		// The Clear All button should be disabled
		const clearAllButton = screen.getByLabelText("Clear all templates");
		expect(clearAllButton.getAttribute("disabled")).not.toBeNull();
	});
});
