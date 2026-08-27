/**
 * Regression guard for the Templates page's incremental "Show more"
 * reveal (mirrors Vocabulary.tsx's DISPLAY_CAP pagination).
 *
 * The list renders at most `DISPLAY_CAP` (200) rows until the user
 * clicks "Show more", which reveals another batch of 200. This keeps
 * very large template collections from mounting thousands of DOM rows
 * at once. The button disappears once every row is visible.
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
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";
import { TooltipProvider } from "@/components/ui/tooltip";

const { mockCall } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

/** 450 templates — exceeds two DISPLAY_CAP batches (200 + 200 = 400)
 *  but not three (600). */
const seedTemplates = {
	templates: Array.from({ length: 450 }, (_, i) => ({
		trigger: `tmpl-${i}`,
		output: `output-${i}`,
		match_mode: "exact",
	})),
};

/** TemplateListRow renders an InfoTooltip (Radix Tooltip) which throws
 *  without a TooltipProvider ancestor — the real App shell provides
 *  one, so tests mounting the page directly must too. */
const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

describe("Templates page — paginated Show more (incremental reveal)", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_templates") return Promise.resolve(seedTemplates);
			if (type === "save_templates") return Promise.resolve({});
			return Promise.resolve({});
		});
		localStorage.clear();
		sessionStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("each Show more click reveals another batch (not all at once)", async () => {
		const { default: TemplatesPage } = await import("@/pages/Templates");
		renderWithProviders(<TemplatesPage />);

		// Default sort is "newest" (reversed insertion order) → tmpl-449
		// renders first.
		await waitFor(() => {
			expect(screen.getByText("tmpl-449")).toBeTruthy();
		});

		// Initial cap = 200 → tmpl-449..tmpl-250 visible. tmpl-249 hidden.
		expect(screen.queryByText("tmpl-249")).toBeNull();
		expect(screen.queryByText("tmpl-0")).toBeNull();

		// First click: displayCount 200 → 400. tmpl-249..tmpl-50 visible.
		// tmpl-49 still hidden.
		fireEvent.click(screen.getByTestId("templates-show-more"));
		await waitFor(() => {
			expect(screen.getByText("tmpl-50")).toBeTruthy();
		});
		expect(screen.queryByText("tmpl-49")).toBeNull();

		// Show more is STILL rendered (450 > 400).
		expect(screen.getByTestId("templates-show-more")).toBeTruthy();

		// Second click: displayCount 400 → 600. All 450 visible.
		fireEvent.click(screen.getByTestId("templates-show-more"));
		await waitFor(() => {
			expect(screen.getByText("tmpl-0")).toBeTruthy();
		});
		expect(screen.getByText("tmpl-49")).toBeTruthy();

		// Show more is gone (450 ≤ 600).
		expect(screen.queryByTestId("templates-show-more")).toBeNull();
	});

	it("does not render Show more when the list fits within the cap", async () => {
		mockCall.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_templates") {
				return Promise.resolve({
					templates: [
						{ trigger: "hello", output: "Hi!", match_mode: "exact" },
						{ trigger: "brb", output: "be right back", match_mode: "contains" },
					],
				});
			}
			if (type === "save_templates") return Promise.resolve({});
			return Promise.resolve({});
		});

		const { default: TemplatesPage } = await import("@/pages/Templates");
		renderWithProviders(<TemplatesPage />);

		await waitFor(() => {
			expect(screen.getByText("hello")).toBeTruthy();
		});

		expect(screen.queryByTestId("templates-show-more")).toBeNull();
	});
});
