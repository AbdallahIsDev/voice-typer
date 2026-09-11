/**
 * SelectableRow, the shared "whole row is clickable" wrapper the
 * collection pages' ListRows render through. Previously untested in
 * isolation (only exercised via the page suites); these tests pin the
 * a11y + skip-nested-control contract the collection-page family
 * depends on:
 *   - a click anywhere on the row (that didn't originate on an ignored
 *     nested control) selects the row
 *   - clicks originating on a nested control listed in
 *     `ignoreClicksFrom` are left to that control (no double-fire)
 *   - clicks on nested controls NOT listed still select the row (rows
 *     whose nested controls stopPropagation themselves pass none)
 *   - rest props (className, data-testid, ...) pass straight through
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
	RADIO_GROUP_ITEM_SELECTOR,
	SelectableRow,
} from "@/components/common/SelectableRow";

afterEach(() => {
	cleanup();
});

function RowContent() {
	return (
		<>
			<button type="button" data-testid="nested-button">
				nested
			</button>
			<button
				type="button"
				data-testid="radio-item"
				data-slot="radio-group-item"
			>
				radio
			</button>
		</>
	);
}

describe("SelectableRow, row-click selection", () => {
	it("invokes onRowSelect when the row body is clicked", () => {
		const onRowSelect = vi.fn();
		render(
			<SelectableRow
				data-testid="row"
				onRowSelect={onRowSelect}
				className="row-classes"
			>
				<RowContent />
			</SelectableRow>,
		);
		fireEvent.click(screen.getByTestId("row"));
		expect(onRowSelect).toHaveBeenCalledTimes(1);
	});

	it("forwards rest props (className, data-*) onto the rendered div", () => {
		render(
			<SelectableRow
				data-testid="row"
				data-selected="false"
				onRowSelect={vi.fn()}
				className="grid cursor-pointer"
			/>,
		);
		const row = screen.getByTestId("row");
		expect(row.className).toBe("grid cursor-pointer");
		expect(row).toHaveAttribute("data-selected", "false");
	});
});

describe("SelectableRow, skip-nested-control gating", () => {
	it("leaves clicks on ignored nested controls to those controls (no selection fired)", () => {
		const onRowSelect = vi.fn();
		render(
			<SelectableRow
				data-testid="row"
				onRowSelect={onRowSelect}
				ignoreClicksFrom={["button"]}
			>
				<RowContent />
			</SelectableRow>,
		);
		fireEvent.click(screen.getByTestId("nested-button"));
		fireEvent.click(screen.getByTestId("radio-item"));
		expect(onRowSelect).not.toHaveBeenCalled();
	});

	it("matches ignoreClicksFrom against the Radix radio-item data-slot selector", () => {
		const onRowSelect = vi.fn();
		render(
			<SelectableRow
				data-testid="row"
				onRowSelect={onRowSelect}
				ignoreClicksFrom={[RADIO_GROUP_ITEM_SELECTOR]}
			>
				<RowContent />
			</SelectableRow>,
		);
		// The radio item is skipped...
		fireEvent.click(screen.getByTestId("radio-item"));
		expect(onRowSelect).not.toHaveBeenCalled();
		// ...the plain button is not.
		fireEvent.click(screen.getByTestId("nested-button"));
		expect(onRowSelect).toHaveBeenCalledTimes(1);
	});

	it("selects on nested-control clicks when no selectors are provided (rows whose controls stopPropagation themselves)", () => {
		const onRowSelect = vi.fn();
		render(
			<SelectableRow data-testid="row" onRowSelect={onRowSelect}>
				<RowContent />
			</SelectableRow>,
		);
		fireEvent.click(screen.getByTestId("nested-button"));
		expect(onRowSelect).toHaveBeenCalledTimes(1);
	});
});
