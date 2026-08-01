/**
 * Tests for the Vocabulary page-level improvements:
 *
 *   - : soft display cap (200 rows) with "Show more" button
 *   - : LastUpdatedIndicator rendered (agoLabel from useVocabulary)
 *   - : "Clear All" button gated by ConfirmDialog
 *   - : count footer stays visible during search
 *   - : noResults EmptyState has a description
 *
 * Mock strategy mirrors the existing Vocabulary.test.tsx: we stub
 * ``usePython`` (so ``call`` is a vi.fn we control), ``useSnackbar``,
 * sonner, hugeicons, and next-themes. ``useLastUpdated`` is NOT mocked
 * — the real hook is used so we can verify ``markUpdated`` is wired
 * into ``useVocabulary``'s load path (the ``agoLabel`` shows up after a
 * successful load).
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockCall, showSnack } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	showSnack: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({ showSnack }),
	showUndoableToast: vi.fn(),
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
	// Enumerate every icon imported by the page's render graph
	// (Vocabulary + Modal + Dialog + AlertDialog + Select +
	// SearchField + ExportFormatMenu + EmptyState +
	// LastUpdatedIndicator + VocabToolbar + VocabClearAllButton +
	// VocabListRow + VocabDialog + VocabSearchFilterBar). Missing one
	// crashes vitest's strict mock module.
	return {
		Add01Icon: make("Add01Icon"),
		Alert01Icon: make("Alert01Icon"),
		Alert02Icon: make("Alert02Icon"),
		AlertCircleIcon: make("AlertCircleIcon"),
		ArrowDown01Icon: make("ArrowDown01Icon"),
		ArrowRight01Icon: make("ArrowRight01Icon"),
		ArrowUp01Icon: make("ArrowUp01Icon"),
		BookOpen02Icon: make("BookOpen02Icon"),
		Cancel01Icon: make("Cancel01Icon"),
		Delete01Icon: make("Delete01Icon"),
		Download01Icon: make("Download01Icon"),
		PencilEdit02Icon: make("PencilEdit02Icon"),
		RefreshIcon: make("RefreshIcon"),
		Search01Icon: make("Search01Icon"),
		Tick02Icon: make("Tick02Icon"),
		UnfoldMoreIcon: make("UnfoldMoreIcon"),
	};
});

const toastSuccess = vi.fn();
const toastError = vi.fn();
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

import type { VocabularyData } from "@/types/ipc";

/** Build a VocabularyData with N misspellings entries. */
function buildSeed(n: number): VocabularyData {
	const misspellings: Record<string, string> = {};
	for (let i = 0; i < n; i++) {
		misspellings[`word${i}`] = `correction${i}`;
	}
	return { misspellings };
}

describe("Vocabulary page — display cap + Show more", () => {
	beforeEach(() => {
		mockCall.mockReset();
		showSnack.mockReset();
		toastSuccess.mockClear();
		toastError.mockClear();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the LastUpdatedIndicator after a successful load", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(3) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		render(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("word0")).toBeTruthy();
		});

		// The LastUpdatedIndicator renders a button with the refresh aria-label.
		expect(screen.getByTestId("last-updated-indicator")).toBeTruthy();
	});

	it("caps the visible list at 200 rows and shows a Show more button", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(250) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		render(<VocabularyPage />);

		// Wait for the first entry to render. The default sort is
		// "newest" which reverses the array, so word249 renders first.
		await waitFor(() => {
			expect(screen.getByText("word249")).toBeTruthy();
		});

		// The cap is 200. With "newest" sort (reversed), the visible
		// 200 rows are word249..word50. word49 and below should NOT be
		// in the document.
		expect(screen.queryByText("word49")).toBeNull();
		expect(screen.queryByText("word0")).toBeNull();

		// The Show more button is rendered.
		const showMoreButton = screen.getByText("Show more");
		expect(showMoreButton).toBeTruthy();

		// Click Show more — the next 200 rows are revealed (word49..word0).
		fireEvent.click(showMoreButton);
		await waitFor(() => {
			expect(screen.getByText("word49")).toBeTruthy();
		});
		expect(screen.getByText("word0")).toBeTruthy();
	});

	it("does NOT render Show more when the list is under the cap", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(50) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		render(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("word0")).toBeTruthy();
		});

		expect(screen.queryByText("Show more")).toBeNull();
	});

	it("keeps the total entry count visible during search", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(5) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		render(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("word0")).toBeTruthy();
		});

		// The count footer is visible with 5 entries.
		expect(screen.getByText("5 entries")).toBeTruthy();

		// Type a search query that matches nothing — the count footer
		// stays visible (the user still wants to know the total).
		const searchInput = screen.getByPlaceholderText("Search vocabulary…");
		fireEvent.change(searchInput, { target: { value: "zzzzznomatch" } });

		// No results empty state appears.
		await waitFor(() => {
			expect(screen.getByText("No results found")).toBeTruthy();
		});

		//The total count footer is STILL visible ().
		expect(screen.getByText("5 entries")).toBeTruthy();
	});

	it("renders the noResults EmptyState with a description", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(5) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		render(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("word0")).toBeTruthy();
		});

		const searchInput = screen.getByPlaceholderText("Search vocabulary…");
		fireEvent.change(searchInput, { target: { value: "zzzzznomatch" } });

		await waitFor(() => {
			expect(screen.getByText("No results found")).toBeTruthy();
		});

		// The description from vocabulary.noResultsDescription is rendered.
		expect(
			screen.getByText(
				/Try a different search term or clear the search to see all entries\./,
			),
		).toBeTruthy();
	});
});

