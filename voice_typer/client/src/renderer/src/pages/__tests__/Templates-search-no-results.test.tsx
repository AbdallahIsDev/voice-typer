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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module.
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	nextThemesMock,
	pythonMock,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";
import { TooltipProvider } from "@/components/ui/tooltip";

const { mockCall, toastSuccess, toastError } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

/** TemplateListRow renders an InfoTooltip (Radix Tooltip) which throws
 *  without a TooltipProvider ancestor — the real App shell provides
 *  one, so tests mounting the page directly must too. */
const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

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
		const { useGlobalSearch } = await import("@/hooks/useGlobalSearch");
		const { default: TemplatesPage } = await import("@/pages/Templates");
		renderWithProviders(<TemplatesPage />);

		// Wait for at least one template row to render.
		await waitFor(() => {
			expect(screen.getByText("hello world")).toBeTruthy();
		});

		// Drive the global title-bar search to a term that matches no template.
		useGlobalSearch.getState().setQuery("zzz_no_match_zzz");

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
