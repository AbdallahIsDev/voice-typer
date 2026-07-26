/**
 * NH-21 (session NH) test: ``DialogContent`` renders a visible close (X) button.
 *
 * Before NH-21, ``DialogContent`` had NO visible close affordance — only
 * Escape + backdrop click dismissed the dialog. Sighted users without
 * keyboard expertise had no way to close a dialog with the mouse beyond
 * clicking the backdrop (which can be unintuitive for confirm-style
 * dialogs where the user is meant to choose a button).
 *
 * The fix added a ``<DialogPrimitive.Close>`` with an X icon positioned
 * at the inline-end top corner of every ``DialogContent``. This test
 * asserts the close button is present, labelled via ``t("common.close")``,
 * and triggers ``onOpenChange(false)`` when clicked (so callers' existing
 * close handlers receive the event through the same channel as Escape +
 * backdrop).
 */
import { cleanup, render, screen } from "@testing-library/react";
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

describe("NH-21: DialogContent renders a visible close (X) button", () => {
	it("renders a close button inside DialogContent", () => {
		render(
			<Dialog open={true}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Test dialog</DialogTitle>
					</DialogHeader>
				</DialogContent>
			</Dialog>,
		);
		// t("common.close") in en.json = "Close"
		const closeBtn = screen.getByRole("button", { name: "Close" });
		expect(closeBtn).toBeTruthy();
		expect(closeBtn.getAttribute("data-slot")).toBe("dialog-close-button");
	});

	it("the close button is positioned at the inline-end top corner", () => {
		render(
			<Dialog open={true}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Test dialog</DialogTitle>
					</DialogHeader>
				</DialogContent>
			</Dialog>,
		);
		const closeBtn = screen.getByRole("button", { name: "Close" });
		// ``end-2 top-2`` are logical-property positioning utilities so the
		// button flips with the document direction (Arabic RTL).
		expect(closeBtn.className).toContain("absolute");
		expect(closeBtn.className).toContain("end-2");
		expect(closeBtn.className).toContain("top-2");
	});

	it("the close button carries an X icon (Cancel01Icon)", () => {
		render(
			<Dialog open={true}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Test dialog</DialogTitle>
					</DialogHeader>
				</DialogContent>
			</Dialog>,
		);
		const closeBtn = screen.getByRole("button", { name: "Close" });
		const icon = closeBtn.querySelector('[data-testid="hugeicon"]');
		expect(icon).toBeTruthy();
		expect(icon?.getAttribute("data-name")).toBe("Cancel01Icon");
	});

	it("clicking the close button fires onOpenChange(false)", async () => {
		const onOpenChange = vi.fn();
		render(
			<Dialog open={true} onOpenChange={onOpenChange}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Test dialog</DialogTitle>
					</DialogHeader>
				</DialogContent>
			</Dialog>,
		);
		const closeBtn = screen.getByRole("button", { name: "Close" });
		closeBtn.click();
		// Radix DialogPrimitive.Close auto-fires onOpenChange(false).
		expect(onOpenChange).toHaveBeenCalledWith(false);
	});
});