describe("Vocabulary page — Clear All + ConfirmDialog", () => {
	beforeEach(() => {
		mockCall.mockReset();
		showSnack.mockReset();
		toastSuccess.mockClear();
		toastError.mockClear();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the Clear All button (disabled when empty, enabled when entries exist)", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(3) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		render(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("word0")).toBeTruthy();
		});

		// The Clear All button is rendered and enabled.
		const clearAllButton = screen.getByLabelText(
			"Clear all vocabulary entries",
		);
		expect(clearAllButton).toBeTruthy();
		expect(clearAllButton.hasAttribute("disabled")).toBe(false);
	});

	it("opens a ConfirmDialog when Clear All is clicked (no alertdialog before click)", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(3) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		render(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("word0")).toBeTruthy();
		});

		// No alertdialog before the click.
		expect(screen.queryByRole("alertdialog")).toBeNull();

		// Click Clear All.
		fireEvent.click(screen.getByLabelText("Clear all vocabulary entries"));

		// The ConfirmDialog appears with the clear-all title + message.
		await waitFor(() => {
			expect(screen.getByText("Clear All Vocabulary")).toBeTruthy();
		});
		expect(
			screen.getByText(
				/Are you sure you want to clear ALL vocabulary entries\?/,
			),
		).toBeTruthy();
		expect(screen.getByRole("alertdialog")).toBeTruthy();
	});

	it("clears all entries + persists empty VocabularyData when the user confirms", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(3) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		render(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("word0")).toBeTruthy();
		});

		// Open the confirm dialog.
		fireEvent.click(screen.getByLabelText("Clear all vocabulary entries"));
		await waitFor(() => {
			expect(screen.getByText("Clear All Vocabulary")).toBeTruthy();
		});

		// Count save_vocabulary calls before confirm.
		const saveCallsBefore = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "save_vocabulary",
		).length;

		// The dialog's confirm button has a different aria-label — find
		// it by its text content + role.
		const dialogConfirm = screen
			.getByRole("alertdialog")
			.querySelector("button:last-of-type");
		expect(dialogConfirm).toBeTruthy();
		fireEvent.click(dialogConfirm as HTMLElement);

		// save_vocabulary is called with an empty VocabularyData.
		await waitFor(() => {
			const saveCalls = mockCall.mock.calls.filter(
				(args: unknown[]) => args[0] === "save_vocabulary",
			);
			expect(saveCalls.length).toBeGreaterThan(saveCallsBefore);
			// The last save_vocabulary call's first arg should be the
			// empty data object.
			const lastSaveArg = saveCalls[saveCalls.length - 1][1];
			expect(lastSaveArg).toEqual({
				misspellings: {},
				phrase_corrections: [],
				extra_word_patterns: [],
				technical_terms: {},
				names: {},
				products: {},
			});
		});

		// The entries are gone from the UI.
		await waitFor(() => {
			expect(screen.queryByText("word0")).toBeNull();
		});

		// The success toast fired.
		expect(toastSuccess).toHaveBeenCalledWith("Vocabulary cleared");
	});
});
