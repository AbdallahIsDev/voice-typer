/**
 *  regression test: when a Templates search yields no matches, the
 * EmptyState must use the dedicated `templates.noResults` /
 * `templates.noResultsDescription` i18n keys — NOT the misleading
 * `templates.emptyTitle` ("No templates yet") nor the cross-module
 * `history.noResultsDescription`.
 *
 * Before , the search-no-results branch borrowed
 * `history.noResultsDescription` (History namespace coupling) and reused
 * `templates.emptyTitle` which is semantically wrong: it implies the user's
 * templates are missing rather than the search just did not match.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockCall } = vi.hoisted(() => ({
	mockCall: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
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

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock("sonner", () => ({
	toast: {
		success: (...args: unknown[]) => toastSuccess(...args),
		error: (...args: unknown[]) => toastError(...args),
		warning: vi.fn(),
		info: vi.fn(),
		dismiss: vi.fn(),
	},
	Toaster: () => null,
}));

vi.mock("next-themes", () => ({
	useTheme: () => ({ theme: "light" as const }),
}));

describe("Templates page — NH-15 search-no-results uses dedicated i18n keys", () => {
	beforeEach(() => {
		mockCall.mockReset();
		// Seed two templates so the page renders the list (not the empty-title state)
		// and we can drive the search input to a no-match term.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_templates") {
				return Promise.resolve({
					templates: [
						{ trigger: "hello world", output: "Hi!", match_mode: "exact" },
						{
							trigger: "morning greeting",
							output: "Good morning!",
							match_mode: "contains",
						},
					],
				});
			}
			if (type === "save_templates") return Promise.resolve({});
			return Promise.resolve({});
		});
		toastError.mockClear();
		toastSuccess.mockClear();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders templates.noResults title when search returns no matches", async () => {
		const user = userEvent.setup();
		const { default: TemplatesPage } = await import("@/pages/Templates");
		render(<TemplatesPage />);

		// Wait for at least one template row to render.
		await waitFor(() => {
			expect(screen.getByText("hello world")).toBeTruthy();
		});

		// Type a search term that matches no template.
		const searchInput = screen.getByPlaceholderText(/search templates/i);
		await user.type(searchInput, "zzz_no_match_zzz");

		// The dedicated templates.noResults title should be shown.
		await waitFor(() => {
			expect(screen.getByText("No results found")).toBeTruthy();
		});
		// The dedicated templates.noResultsDescription should be shown.
		expect(
			screen.getByText(
				/Try a different search term or clear the search to see all templates\./i,
			),
		).toBeTruthy();

		// And the misleading "No templates yet" title should NOT be shown
		// (that key is reserved for the genuinely-empty state).
		expect(screen.queryByText("No templates yet")).toBeNull();
	});
});
