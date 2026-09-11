/**
 * CollectionBulkBar, the shared floating bulk-action bar extracted
 * from the VocabBulkBar / TemplateBulkBar mirror (the pair differed
 * ONLY in i18n keys and data-testid).
 *
 * These tests pin the shell's contract for the Wave-5 page migration:
 *   - the selected count interpolates { count } through t()
 *   - delete / export / deselect actions wire to the page callbacks
 *     (the export dropdown forwards the picked format)
 *   - the byte-identical visual tokens survive the extraction: the
 *     sticky/centered floating treatment, the subtle surface, and the
 *     C-UI-10 gap-2 spacing on the bar itself
 *
 * Mock strategy mirrors ExportFormatMenu.test.tsx (hugeicons stubbed)
 * plus an identity t() so the shells' key props assert as raw keys.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
// JSON-suffixed so the interpolated count is visible in the assertion.
vi.mock("@/i18n/i18n", async () => {
	const actual = await import("@/i18n/i18n");
	return {
		...actual,
		t: (key: string, params?: Record<string, unknown>) =>
			params ? `${key}:${JSON.stringify(params)}` : key,
	};
});

import { CollectionBulkBar } from "@/components/common/CollectionBulkBar";

function makeProps(overrides: Record<string, unknown> = {}) {
	return {
		testId: "test-bulk-bar",
		selectedCount: 3,
		selectedCountKey: "test.selectedCount",
		onDeleteSelected: vi.fn(),
		deleteLabelKey: "test.bulkDelete",
		exportSelectedKey: "test.exportSelected",
		onExportSelected: vi.fn(),
		deselectAllKey: "test.deselectAll",
		onClearSelection: vi.fn(),
		...overrides,
	};
}

type BulkBarProps = ReturnType<typeof makeProps>;

function setupBulkBar(overrides: Record<string, unknown> = {}) {
	const props = makeProps(overrides);
	render(<CollectionBulkBar {...(props as BulkBarProps)} />);
	return props;
}

beforeEach(() => {
	vi.clearAllMocks();
});

afterEach(() => {
	cleanup();
});

describe("CollectionBulkBar, domain label injection", () => {
	it("renders the selected count with { count } interpolated through t()", () => {
		setupBulkBar({ selectedCount: 3 });
		expect(screen.getByText('test.selectedCount:{"count":"3"}')).toBeTruthy();
	});

	it("renders the delete and export labels from their keys", () => {
		setupBulkBar();
		expect(
			screen.getByRole("button", { name: "test.bulkDelete" }),
		).toBeTruthy();
		expect(
			screen.getByRole("button", { name: "test.exportSelected" }),
		).toBeTruthy();
	});

	it("carries the page-unique data-testid", () => {
		setupBulkBar({ testId: "vocab-bulk-bar" });
		expect(screen.getByTestId("vocab-bulk-bar")).toBeTruthy();
	});
});

describe("CollectionBulkBar, action wiring", () => {
	it("Delete selected invokes the page callback", () => {
		const props = setupBulkBar();
		fireEvent.click(screen.getByRole("button", { name: "test.bulkDelete" }));
		expect(props.onDeleteSelected).toHaveBeenCalledTimes(1);
	});

	it("Export selected opens the format menu and forwards the picked format", async () => {
		// userEvent (not fireEvent), Radix's DropdownMenuTrigger opens on
		// the full pointer-event pipeline, which plain fireEvent.click
		// skips (same pattern as ExportFormatMenu.test.tsx).
		const user = userEvent.setup();
		const props = makeProps();
		render(<CollectionBulkBar {...(props as BulkBarProps)} />);
		await user.click(
			screen.getByRole("button", { name: "test.exportSelected" }),
		);
		// Radix opens the portal asynchronously, await the menu before
		// querying its items.
		const csvItem = await screen.findByRole("menuitem", {
			name: "exportFormat.csv",
		});
		fireEvent.click(csvItem);
		expect(props.onExportSelected).toHaveBeenCalledTimes(1);
		expect(props.onExportSelected).toHaveBeenCalledWith("csv");
	});

	it("Deselect-all (X) carries the aria-label + title from its key and invokes onClearSelection", () => {
		const props = setupBulkBar();
		const deselect = screen.getByRole("button", { name: "test.deselectAll" });
		expect(deselect).toHaveAttribute("title", "test.deselectAll");
		fireEvent.click(deselect);
		expect(props.onClearSelection).toHaveBeenCalledTimes(1);
	});
});

describe("CollectionBulkBar, visual tokens (byte-identical extraction)", () => {
	it("keeps the sticky floating-bar treatment: sticky bottom-4, mt-auto, centered w-fit, subtle surface, gap-2 spacing (C-UI-10)", () => {
		setupBulkBar();
		const bar = screen.getByTestId("test-bulk-bar");
		expect(bar.className).toContain("sticky bottom-4 z-20 mx-auto mt-auto");
		expect(bar.className).toContain(
			"flex w-fit max-w-full flex-wrap items-center gap-2",
		);
		expect(bar.className).toContain(
			"rounded-2xl border border-border/5 bg-(--bg-subtle) px-3 py-2 shadow-lg",
		);
	});

	it("delete button keeps the muted + destructive-text hover treatment", () => {
		setupBulkBar();
		const del = screen.getByRole("button", { name: "test.bulkDelete" });
		expect(del.className).toContain(
			"text-xs text-(--text-muted) hover:text-destructive hover:border-destructive/40",
		);
	});

	it("deselect-all keeps the focus-visible ring contract (C-FOCUS-2: full-opacity ring-3)", () => {
		setupBulkBar();
		const deselect = screen.getByRole("button", { name: "test.deselectAll" });
		expect(deselect.className).toContain(
			"focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none",
		);
	});
});
