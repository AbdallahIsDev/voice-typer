/**
 * CollectionToolbar — the shared toolbar shell extracted from the
 * VocabToolbar / TemplateToolbar mirror.
 *
 * These tests pin the shell's contract for the Wave-5 page migration:
 *   - every domain label key resolves through t() into the button's
 *     accessible name / visible text / hover title (the injection
 *     points the pages own)
 *   - the hidden import input (accept attr, sr-only, aria-hidden) is
 *     wired to the page's import handlers
 *   - the three documented drift props (addAriaLabelKey, addDisabled,
 *     importAccept) reproduce BOTH pages' current forms
 *   - the byte-identical visual tokens survive the extraction: the
 *     single-row justify-between layout, the C-UI-9 Clear All
 *     destructive hover treatment, and the C-FILTER-1 SortSelect
 *     primitive (rendered only when there are entries)
 *
 * Mock strategy mirrors ExportFormatMenu.test.tsx (hugeicons stubbed)
 * plus an identity t() so the shells' key props assert as raw keys.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

// Identity t(): key props assert as their raw key strings, params are
// JSON-suffixed so interpolated keys are distinguishable.
vi.mock("@/i18n/i18n", async () => {
	const actual = await import("@/i18n/i18n");
	return {
		...actual,
		t: (key: string, params?: Record<string, unknown>) =>
			params ? `${key}:${JSON.stringify(params)}` : key,
	};
});

import { CollectionToolbar } from "@/components/common/CollectionToolbar";
import type { SortOrder } from "@/components/common/SortSelect";

function makeProps(overrides: Record<string, unknown> = {}) {
	return {
		importInputRef: { current: null },
		importAccept: "application/json,.json,.csv,text/csv",
		onImportClick: vi.fn(),
		onImportFile: vi.fn(),
		importAriaLabelKey: "test.importAria",
		importLabelKey: "test.import",
		importTitleKey: "test.importHint",
		onExport: vi.fn(),
		exportDisabled: false,
		onClearAll: vi.fn(),
		clearAllDisabled: false,
		clearAllAriaLabelKey: "test.clearAllAria",
		clearAllLabelKey: "test.clearAll",
		addLabelKey: "test.add",
		onAdd: vi.fn(),
		sortOrder: "newest" as SortOrder,
		onSortOrderChange: vi.fn(),
		hasEntries: true,
		...overrides,
	};
}

type ToolbarProps = ReturnType<typeof makeProps>;

function setupToolbar(overrides: Record<string, unknown> = {}) {
	const props = makeProps(overrides);
	render(<CollectionToolbar {...(props as ToolbarProps)} />);
	return props;
}

beforeEach(() => {
	vi.clearAllMocks();
});

afterEach(() => {
	cleanup();
});

describe("CollectionToolbar — domain label injection", () => {
	it("resolves every action button's visible label and accessible name from the injected keys", () => {
		setupToolbar();

		expect(
			screen.getByRole("button", { name: "test.importAria" }),
		).toBeTruthy();
		expect(screen.getByText("test.import")).toBeTruthy();
		expect(
			screen.getByRole("button", { name: "test.clearAllAria" }),
		).toBeTruthy();
		expect(screen.getByText("test.clearAll")).toBeTruthy();
		// The Add button's accessible name comes from its visible label
		// (no aria-label by default — see the drift tests below).
		expect(screen.getByRole("button", { name: "test.add" })).toBeTruthy();
		// The shared ExportFormatMenu trigger renders inside the shell.
		expect(
			screen.getByRole("button", { name: "exportFormat.export" }),
		).toBeTruthy();
	});

	it("uses the import title key as the Import button's hover title", () => {
		setupToolbar();
		const importButton = screen.getByRole("button", {
			name: "test.importAria",
		});
		expect(importButton).toHaveAttribute("title", "test.importHint");
	});

	it("uses the clear-all aria key for BOTH the Clear All aria-label and its title (the pages' current form)", () => {
		setupToolbar();
		const clearAll = screen.getByRole("button", {
			name: "test.clearAllAria",
		});
		expect(clearAll).toHaveAttribute("aria-label", "test.clearAllAria");
		expect(clearAll).toHaveAttribute("title", "test.clearAllAria");
	});
});

describe("CollectionToolbar — hidden import input", () => {
	it("renders the sr-only file input with the injected accept filter, aria-hidden, and out of tab order", () => {
		setupToolbar();
		const input = document.querySelector(
			'input[type="file"]',
		) as HTMLInputElement;
		expect(input).not.toBeNull();
		expect(input).toHaveAttribute(
			"accept",
			"application/json,.json,.csv,text/csv",
		);
		expect(input).toHaveAttribute("aria-hidden", "true");
		expect(input).toHaveAttribute("tabindex", "-1");
		expect(input.className).toContain("sr-only");
	});

	it("forwards the picked file to onImportFile", () => {
		const props = setupToolbar();
		const input = document.querySelector(
			'input[type="file"]',
		) as HTMLInputElement;
		const file = new File(["[]"], "list.json", { type: "application/json" });
		fireEvent.change(input, { target: { files: [file] } });
		expect(props.onImportFile).toHaveBeenCalledTimes(1);
		expect(props.onImportFile).toHaveBeenCalledWith(file);
	});

	it("opens the OS picker through onImportClick (the Import button delegates; the page owns the input ref)", () => {
		const props = setupToolbar();
		fireEvent.click(screen.getByRole("button", { name: "test.importAria" }));
		expect(props.onImportClick).toHaveBeenCalledTimes(1);
	});
});

describe("CollectionToolbar — drift props (both pages' forms expressible)", () => {
	it("Add button has NO aria-label by default (the Vocabulary form: visible label is the accessible name)", () => {
		setupToolbar();
		const add = screen.getByRole("button", { name: "test.add" });
		expect(add).not.toHaveAttribute("aria-label");
	});

	it("Add button carries the injected aria-label when the key is provided (the Templates form)", () => {
		setupToolbar({ addAriaLabelKey: "test.addNewAria" });
		const add = screen.getByRole("button", { name: "test.addNewAria" });
		expect(add).toHaveAttribute("aria-label", "test.addNewAria");
	});

	it("Add button is enabled by default (the Templates form: never disabled)", () => {
		setupToolbar();
		expect(screen.getByRole("button", { name: "test.add" })).toBeEnabled();
	});

	it("Add button disables when addDisabled is set (the Vocabulary form: disabled while saving)", () => {
		setupToolbar({ addDisabled: true });
		expect(screen.getByRole("button", { name: "test.add" })).toBeDisabled();
	});

	it("Clear All disables via clearAllDisabled", () => {
		setupToolbar({ clearAllDisabled: true });
		expect(
			screen.getByRole("button", { name: "test.clearAllAria" }),
		).toBeDisabled();
	});
});

describe("CollectionToolbar — visual tokens (byte-identical extraction)", () => {
	it("single-row layout: full-width justify-between parent + gap-2 secondary cluster (C-UI-10: spacing via parent gap)", () => {
		setupToolbar();
		const root = document.querySelector("div.flex.w-full");
		expect(root).not.toBeNull();
		expect(root?.className).toBe(
			"flex w-full flex-wrap items-center justify-between gap-2",
		);
		const secondary = root?.querySelector("div.flex.flex-wrap");
		expect(secondary?.className).toBe("flex flex-wrap items-center gap-2");
	});

	it("Clear All keeps the C-UI-9 muted-at-rest + solid-destructive-on-hover treatment", () => {
		setupToolbar();
		const clearAll = screen.getByRole("button", {
			name: "test.clearAllAria",
		});
		expect(clearAll.className).toContain(
			"text-(--text-muted) hover:border-destructive hover:bg-destructive hover:text-destructive-foreground dark:hover:bg-destructive",
		);
	});

	it("renders the shared SortSelect primitive (C-FILTER-1) only when there are entries", () => {
		const { unmount } = render(
			<CollectionToolbar {...(makeProps() as ToolbarProps)} />,
		);
		expect(screen.getByRole("combobox")).toBeTruthy();
		unmount();

		render(
			<CollectionToolbar
				{...(makeProps({ hasEntries: false }) as ToolbarProps)}
			/>,
		);
		expect(screen.queryByRole("combobox")).toBeNull();
	});
});

describe("CollectionToolbar — action wiring", () => {
	it("clicking Clear All and Add invokes the page callbacks", () => {
		const props = setupToolbar();
		fireEvent.click(screen.getByRole("button", { name: "test.clearAllAria" }));
		expect(props.onClearAll).toHaveBeenCalledTimes(1);
		fireEvent.click(screen.getByRole("button", { name: "test.add" }));
		expect(props.onAdd).toHaveBeenCalledTimes(1);
	});
});
