/**
 * DialogContent autofocus test — covers the fix for the long-standing
 * issue where Radix Dialog's default `onOpenAutoFocus` behavior focused
 * the FIRST focusable descendant. Because DialogContent renders the
 * visible X close button as its first child (for visual corner
 * placement), every Modal opened with keyboard focus on the X button —
 * SR users heard "Close button" first instead of the dialog title.
 *
 * The fix:
 * 1. `DialogContent` now wires `onOpenAutoFocus` to call
 *    `e.preventDefault()` (suppressing Radix's first-focusable scan)
 *    and then explicitly `focus()`-es the title element.
 * 2. `DialogTitle` now carries `tabIndex={-1}` so the heading is
 *    programmatically focusable (without joining the tab order).
 *
 * This test pins both halves of the contract.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({ icon }: { icon?: { name?: string } }) => (
		<span data-testid="hugeicon" data-name={icon?.name} />
	),
}));

vi.mock("@hugeicons/core-free-icons", () => ({
	Cancel01Icon: { name: "Cancel01Icon" },
}));

import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";

afterEach(() => {
	cleanup();
});

describe("DialogContent autofocus — title receives focus on open (ZU-46)", () => {
	it("DialogTitle carries tabIndex={-1} so it is programmatically focusable", () => {
		render(
			<Dialog open>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>My Title</DialogTitle>
					</DialogHeader>
				</DialogContent>
			</Dialog>,
		);
		const title = screen.getByText("My Title");
		// tabIndex={-1} makes the heading focusable via .focus()
		// (which DialogContent's onOpenAutoFocus handler calls)
		// without inserting it into the keyboard tab order.
		expect(title.getAttribute("tabindex")).toBe("-1");
	});

	it("auto-focuses the dialog title (not the close button) when the dialog opens", async () => {
		render(
			<Dialog open>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Autofocus Title</DialogTitle>
					</DialogHeader>
				</DialogContent>
			</Dialog>,
		);
		const title = screen.getByText("Autofocus Title");
		const closeBtn = screen.getByRole("button", { name: "Close" });

		// Radix Dialog fires onOpenAutoFocus on mount via useEffect.
		// Our handler calls preventDefault() and focuses the title
		// element (which is focusable thanks to tabIndex={-1}).
		// Wait for the focus to land — in jsdom this usually resolves
		// within a single tick, but waitFor keeps the test resilient.
		await waitFor(() => {
			expect(document.activeElement).toBe(title);
		});

		// Negative contract: the close button must NOT have stolen focus.
		expect(document.activeElement).not.toBe(closeBtn);
	});

	it("caller-provided onOpenAutoFocus override wins over the default title-focus behavior", () => {
		// DialogContent spreads {...props} onto DialogPrimitive.Content
		// AFTER its default onOpenAutoFocus, so a caller passing their
		// own onOpenAutoFocus overrides the title-focus behavior. This
		// is a positive contract — callers that need custom focus
		// management (e.g. focusing a primary action button) can do so.
		const customHandler = vi.fn((e: Event) => e.preventDefault());
		render(
			<Dialog open>
				<DialogContent onOpenAutoFocus={customHandler}>
					<DialogHeader>
						<DialogTitle>Override Title</DialogTitle>
					</DialogHeader>
				</DialogContent>
			</Dialog>,
		);
		expect(customHandler).toHaveBeenCalled();
	});
});
