/**
 * Tests for the redesigned Vocabulary page features:
 *
 *   - flat two-column list: no category badges, no group headers, no
 *     Category column in the header
 *   - direct Edit / Delete icon buttons on each row (aria-labels,
 *     no tooltips)
 *   - bulk selection: row checkboxes → floating bulk bar with count,
 *     "Delete selected"
 *   - inline quick-add row (replaces the disconnected Add modal),
 *     duplicate wrong→correct pairs refused
 *   - per-entry "Test this entry" action → inline live-engine result
 *     (applied / no-change / error+retry)
 *   - load-time dedupe of duplicate pairs (merged toast)
 *
 * Mock strategy mirrors Vocabulary-page-improvements.test.tsx: stub
 * usePython / useSnackbar / sonner / hugeicons / next-themes and drive
 * the page through the real components.
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
	within,
} from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";

const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module. Variants here:
// the snackbar routes through the toast singletons by type
// (snackbarMock({ routeToSonner: true }) — the real useSnackbar
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

/** Seed: 2 misspellings + 1 phrase correction (flat list, 3 rows). */
const seedData: VocabularyData = {
	misspellings: { recieve: "receive", teh: "the" },
	phrase_corrections: [["i am going to", "I'm going to"]],
};

/** Wire mockCall to return `data` for get_vocabulary. */
function seedWith(data: VocabularyData) {
	mockCall.mockImplementation((type: unknown, arg?: unknown) => {
		const cmd =
			typeof type === "string"
				? type
				: ((type as { type?: string })?.type ?? "");
		const text =
			typeof arg === "object" && arg !== null
				? ((arg as { text?: unknown })?.text ?? "")
				: "";
		if (cmd === "get_vocabulary") return Promise.resolve(data);
		if (cmd === "save_vocabulary") return Promise.resolve({ success: true });
		// Simulate the backend correction engine for the "Test
		// corrections" panel: phrase corrections (case-insensitive
		// literal replace) + misspellings dict on lowercased tokens.
		// Mirrors VocabularyManager.apply_to_text semantics enough for
		// the panel tests.
		if (cmd === "test_vocabulary_correction") {
			let output = String(text);
			const phrases = Array.isArray(data.phrase_corrections)
				? (data.phrase_corrections as Array<[string, string]>)
				: [];
			for (const [wrong, good] of phrases) {
				output = output.split(wrong).join(good);
			}
			const misspellings = data.misspellings ?? {};
			output = output
				.split(" ")
				.map((token) => {
					const key = token.toLowerCase();
					const good = misspellings[key];
					return typeof good === "string" ? good : token;
				})
				.join(" ");
			return Promise.resolve({
				input: String(text),
				output,
				applied: output !== String(text),
			});
		}
		return Promise.resolve({});
	});
}

