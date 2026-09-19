import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({ icon }: { icon?: { name?: string } }) => (
		<span data-testid="hugeicon" data-name={icon?.name} />
	),
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

import { SearchField } from "@/components/common/SearchField";

describe("SearchField clear button, RW-0 rewrite of test_history_has_clear_button", () => {
	beforeEach(() => {
		cleanup();
	});

	afterEach(() => {
		cleanup();
	});

	it("does not render the clear button when the field is empty", () => {
		render(<SearchField value="" onChange={() => {}} placeholder="Search" />);

		// The clear button is conditionally rendered (only
		// when `value` is truthy).  An empty field shows no
		// clear button.
		expect(screen.queryByRole("button")).toBeNull();
	});

	it("renders a clear button with an accessible aria-label when the field has a value", () => {
		render(
			<SearchField value="hello" onChange={() => {}} placeholder="Search" />,
		);

		// The Python invariant was: SearchField.tsx contains
		// one of `"Clear search"`, `"clearSearch"`, or
		// `aria-label="Clear search"`.  Behavioral: the
		// rendered DOM has a button with aria-label "Clear
		// search" (i18n key a11y.clearSearch → "Clear search"
		// in en.json) that a screen reader can announce.
		const clearBtn = screen.getByRole("button", {
			name: /clear search/i,
		});
		expect(clearBtn).toBeTruthy();
	});

	it("calls onChange with an empty string when the clear button is clicked", () => {
		const onChange = vi.fn();
		render(
			<SearchField value="hello" onChange={onChange} placeholder="Search" />,
		);

		const clearBtn = screen.getByRole("button", {
			name: /clear search/i,
		});
		fireEvent.click(clearBtn);

		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenCalledWith("");
	});

	it("forwards the typed value to onChange", () => {
		const onChange = vi.fn();
		render(<SearchField value="" onChange={onChange} placeholder="Search" />);

		const input = screen.getByPlaceholderText("Search");
		fireEvent.change(input, { target: { value: "typing" } });

		expect(onChange).toHaveBeenCalledWith("typing");
	});
});
