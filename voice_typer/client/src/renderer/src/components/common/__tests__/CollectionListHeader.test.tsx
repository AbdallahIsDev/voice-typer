/**
 * CollectionListHeader — the shared column-header row extracted from
 * the VocabListHeader / TemplateListHeader mirror (the pair differed
 * ONLY in i18n keys and data-testid).
 *
 * These tests pin the shell's contract for the Wave-5 page migration:
 *   - the three column labels + the select-all aria resolve through
 *     t() from the injected keys
 *   - the select-all checkbox state machine: unchecked →
 *     indeterminate (some visible rows selected) → checked (all
 *     selected), and the onCheckedChange direction flips with
 *     allSelected (clicking a checked header deselects)
 *   - the byte-identical visual tokens survive the extraction: the
 *     sticky header treatment, the responsive grid template with the
 *     fixed 6.25rem actions column, and the C-UI-10 gap-x-3 spacing
 *
 * The Checkbox is the real Radix-based design-system component (the
 * same one the rows use) — driven with fireEvent.click like the
 * Vocabulary page suites do.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Identity t(): key props assert as their raw key strings.
vi.mock("@/i18n/i18n", async () => {
	const actual = await import("@/i18n/i18n");
	return {
		...actual,
		t: (key: string, params?: Record<string, unknown>) =>
			params ? `${key}:${JSON.stringify(params)}` : key,
	};
});

import { CollectionListHeader } from "@/components/common/CollectionListHeader";

function makeProps(overrides: Record<string, unknown> = {}) {
	return {
		testId: "test-list-header",
		visibleIds: ["a", "b", "c"],
		selectedIds: new Set<string>(),
		onSelectAll: vi.fn(),
		selectAllAriaKey: "test.selectAll",
		primaryColumnKey: "test.columnPrimary",
		secondaryColumnKey: "test.columnSecondary",
		actionsColumnKey: "test.columnActions",
		...overrides,
	};
}

type ListHeaderProps = ReturnType<typeof makeProps>;

function setupHeader(overrides: Record<string, unknown> = {}) {
	const props = makeProps(overrides);
	render(<CollectionListHeader {...(props as ListHeaderProps)} />);
	return props;
}

function headerCheckbox() {
	return screen.getByRole("checkbox", { name: "test.selectAll" });
}

beforeEach(() => {
	vi.clearAllMocks();
});

afterEach(() => {
	cleanup();
});

describe("CollectionListHeader — domain label injection", () => {
	it("renders the three column labels from their keys", () => {
		setupHeader();
		expect(screen.getByText("test.columnPrimary")).toBeTruthy();
		expect(screen.getByText("test.columnSecondary")).toBeTruthy();
		expect(screen.getByText("test.columnActions")).toBeTruthy();
	});

	it("carries the page-unique data-testid and the select-all aria", () => {
		setupHeader({ testId: "vocab-list-header" });
		expect(screen.getByTestId("vocab-list-header")).toBeTruthy();
		expect(headerCheckbox()).toHaveAccessibleName("test.selectAll");
	});
});

describe("CollectionListHeader — select-all state machine", () => {
	it("is unchecked when no visible row is selected", () => {
		setupHeader({ selectedIds: new Set() });
		expect(headerCheckbox()).toHaveAttribute("aria-checked", "false");
	});

	it("is mixed (ARIA indeterminate) when only some visible rows are selected", () => {
		setupHeader({ selectedIds: new Set(["a"]) });
		expect(headerCheckbox()).toHaveAttribute("aria-checked", "mixed");
	});

	it("is mixed (ARIA indeterminate) when selected rows include ids not in the visible list", () => {
		// A stale selection id (row scrolled out of the display cap)
		// still counts as "some, not all" — matches the pages' current
		// someSelected derivation.
		setupHeader({ selectedIds: new Set(["a", "gone"]) });
		expect(headerCheckbox()).toHaveAttribute("aria-checked", "mixed");
	});

	it("is checked when every visible row is selected", () => {
		setupHeader({ selectedIds: new Set(["a", "b", "c"]) });
		expect(headerCheckbox()).toHaveAttribute("aria-checked", "true");
	});

	it("is unchecked when the visible list is empty (nothing to select)", () => {
		setupHeader({ visibleIds: [], selectedIds: new Set(["a"]) });
		expect(headerCheckbox()).toHaveAttribute("aria-checked", "false");
	});

	it("clicking an unchecked header selects ALL visible ids", () => {
		const props = setupHeader({ selectedIds: new Set() });
		fireEvent.click(headerCheckbox());
		expect(props.onSelectAll).toHaveBeenCalledWith(["a", "b", "c"], true);
	});

	it("clicking a mixed (indeterminate) header selects all (NOT deselects — matches the pages' !allSelected direction)", () => {
		const props = setupHeader({ selectedIds: new Set(["a"]) });
		fireEvent.click(headerCheckbox());
		expect(props.onSelectAll).toHaveBeenCalledWith(["a", "b", "c"], true);
	});

	it("clicking a fully-checked header deselects all visible ids", () => {
		const props = setupHeader({
			selectedIds: new Set(["a", "b", "c"]),
		});
		fireEvent.click(headerCheckbox());
		expect(props.onSelectAll).toHaveBeenCalledWith(["a", "b", "c"], false);
	});
});

describe("CollectionListHeader — visual tokens (byte-identical extraction)", () => {
	it("keeps the sticky header treatment with backdrop blur and the subtle surface", () => {
		setupHeader();
		const header = screen.getByTestId("test-list-header");
		expect(header.className).toContain("sticky top-0 z-10");
		expect(header.className).toContain(
			"rounded-t-xl border-b border-border/5 bg-(--bg-subtle)/95",
		);
		expect(header.className).toContain("backdrop-blur-sm");
	});

	it("keeps the responsive grid: narrow 3-col template, sm+ 4-col with the FIXED 6.25rem actions column", () => {
		setupHeader();
		const header = screen.getByTestId("test-list-header");
		expect(header.className).toContain(
			"grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3",
		);
		expect(header.className).toContain(
			"sm:grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)_6.25rem]",
		);
	});

	it("secondary label stacks below the primary on narrow widths (col-start-2 → sm:col-start-auto)", () => {
		setupHeader();
		const secondary = screen.getByText("test.columnSecondary");
		expect(secondary.className).toBe("col-start-2 sm:col-start-auto");
	});

	it("actions label is end-aligned (justify-self-end)", () => {
		setupHeader();
		const actions = screen.getByText("test.columnActions");
		expect(actions.className).toContain("justify-self-end");
	});
});