describe("Vocabulary page — flat two-column list", () => {
	beforeEach(() => {
		mockCall.mockReset();
		showSnack.mockReset();
		toastSuccess.mockClear();
		toastError.mockClear();
		// The page persists search/sort filters in sessionStorage via
		// useFilterState — clear it before every test so a query typed
		// by an earlier test never filters out the seeded rows a later
		// test expects.
		sessionStorage.clear();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders a flat list with a 3-column header and no category UI", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		// Column header row: Heard as | Corrected to | Actions — no
		// "Category" column.
		const header = screen.getByTestId("vocab-list-header");
		expect(within(header).getByText("Heard as")).toBeTruthy();
		expect(within(header).getByText("Corrected to")).toBeTruthy();
		expect(within(header).getByText("Actions")).toBeTruthy();
		expect(within(header).queryByText("Category")).toBeNull();

		// No group headers, no category badges anywhere.
		expect(screen.queryAllByTestId("vocab-group-header").length).toBe(0);
		expect(screen.queryByText("Misspellings")).toBeNull();
		expect(screen.queryByText("Phrase Corrections")).toBeNull();

		// All three rows render flat, in one list.
		expect(screen.getAllByTestId("vocab-list-row").length).toBe(3);
		expect(screen.getByText("recieve")).toBeTruthy();
		expect(screen.getByText("teh")).toBeTruthy();
		expect(screen.getByText("i am going to")).toBeTruthy();
	});

	it("renders direct Edit + Delete icon buttons with aria-labels on each row", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		const row = screen
			.getByText("recieve")
			.closest('[data-testid="vocab-list-row"]') as HTMLElement;

		// Direct buttons (no overflow menu): Edit + Delete with the
		// entry name in the aria-label.
		expect(within(row).getByLabelText("Edit: recieve")).toBeTruthy();
		expect(within(row).getByLabelText("Delete: recieve")).toBeTruthy();
		// No overflow "Entry actions" menu button anymore.
		expect(within(row).queryByLabelText("Entry actions")).toBeNull();

		// Clicking Edit swaps the row for the inline edit form (the SAME
		// inline treatment as Add — no modal), pre-filled with the entry.
		fireEvent.click(within(row).getByLabelText("Edit: recieve"));
		await waitFor(() => {
			expect(screen.getByTestId("vocab-edit-row")).toBeTruthy();
			expect(screen.getByDisplayValue("recieve")).toBeTruthy();
			expect(screen.getByDisplayValue("receive")).toBeTruthy();
		});
	});

	it("renders the row actions in Test → Delete → Edit order with NO tooltips", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		const row = screen
			.getByText("recieve")
			.closest('[data-testid="vocab-list-row"]') as HTMLElement;
		const actions = within(row)
			.getAllByRole("button")
			.filter((b) =>
				/^(Edit:|Test this entry:|Delete:)/.test(
					b.getAttribute("aria-label") ?? "",
				),
			);
		// Edit is RIGHTMOST (the app-wide convention — the pencil sits
		// at the far edge of every action group). Left to right:
		// Test → Delete → Edit.
		expect(
			actions.map((b) => b.getAttribute("aria-label")?.split(":")[0]),
		).toEqual(["Test this entry", "Delete", "Edit"]);
		// No native tooltips on any action icon (hover tooltips were
		// removed — the shapes + aria-labels carry the meaning).
		for (const b of actions) {
			expect(b.getAttribute("title")).toBeNull();
		}
	});
});

describe("Vocabulary page — empty state", () => {
	beforeEach(() => {
		mockCall.mockReset();
		showSnack.mockReset();
		toastSuccess.mockClear();
		toastError.mockClear();
		sessionStorage.clear();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the empty state with an Add CTA and hides search/sort when there are no entries", async () => {
		seedWith({ misspellings: {}, phrase_corrections: [] });
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		// Empty-state copy + primary CTA replace the table.
		await waitFor(() => {
			expect(screen.getByText("No corrections yet")).toBeTruthy();
		});
		expect(screen.getByText("Add your first word")).toBeTruthy();
		// No table, no rows.
		expect(screen.queryByTestId("vocab-list-row")).toBeNull();

		// Search + sort are NOT rendered for an empty list.
		expect(screen.queryByPlaceholderText(/Search \d+ corrections/)).toBeNull();
		expect(screen.queryByLabelText("Sort order")).toBeNull();

		// The CTA starts the add flow (inline quick-add row appears).
		fireEvent.click(screen.getByText("Add your first word"));
		await waitFor(() => {
			expect(screen.getByTestId("vocab-quick-add")).toBeTruthy();
		});
	});
});

