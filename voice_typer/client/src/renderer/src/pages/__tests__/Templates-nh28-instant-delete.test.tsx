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

const { mockCall, mockShowSnack } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockShowSnack: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({ showSnack: mockShowSnack }),
}));

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({
		children,
		icon,
	}: {
		children?: React.ReactNode;
		icon?: { name?: string };
	}) => (
		<span data-testid="hugeicon" data-name={icon?.name}>
			{children}
		</span>
	),
}));

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		Add01Icon: make("Add01Icon"),
		Alert01Icon: make("Alert01Icon"),
		Alert02Icon: make("Alert02Icon"),
		AlertCircleIcon: make("AlertCircleIcon"),
		ArrowDown01Icon: make("ArrowDown01Icon"),
		ArrowUp01Icon: make("ArrowUp01Icon"),
		Cancel01Icon: make("Cancel01Icon"),
		Delete01Icon: make("Delete01Icon"),
		Download01Icon: make("Download01Icon"),
		File02Icon: make("File02Icon"),
		PencilEdit02Icon: make("PencilEdit02Icon"),
		Search01Icon: make("Search01Icon"),
		Tick02Icon: make("Tick02Icon"),
		UnfoldMoreIcon: make("UnfoldMoreIcon"),
	};
});

const toastWarning = vi.fn();
const toastSuccess = vi.fn();
vi.mock("sonner", () => ({
	toast: {
		success: (...args: unknown[]) => toastSuccess(...args),
		error: vi.fn(),
		warning: (...args: unknown[]) => toastWarning(...args),
		info: vi.fn(),
		dismiss: vi.fn(),
	},
	Toaster: () => null,
}));

vi.mock("next-themes", () => ({
	useTheme: () => ({ theme: "light" as const }),
}));

/** Two seeded templates so the page renders the list view (not EmptyState). */
const seedTemplates = {
	templates: [
		{ trigger: "hello", output: "Hello, world!", match_mode: "exact" },
		{ trigger: "brb", output: "be right back", match_mode: "contains" },
	],
};

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
		render(<TemplatesPage />);

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
		render(<TemplatesPage />);

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
