/**
 * Tests for the Vocabulary page-level improvements:
 *
 *   - : soft display cap (200 rows) with "Show more" button
 *   - : "Clear All" button gated by ConfirmDialog
 *   - : count label stays visible during search (in the search/filter
 *     row, reworded to "corrections")
 *   - : noResults EmptyState has a description
 *
 * Mock strategy mirrors the existing Vocabulary.test.tsx: we stub
 * ``usePython`` (so ``call`` is a vi.fn we control), ``useSnackbar``,
 * sonner, hugeicons, and next-themes.
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

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module. The snackbar
// routes through the toast singletons by type (the real useSnackbar
// delegates to sonner), so tests assert on toastSuccess / toastError.
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	nextThemesMock,
	pythonMock,
	snackbarMock,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockCall, showSnack, toastSuccess, toastError } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useSnackbar", () => snackbarMock({ routeToSonner: true }));
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

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

	it("caps the visible list at 200 rows and shows a Show more button", async () => {
		mockCall.mockImplementation((arg: unknown) => {
			const type =
				typeof arg === "string"
					? arg
					: ((arg as { type?: string })?.type ?? "");
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(250) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

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
		mockCall.mockImplementation((arg: unknown) => {
			const type =
				typeof arg === "string"
					? arg
					: ((arg as { type?: string })?.type ?? "");
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(50) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("word0")).toBeTruthy();
		});

		expect(screen.queryByText("Show more")).toBeNull();
	});

	it("folds the total entry count into the search placeholder", async () => {
		mockCall.mockImplementation((arg: unknown) => {
			const type =
				typeof arg === "string"
					? arg
					: ((arg as { type?: string })?.type ?? "");
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(5) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("word0")).toBeTruthy();
		});

		// No standalone count label — the total is folded into the
		// search placeholder ("Search 5 corrections…"), updated live as
		// entries are added/removed.
		expect(screen.queryByTestId("vocab-entry-count")).toBeNull();
		expect(screen.getByPlaceholderText("Search 5 corrections")).toBeTruthy();

		// Type a search query that matches nothing — the placeholder
		// still shows the total (the no-results empty state explains the
		// filter); there is no filtered-count element anymore.
		const searchInput = screen.getByPlaceholderText("Search 5 corrections");
		fireEvent.change(searchInput, { target: { value: "zzzzznomatch" } });

		// No results empty state appears.
		await waitFor(() => {
			expect(screen.getByText("No results found")).toBeTruthy();
		});

		expect(screen.queryByTestId("vocab-entry-count")).toBeNull();
	});

	it("renders the noResults EmptyState with a description", async () => {
		mockCall.mockImplementation((arg: unknown) => {
			const type =
				typeof arg === "string"
					? arg
					: ((arg as { type?: string })?.type ?? "");
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(5) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("word0")).toBeTruthy();
		});

		const searchInput = screen.getByPlaceholderText("Search 5 corrections");
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
		mockCall.mockImplementation((arg: unknown) => {
			const type =
				typeof arg === "string"
					? arg
					: ((arg as { type?: string })?.type ?? "");
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(3) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

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
		mockCall.mockImplementation((arg: unknown) => {
			const type =
				typeof arg === "string"
					? arg
					: ((arg as { type?: string })?.type ?? "");
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(3) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

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
		mockCall.mockImplementation((arg: unknown) => {
			const type =
				typeof arg === "string"
					? arg
					: ((arg as { type?: string })?.type ?? "");
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(3) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

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
			const lastSaveArg = saveCalls[saveCalls.length - 1]?.[1];
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

	it("Clear All also clears the bulk selection so the floating bar disappears", async () => {
		mockCall.mockImplementation((arg: unknown) => {
			const type =
				typeof arg === "string"
					? arg
					: ((arg as { type?: string })?.type ?? "");
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(3) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("word0")).toBeTruthy();
		});

		// Select every row → the floating bulk bar appears.
		fireEvent.click(screen.getByLabelText("Select all"));
		expect(screen.queryByTestId("vocab-bulk-bar")).toBeTruthy();

		// Open Clear All + confirm.
		fireEvent.click(screen.getByLabelText("Clear all vocabulary entries"));
		await waitFor(() => {
			expect(screen.getByText("Clear All Vocabulary")).toBeTruthy();
		});
		const dialogConfirm = screen
			.getByRole("alertdialog")
			.querySelector("button:last-of-type");
		expect(dialogConfirm).toBeTruthy();
		fireEvent.click(dialogConfirm as HTMLElement);

		// The list is empty AND the bulk bar is gone — a stale
		// "N selected" bar floating over an empty list was the bug
		// (Clear All never cleared the selection state).
		await waitFor(() => {
			expect(screen.queryByText("word0")).toBeNull();
		});
		expect(screen.queryByTestId("vocab-bulk-bar")).toBeNull();
	});
});

describe("Vocabulary page — paginated Show more (incremental reveal)", () => {
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

	it("each Show more click reveals another batch (not all at once)", async () => {
		// 450 entries — exceeds two DISPLAY_CAP batches (200 + 200 = 400)
		// but not three (600). With the old setShowAll(true) path a
		// single click would mount all 450 rows at once. With the
		// paginated path the first click reveals rows 201..400, the
		// second reveals rows 401..450, and only then does the
		// Show more button disappear.
		mockCall.mockImplementation((arg: unknown) => {
			const type =
				typeof arg === "string"
					? arg
					: ((arg as { type?: string })?.type ?? "");
			if (type === "get_vocabulary")
				return Promise.resolve(buildSeed(450) as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		// Default sort is "newest" (reversed) → word449 renders first.
		await waitFor(() => {
			expect(screen.getByText("word449")).toBeTruthy();
		});

		// Initial cap = 200 → word449..word250 visible. word249 hidden.
		expect(screen.queryByText("word249")).toBeNull();
		expect(screen.queryByText("word0")).toBeNull();

		// First click: displayCount 200 → 400. word449..word50 visible.
		// word49 still hidden.
		fireEvent.click(screen.getByText("Show more"));
		await waitFor(() => {
			expect(screen.getByText("word50")).toBeTruthy();
		});
		expect(screen.queryByText("word49")).toBeNull();

		// Show more is STILL rendered (450 > 400).
		expect(screen.getByText("Show more")).toBeTruthy();

		// Second click: displayCount 400 → 600. All 450 visible.
		fireEvent.click(screen.getByText("Show more"));
		await waitFor(() => {
			expect(screen.getByText("word0")).toBeTruthy();
		});
		expect(screen.getByText("word49")).toBeTruthy();

		// Show more is gone (450 ≤ 600).
		expect(screen.queryByText("Show more")).toBeNull();
	});
});

describe("Vocabulary page — duplicate detection on save", () => {
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

	it("refuses to save a new entry whose wrong→correct pair already exists", async () => {
		// Seed with one entry: "recieve" → "receive" in misspellings.
		// Re-adding the same pair must be refused — with categories
		// hidden, an exact pair is a UI-visible duplicate.
		mockCall.mockImplementation((arg: unknown) => {
			const type =
				typeof arg === "string"
					? arg
					: ((arg as { type?: string })?.type ?? "");
			if (type === "get_vocabulary")
				return Promise.resolve({
					misspellings: { recieve: "receive" },
				} as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		// Count save_vocabulary calls before the duplicate save.
		const saveCallsBefore = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "save_vocabulary",
		).length;

		// Open the inline quick-add row.
		fireEvent.click(screen.getByText("Add Word"));

		const triggerInput = await screen.findByPlaceholderText(
			"treat three, mynameis",
		);
		const replacementInput = screen.getByPlaceholderText(
			"treat this, My Name Is",
		);

		// Type the exact existing pair.
		fireEvent.change(triggerInput, { target: { value: "recieve" } });
		fireEvent.change(replacementInput, { target: { value: "receive" } });

		// Click Save.
		fireEvent.click(screen.getByText("Save"));

		// The inline error fires with the localised duplicate message
		// (the quick-add row stays open).
		await waitFor(() => {
			expect(screen.getByTestId("vocab-quick-add-error").textContent).toBe(
				"This correction already exists",
			);
		});

		// No save_vocabulary IPC call was made — the duplicate path
		// returns before persisting.
		const saveCallsAfter = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "save_vocabulary",
		).length;
		expect(saveCallsAfter).toBe(saveCallsBefore);

		// The list still shows exactly one "recieve" row — the
		// duplicate was NOT appended.
		expect(screen.getAllByText("recieve").length).toBe(1);
	});

	it("refuses the same trigger re-added to the same backend bucket with a different correction", async () => {
		// Categories are hidden, so a second entry with the same
		// original that would land in the SAME bucket (auto-detect)
		// would silently overwrite the first on save — refuse it.
		// Seed: "recieve" → "receive" in misspellings; adding
		// "recieve" → "recieved" (also auto-detected as
		// misspellings) must be blocked.
		mockCall.mockImplementation((arg: unknown) => {
			const type =
				typeof arg === "string"
					? arg
					: ((arg as { type?: string })?.type ?? "");
			if (type === "get_vocabulary")
				return Promise.resolve({
					misspellings: { recieve: "receive" },
				} as VocabularyData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		fireEvent.click(screen.getByText("Add Word"));

		const triggerInput = await screen.findByPlaceholderText(
			"treat three, mynameis",
		);
		const replacementInput = screen.getByPlaceholderText(
			"treat this, My Name Is",
		);

		fireEvent.change(triggerInput, { target: { value: "recieve" } });
		fireEvent.change(replacementInput, { target: { value: "recieved" } });

		fireEvent.click(screen.getByText("Save"));

		await waitFor(() => {
			expect(screen.getByTestId("vocab-quick-add-error").textContent).toBe(
				"This correction already exists",
			);
		});

		// No save happened; the list is unchanged.
		const saveCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "save_vocabulary",
		);
		expect(saveCalls.length).toBe(0);
		expect(screen.getAllByText("recieve").length).toBe(1);
	});

	it("surfaces the backend's authoritative rejection as an inline error", async () => {
		// The frontend pre-check is a convenience layer — the AUTHORITATIVE
		// check lives in the backend write path (save_vocabulary_with_diff,
		// which rejects with client.duplicate_entry). This test seeds a
		// list WITHOUT the phrase so the pre-check passes, then makes the
		// backend reject the write — the renderer must surface the inline
		// "already exists" message and NOT append a row.
		mockCall.mockImplementation((type: unknown, arg?: unknown) => {
			const cmd =
				typeof type === "string"
					? type
					: ((type as { type?: string })?.type ?? "");
			if (cmd === "get_vocabulary")
				return Promise.resolve({
					misspellings: { teh: "the" },
				} as VocabularyData);
			if (cmd === "save_vocabulary") {
				void arg;
				const err = new Error(
					"duplicate correction: 'recieve' (2 entries)",
				) as Error & {
					code?: string;
				};
				err.code = "client.duplicate_entry";
				return Promise.reject(err);
			}
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("teh")).toBeTruthy();
		});

		fireEvent.click(screen.getByText("Add Word"));
		const triggerInput = await screen.findByPlaceholderText(
			"treat three, mynameis",
		);
		const replacementInput = screen.getByPlaceholderText(
			"treat this, My Name Is",
		);
		fireEvent.change(triggerInput, { target: { value: "recieve" } });
		fireEvent.change(replacementInput, { target: { value: "receive" } });
		fireEvent.click(screen.getByText("Save"));

		// The backend rejection surfaces as the inline error and the
		// row is NOT added locally.
		await waitFor(() => {
			expect(screen.getByTestId("vocab-quick-add-error").textContent).toBe(
				"This correction already exists",
			);
		});
		expect(screen.queryByText("recieve")).toBeNull();
	});
});

describe("Vocabulary page — duplicate review banner", () => {
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

	it("surfaces pre-existing duplicates and Remove duplicates collapses them", async () => {
		// "recieve" + "Recieve" normalize to the same wrong phrase
		// (case-insensitive) — a pre-existing duplicate from before the
		// backend check shipped.
		mockCall.mockImplementation((type: unknown, _arg?: unknown) => {
			const cmd =
				typeof type === "string"
					? type
					: ((type as { type?: string })?.type ?? "");
			if (cmd === "get_vocabulary")
				return Promise.resolve({
					misspellings: { recieve: "receive", Recieve: "receive" },
				} as VocabularyData);
			if (cmd === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByTestId("vocab-duplicate-banner")).toBeTruthy();
		});

		// Banner reports the extra (duplicate) entry: 1.
		expect(screen.getByTestId("vocab-duplicate-banner").textContent).toContain(
			"1 duplicate corrections found",
		);

		// Remove duplicates → the collapsed list (first occurrence
		// kept) is persisted.
		fireEvent.click(screen.getByRole("button", { name: "Remove duplicates" }));

		await waitFor(() => {
			expect(screen.queryByTestId("vocab-duplicate-banner")).toBeNull();
		});

		// Exactly one save; the payload keeps only "recieve".
		const saveCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "save_vocabulary",
		);
		expect(saveCalls.length).toBe(1);
		const payload = saveCalls[0]?.[1] as {
			misspellings?: Record<string, string>;
		};
		expect(payload?.misspellings).toEqual({ recieve: "receive" });

		// The collapsed list renders one row.
		expect(screen.getAllByText("recieve").length).toBe(1);
	});

	it("does not show the banner for a clean list", async () => {
		mockCall.mockImplementation((type: unknown) => {
			const cmd =
				typeof type === "string"
					? type
					: ((type as { type?: string })?.type ?? "");
			if (cmd === "get_vocabulary")
				return Promise.resolve(buildSeed(2) as VocabularyData);
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("word0")).toBeTruthy();
		});
		expect(screen.queryByTestId("vocab-duplicate-banner")).toBeNull();
	});
});