describe("Vocabulary page — per-entry usage", () => {
	beforeEach(() => {
		mockCall.mockReset();
		showSnack.mockReset();
		toastSuccess.mockClear();
		toastError.mockClear();
		sessionStorage.clear();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("shows 'Used N×' + last-used for entries with server-tracked usage", async () => {
		// seedWith covers get_vocabulary; layer the usage snapshot on top.
		seedWith(seedData);
		const base = mockCall.getMockImplementation();
		mockCall.mockImplementation((type: unknown, arg?: unknown) => {
			const cmd =
				typeof type === "string"
					? type
					: ((type as { type?: string })?.type ?? "");
			if (cmd === "get_correction_usage")
				return Promise.resolve({
					version: 1,
					entries: {
						misspellings: {
							recieve: { count: 12, last_ts: 1723651200 },
							teh: { count: 1, last_ts: 1723651200 },
						},
					},
				});
			return base?.(type, arg) ?? Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		// Both entries with usage show the meta line; the phrase
		// correction (no usage record) does not.
		const usageLines = screen.getAllByTestId("vocab-entry-usage");
		expect(usageLines.length).toBe(2);
		const recieveRow = screen
			.getByText("recieve")
			.closest('[data-testid="vocab-list-row"]') as HTMLElement;
		expect(within(recieveRow).getByText(/Used 12×/)).toBeTruthy();
		expect(within(recieveRow).getByText(/last used/)).toBeTruthy();
		const tehRow = screen
			.getByText("teh")
			.closest('[data-testid="vocab-list-row"]') as HTMLElement;
		expect(within(tehRow).getByText(/Used 1×/)).toBeTruthy();
	});

	it("omits the usage line when there is no usage data", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		expect(screen.queryAllByTestId("vocab-entry-usage").length).toBe(0);
	});
});

describe("Vocabulary page — bulk selection", () => {
	beforeEach(() => {
		mockCall.mockReset();
		showSnack.mockReset();
		toastSuccess.mockClear();
		toastError.mockClear();
		sessionStorage.clear();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("shows the bulk bar when rows are selected and delete selected removes them", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		// No bulk bar before any selection.
		expect(screen.queryByTestId("vocab-bulk-bar")).toBeNull();

		// Select the "recieve" row via its checkbox.
		fireEvent.click(screen.getByLabelText("Select recieve"));

		const bulkBar = screen.getByTestId("vocab-bulk-bar");
		expect(within(bulkBar).getByText("1 selected")).toBeTruthy();

		// Delete selected → "recieve" gone, "teh" stays.
		fireEvent.click(within(bulkBar).getByText("Delete selected"));
		await waitFor(() => {
			expect(screen.queryByText("recieve")).toBeNull();
		});
		expect(screen.getByText("teh")).toBeTruthy();
		expect(screen.getByText("i am going to")).toBeTruthy();

		// save_vocabulary was called exactly once (the bulk delete).
		const saveCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "save_vocabulary",
		);
		expect(saveCalls.length).toBe(1);

		// Selection cleared after delete → bulk bar gone.
		expect(screen.queryByTestId("vocab-bulk-bar")).toBeNull();
	});

	it("select-all in the column header selects every visible row", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});
		fireEvent.click(screen.getByLabelText("Select all"));
		const bulkBar = screen.getByTestId("vocab-bulk-bar");
		expect(within(bulkBar).getByText("3 selected")).toBeTruthy();
	});

	it("exports exactly the selected rows via the bulk bar", async () => {
		seedWith(seedData);
		const exportVocabulary = vi.fn().mockResolvedValue({
			success: true,
			path: "C:\\Users\\test\\Downloads\\vocabulary.json",
		});
		// The export bridge lives on window.window_ (GDPR export IPC).
		(window as unknown as { window_?: unknown }).window_ = {
			exportVocabulary,
		};
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		// Select only the "recieve" row.
		fireEvent.click(screen.getByLabelText("Select recieve"));
		const bulkBar = screen.getByTestId("vocab-bulk-bar");

		// Open the Export selected menu and pick JSON. Radix opens
		// dropdowns on pointerdown, not click.
		fireEvent.pointerDown(within(bulkBar).getByText("Export selected"));
		const jsonItem = await screen.findByText("Export as JSON");
		fireEvent.click(jsonItem);

		await waitFor(() => {
			expect(exportVocabulary).toHaveBeenCalledTimes(1);
		});
		const [payload] = exportVocabulary.mock.calls[0] ?? [];
		expect(payload?.entries).toEqual([
			{ original: "recieve", correction: "receive", category: "misspellings" },
		]);
	});

	it("deselects all via the bulk bar X button", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		fireEvent.click(screen.getByLabelText("Select recieve"));
		expect(screen.getByTestId("vocab-bulk-bar")).toBeTruthy();

		fireEvent.click(screen.getByLabelText("Deselect all"));
		expect(screen.queryByTestId("vocab-bulk-bar")).toBeNull();
		// No rows selected → the header checkbox is unchecked too (the
		// design-system checkbox is a Radix button exposing aria-checked,
		// not a native input).
		expect(
			screen.getByLabelText("Select all").getAttribute("aria-checked"),
		).toBe("false");
	});
});

