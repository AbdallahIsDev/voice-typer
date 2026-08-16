/**
 * Tests for the redesigned Vocabulary page features:
 *
 *   - flat two-column list: no category badges, no group headers, no
 *     Category column in the header
 *   - direct Edit / Delete icon buttons on each row (aria-labels,
 *     tooltips)
 *   - bulk selection: row checkboxes → floating bulk bar with count,
 *     "Delete selected"
 *   - inline quick-add row (replaces the disconnected Add modal),
 *     duplicate wrong→correct pairs refused
 *   - live "Test corrections" panel
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

const { mockCall, showSnack } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	showSnack: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({
		showSnack: (message: string, type?: string) => {
			if (type === "error") toastError(message);
			else toastSuccess(message);
		},
	}),
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

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
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

		// Column header row: Original | Corrected | Actions — no
		// "Category" column.
		const header = screen.getByTestId("vocab-list-header");
		expect(within(header).getByText("Original")).toBeTruthy();
		expect(within(header).getByText("Corrected")).toBeTruthy();
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

		// Clicking Edit opens the edit dialog pre-filled with the entry.
		fireEvent.click(within(row).getByLabelText("Edit: recieve"));
		await waitFor(() => {
			expect(screen.getByDisplayValue("recieve")).toBeTruthy();
			expect(screen.getByDisplayValue("receive")).toBeTruthy();
		});
	});
});

describe("Vocabulary page — bulk selection", () => {
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
		// No rows selected → the header checkbox is unchecked too.
		expect(
			(screen.getByLabelText("Select all") as HTMLInputElement).checked,
		).toBe(false);
	});
});

describe("Vocabulary page — inline quick add", () => {
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

describe("Vocabulary page — live test panel", () => {
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

	it("applies corrections to a typed phrase in real time", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		// Expand the panel.
		fireEvent.click(screen.getByLabelText("Test corrections"));
		const input = await screen.findByPlaceholderText(
			"Type a phrase to see corrections…",
		);

		fireEvent.change(input, {
			target: { value: "recieve teh i am going to" },
		});

		const output = await screen.findByTestId("vocab-test-output");
		// The output comes from the (mocked) backend engine after the
		// debounce — wait for it to settle.
		await waitFor(() => {
			expect(within(output).getByText(/receive the I'm going to/)).toBeTruthy();
		});
	});

	it("shows the no-change hint when the phrase matches nothing", async () => {
		seedWith(seedData);
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		fireEvent.click(screen.getByLabelText("Test corrections"));
		const input = await screen.findByPlaceholderText(
			"Type a phrase to see corrections…",
		);
		fireEvent.change(input, { target: { value: "nothing matches here" } });

		expect(await screen.findByText("No corrections applied")).toBeTruthy();
	});
});

describe("Vocabulary page — load-time dedupe", () => {
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