describe("Vocabulary page — inline quick add", () => {
	beforeEach(() => {
		mockCall.mockReset();
		showSnack.mockReset();
		toastSuccess.mockClear();
		toastError.mockClear();
		sessionStorage.clear();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("opens an inline row (not a modal) and saves a new entry", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		// "Add Word" opens the inline quick-add row — the list stays visible.
		fireEvent.click(screen.getByText("Add Word"));
		const quickAdd = screen.getByTestId("vocab-quick-add");
		expect(quickAdd).toBeTruthy();

		fireEvent.change(
			within(quickAdd).getByPlaceholderText("treat three, mynameis"),
			{ target: { value: "goed" } },
		);
		fireEvent.change(
			within(quickAdd).getByPlaceholderText("treat this, My Name Is"),
			{ target: { value: "good" } },
		);
		fireEvent.click(within(quickAdd).getByText("Save"));

		// New entry appears in the list; save_vocabulary called once.
		await waitFor(() => {
			expect(screen.getByText("good")).toBeTruthy();
		});
		expect(screen.getByText("goed")).toBeTruthy();
		const saveCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "save_vocabulary",
		);
		expect(saveCalls.length).toBe(1);

		// The quick-add row closed after a successful save.
		expect(screen.queryByTestId("vocab-quick-add")).toBeNull();
	});

	it("refuses an existing wrong→correct pair via the inline row", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		fireEvent.click(screen.getByText("Add Word"));
		const quickAdd = screen.getByTestId("vocab-quick-add");
		fireEvent.change(
			within(quickAdd).getByPlaceholderText("treat three, mynameis"),
			{ target: { value: "recieve" } },
		);
		fireEvent.change(
			within(quickAdd).getByPlaceholderText("treat this, My Name Is"),
			{ target: { value: "receive" } },
		);
		fireEvent.click(within(quickAdd).getByText("Save"));

		// The pre-check (case-insensitive wrong phrase, mirroring the
		// backend rule) refuses the add with an INLINE message — the
		// quick-add row stays open and shows the error.
		await waitFor(() => {
			expect(screen.getByTestId("vocab-quick-add-error").textContent).toBe(
				"This correction already exists",
			);
		});
		// No save happened.
		const saveCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "save_vocabulary",
		);
		expect(saveCalls.length).toBe(0);
		// Still exactly one "recieve" row.
		expect(screen.getAllByText("recieve").length).toBe(1);
	});
});

// NOTE: the standalone free-text "Test corrections" panel was removed
// (the per-entry Test action covers the same need with one click, no
// typing) — the panel tests that used to live here are gone.

describe("Vocabulary page — test this entry", () => {
	beforeEach(() => {
		mockCall.mockReset();
		showSnack.mockReset();
		toastSuccess.mockClear();
		toastError.mockClear();
		sessionStorage.clear();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders a Test-this-entry button with a descriptive aria-label on each row", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		const row = screen
			.getByText("recieve")
			.closest('[data-testid="vocab-list-row"]') as HTMLElement;
		expect(within(row).getByLabelText("Test this entry: recieve")).toBeTruthy();
	});

	it("runs the entry through the live engine and shows the corrected output inline", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		const row = screen
			.getByText("recieve")
			.closest('[data-testid="vocab-list-row"]') as HTMLElement;
		fireEvent.click(within(row).getByLabelText("Test this entry: recieve"));

		// Inline result appears below the row, fed by the server pass.
		const result = await screen.findByTestId("vocab-entry-test-result");
		expect(within(result).getByText("Corrected:")).toBeTruthy();
		expect(within(result).getByText("receive")).toBeTruthy();

		// Exactly one engine call, with the entry's exact wrong phrase.
		const testCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "test_vocabulary_correction",
		);
		expect(testCalls.length).toBe(1);
		expect(testCalls[0]?.[1]).toEqual({ text: "recieve" });
	});

	it("does not re-run the engine when clicking the Test icon again on an already-open row", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		const row = screen
			.getByText("recieve")
			.closest('[data-testid="vocab-list-row"]') as HTMLElement;
		const testButton = within(row).getByLabelText("Test this entry: recieve");
		fireEvent.click(testButton);

		const result = await screen.findByTestId("vocab-entry-test-result");
		expect(within(result).getByText("receive")).toBeTruthy();

		// Second click on the SAME row while its result is open:
		// no-op — no loading flash, no re-fetch, the completed result
		// stays exactly as it is.
		fireEvent.click(testButton);

		expect(within(result).getByText("receive")).toBeTruthy();
		await waitFor(() => {
			expect(screen.queryByText("Testing with the live engine…")).toBeNull();
		});

		const testCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "test_vocabulary_correction",
		);
		expect(testCalls.length).toBe(1);
	});

	it("does not flash the pending row for fast engine responses (Part F)", async () => {
		seedWith(seedData);
		// Hold the engine response pending so we can observe the UI
		// BEFORE it resolves.
		let resolveEngine!: (value: unknown) => void;
		mockCall.mockImplementation((type: unknown, _arg?: unknown) => {
			const cmd =
				typeof type === "string"
					? type
					: ((type as { type?: string })?.type ?? "");
			if (cmd === "get_vocabulary") return Promise.resolve(seedData);
			if (cmd === "test_vocabulary_correction") {
				return new Promise((resolve) => {
					resolveEngine = resolve;
				});
			}
			return Promise.resolve({});
		});
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		const row = screen
			.getByText("recieve")
			.closest('[data-testid="vocab-list-row"]') as HTMLElement;
		fireEvent.click(within(row).getByLabelText("Test this entry: recieve"));

		// Even though the request is still in flight, the pending row
		// must NOT be painted (fast responses never flash).
		expect(screen.queryByText("Testing with the live engine…")).toBeNull();

		// Resolve promptly — well under the 300ms pending threshold.
		resolveEngine({
			input: "recieve",
			output: "receive",
			applied: true,
		});
		const result = await screen.findByTestId("vocab-entry-test-result");
		await waitFor(() => {
			expect(within(result).getByText("receive")).toBeTruthy();
		});
		expect(screen.queryByText("Testing with the live engine…")).toBeNull();
	});

	it("shows the pending row only when the engine is genuinely slow (>300ms)", async () => {
		seedWith(seedData);
		let resolveEngine!: (value: unknown) => void;
		mockCall.mockImplementation((type: unknown, _arg?: unknown) => {
			const cmd =
				typeof type === "string"
					? type
					: ((type as { type?: string })?.type ?? "");
			if (cmd === "get_vocabulary") return Promise.resolve(seedData);
			if (cmd === "test_vocabulary_correction") {
				return new Promise((resolve) => {
					resolveEngine = resolve;
				});
			}
			return Promise.resolve({});
		});
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		const row = screen
			.getByText("recieve")
			.closest('[data-testid="vocab-list-row"]') as HTMLElement;
		fireEvent.click(within(row).getByLabelText("Test this entry: recieve"));

		// Not shown immediately…
		expect(screen.queryByText("Testing with the live engine…")).toBeNull();
		// …but appears once the request has been pending >300ms.
		await waitFor(
			() => {
				expect(screen.getByText("Testing with the live engine…")).toBeTruthy();
			},
			{ timeout: 2000 },
		);

		resolveEngine({
			input: "recieve",
			output: "receive",
			applied: true,
		});
		await waitFor(() => {
			expect(screen.queryByText("Testing with the live engine…")).toBeNull();
		});
		const result = screen.getByTestId("vocab-entry-test-result");
		expect(within(result).getByText("receive")).toBeTruthy();
	});

	it("clicking a different row's Test icon replaces the previous row's result", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		const rowRecieve = screen
			.getByText("recieve")
			.closest('[data-testid="vocab-list-row"]') as HTMLElement;
		const rowTeh = screen
			.getByText("teh")
			.closest('[data-testid="vocab-list-row"]') as HTMLElement;
		fireEvent.click(
			within(rowRecieve).getByLabelText("Test this entry: recieve"),
		);
		await screen.findByTestId("vocab-entry-test-result");

		// A different row's icon is NOT guarded by the already-open
		// result — it closes the previous result and tests the new row.
		fireEvent.click(within(rowTeh).getByLabelText("Test this entry: teh"));

		const result = await screen.findByTestId("vocab-entry-test-result");
		await waitFor(() => {
			expect(within(result).getByText("the")).toBeTruthy();
		});
		// The replaced row's result text is gone.
		expect(within(result).queryByText("receive")).toBeNull();

		const testCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "test_vocabulary_correction",
		);
		expect(testCalls.length).toBe(2);
		expect(testCalls[1]?.[1]).toEqual({ text: "teh" });
	});

	it("shows the no-change state when the engine does not match the entry", async () => {
		// A misspelling key containing a space can never fire: the
		// engine tokenizes on spaces, so "to 2" never matches a token.
		seedWith({ misspellings: { "to 2": "to" } });
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("to 2")).toBeTruthy();
		});

		const row = screen
			.getByText("to 2")
			.closest('[data-testid="vocab-list-row"]') as HTMLElement;
		fireEvent.click(within(row).getByLabelText("Test this entry: to 2"));

		expect(
			await screen.findByText(
				"No change — the engine didn't match this phrase",
			),
		).toBeTruthy();
	});

	it("shows an error with Retry when the engine call fails, and Retry recovers", async () => {
		let engineCalls = 0;
		mockCall.mockImplementation((type: unknown, arg?: unknown) => {
			const cmd =
				typeof type === "string"
					? type
					: ((type as { type?: string })?.type ?? "");
			if (cmd === "get_vocabulary") return Promise.resolve(seedData);
			if (cmd === "test_vocabulary_correction") {
				engineCalls += 1;
				if (engineCalls === 1) {
					return Promise.reject(new Error("engine offline"));
				}
				const text =
					typeof arg === "object" && arg !== null
						? String((arg as { text?: unknown })?.text ?? "")
						: "";
				return Promise.resolve({
					input: text,
					output: "receive",
					applied: true,
				});
			}
			return Promise.resolve({});
		});
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		const row = screen
			.getByText("recieve")
			.closest('[data-testid="vocab-list-row"]') as HTMLElement;
		fireEvent.click(within(row).getByLabelText("Test this entry: recieve"));

		// First attempt fails → error + Retry (no silent mirror).
		expect(
			await screen.findByText(
				"Couldn't reach the engine — check the connection and try again",
			),
		).toBeTruthy();

		// Retry re-runs the live engine and succeeds.
		fireEvent.click(screen.getByText("Retry"));
		const result = await screen.findByTestId("vocab-entry-test-result");
		await waitFor(() => {
			expect(within(result).getByText("receive")).toBeTruthy();
		});
		expect(engineCalls).toBe(2);
	});

	// The standalone free-text panel (and its client-mirror fallback
	// notice) was removed — the per-entry Test action surfaces engine
	// errors via the inline error + Retry path above.
});
describe("Vocabulary page — search + sort interactions", () => {
	beforeEach(() => {
		mockCall.mockReset();
		showSnack.mockReset();
		toastSuccess.mockClear();
		toastError.mockClear();
		sessionStorage.clear();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("typing in search filters the visible rows to matching entries", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		const searchInput = screen.getByPlaceholderText("Search 3 corrections");
		fireEvent.change(searchInput, { target: { value: "teh" } });

		// Only the matching row remains — the other two are filtered
		// out client-side (no reload, no save).
		await waitFor(() => {
			expect(screen.queryByText("recieve")).toBeNull();
		});
		expect(screen.getByText("teh")).toBeTruthy();
		expect(screen.queryByText("i am going to")).toBeNull();
		expect(screen.getAllByTestId("vocab-list-row").length).toBe(1);
	});

	it("sort reorders rows (A→Z vs Z→A) without a reload", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		const rowOriginals = () =>
			Array.from(
				document.querySelectorAll('[data-testid="vocab-list-row"]'),
			).map((r) => (r.querySelector("span") as HTMLElement)?.textContent ?? "");

		// Open the sort select via keyboard (Radix Select opens on
		// ArrowDown when the trigger is focused; pointer events are
		// unreliable in jsdom — hasPointerCapture isn't implemented).
		const sortTrigger = screen.getByLabelText("Sort order");
		sortTrigger.focus();
		fireEvent.keyDown(sortTrigger, { key: "ArrowDown" });
		const az = await screen.findByText("A → Z");
		fireEvent.click(az);

		// The flat list is sorted by original, ascending.
		await waitFor(() => {
			const order = rowOriginals();
			expect(order).toEqual(["i am going to", "recieve", "teh"]);
		});

		// Flip to Z → A — the order reverses.
		const sortTrigger2 = screen.getByLabelText("Sort order");
		sortTrigger2.focus();
		fireEvent.keyDown(sortTrigger2, { key: "ArrowDown" });
		const za = await screen.findByText("Z → A");
		fireEvent.click(za);
		await waitFor(() => {
			const order = rowOriginals();
			expect(order).toEqual(["teh", "recieve", "i am going to"]);
		});
	});
});

describe("Vocabulary page — load-time dedupe", () => {
	beforeEach(() => {
		mockCall.mockReset();
		showSnack.mockReset();
		toastSuccess.mockClear();
		toastError.mockClear();
		sessionStorage.clear();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("merges exact duplicates on load and surfaces a toast", async () => {
		// phrase_corrections is an array — it CAN contain exact repeats
		// (unlike the dict categories), simulating a legacy/hand-edited file.
		seedWith({
			phrase_corrections: [
				["um", ""],
				["um", ""],
			],
		});
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("um")).toBeTruthy();
		});
		// Exactly ONE "um" row after dedupe.
		expect(screen.getAllByText("um").length).toBe(1);
		expect(toastSuccess).toHaveBeenCalledWith("Merged 1 duplicate entries");
	});
});
