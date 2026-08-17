/**
 *  (ConfirmDialog confirmedRef test).
 *
 * ConfirmDialog wraps Radix AlertDialog. The contract under test is the
 * confirmedRef-based "Confirm vs Cancel" discrimination:
 *
 *   - Radix AlertDialog fires `onOpenChange(false)` exactly once per
 *     close — whether the user clicked Cancel, pressed Escape, focused
 *     away and hit Tab, etc.  Without a discriminator, every close
 *     would call both onConfirm and onCancel — bad.
 *   - The fix (DX-014) flips a `confirmedRef` to true only inside the
 *     Confirm action's onClick.  When `onOpenChange(false)` fires, the
 *     dialog reads the ref: if true → onConfirm was the cause (and
 *     already invoked); if false → close was via Cancel/Escape →
 *     onCancel should run.
 *
 * This test pins the behavior so a future refactor that drops the ref
 * (or that adds an onConfirm+onCancel double-call) is caught here.
 *
 * Additionally,  / : we assert the redundant `aria-label`
 * props were removed — the visible text content provides the accessible
 * name, so the buttons should NOT carry a duplicate aria-label.
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import ConfirmDialog from "@/components/common/ConfirmDialog";

describe("ConfirmDialog — BG-R11 (confirmedRef discriminates Confirm vs Cancel)", () => {
	afterEach(() => {
		cleanup();
	});

	it("clicking Confirm calls onConfirm exactly once and does NOT call onCancel", async () => {
		const user = userEvent.setup();
		const onConfirm = vi.fn();
		const onCancel = vi.fn();
		render(
			<ConfirmDialog
				open={true}
				title="Delete?"
				message="Are you sure?"
				confirmLabel="Delete"
				cancelLabel="Cancel"
				onConfirm={onConfirm}
				onCancel={onCancel}
			/>,
		);
		await screen.findByRole("alertdialog");

		const confirmBtn = screen.getByRole("button", { name: "Delete" });
		await user.click(confirmBtn);

		expect(onConfirm).toHaveBeenCalledTimes(1);
		expect(onCancel).not.toHaveBeenCalled();
	});

	it("clicking Cancel calls onCancel exactly once and does NOT call onConfirm", async () => {
		const user = userEvent.setup();
		const onConfirm = vi.fn();
		const onCancel = vi.fn();
		render(
			<ConfirmDialog
				open={true}
				title="Delete?"
				message="Are you sure?"
				confirmLabel="Delete"
				cancelLabel="Cancel"
				onConfirm={onConfirm}
				onCancel={onCancel}
			/>,
		);
		await screen.findByRole("alertdialog");

		const cancelBtn = screen.getByRole("button", { name: "Cancel" });
		await user.click(cancelBtn);

		expect(onCancel).toHaveBeenCalledTimes(1);
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it("pressing Escape calls onCancel (not onConfirm)", async () => {
		const user = userEvent.setup();
		const onConfirm = vi.fn();
		const onCancel = vi.fn();
		render(
			<ConfirmDialog
				open={true}
				title="Delete?"
				message="Are you sure?"
				confirmLabel="Delete"
				cancelLabel="Cancel"
				onConfirm={onConfirm}
				onCancel={onCancel}
			/>,
		);
		await screen.findByRole("alertdialog");
		await user.keyboard("{Escape}");

		expect(onCancel).toHaveBeenCalledTimes(1);
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it("Confirm button does NOT carry a redundant aria-label (text content provides accessible name)", async () => {
		render(
			<ConfirmDialog
				open={true}
				title="Delete?"
				message="Are you sure?"
				confirmLabel="Delete"
				cancelLabel="Cancel"
				onConfirm={vi.fn()}
				onCancel={vi.fn()}
			/>,
		);
		await screen.findByRole("alertdialog");
		const confirmBtn = screen.getByRole("button", { name: "Delete" });
		//visible text content provides the accessible name —
		// a duplicate aria-label would be announced twice by SR.
		expect(confirmBtn).not.toHaveAttribute("aria-label");
	});

	it("Cancel button does NOT carry a redundant aria-label (text content provides accessible name)", async () => {
		render(
			<ConfirmDialog
				open={true}
				title="Delete?"
				message="Are you sure?"
				confirmLabel="Delete"
				cancelLabel="Cancel"
				onConfirm={vi.fn()}
				onCancel={vi.fn()}
			/>,
		);
		await screen.findByRole("alertdialog");
		const cancelBtn = screen.getByRole("button", { name: "Cancel" });
		expect(cancelBtn).not.toHaveAttribute("aria-label");
	});

	it("warning variant maps to the warning button variant (BG-68)", async () => {
		render(
			<ConfirmDialog
				open={true}
				title="Skip onboarding?"
				message="You can't undo this."
				confirmLabel="Skip"
				cancelLabel="Cancel"
				variant="warning"
				onConfirm={vi.fn()}
				onCancel={vi.fn()}
			/>,
		);
		const confirmBtn = await screen.findByRole("button", { name: "Skip" });
		// The button component exposes data-variant based on the
		// `variant` prop — see components/ui/button.tsx.
		expect(confirmBtn).toHaveAttribute("data-variant", "warning");
	});

	it("destructive variant maps to the destructive button variant (regression guard)", async () => {
		render(
			<ConfirmDialog
				open={true}
				title="Delete?"
				message="Are you sure?"
				confirmLabel="Delete"
				cancelLabel="Cancel"
				variant="destructive"
				onConfirm={vi.fn()}
				onCancel={vi.fn()}
			/>,
		);
		const confirmBtn = await screen.findByRole("button", { name: "Delete" });
		expect(confirmBtn).toHaveAttribute("data-variant", "destructive");
	});

	it("opening twice in a row does not leak confirmedRef state (Cancel still works after Confirm)", async () => {
		const user = userEvent.setup();
		const onConfirm = vi.fn();
		const onCancel = vi.fn();
		const { rerender } = render(
			<ConfirmDialog
				open={true}
				title="Delete?"
				message="Are you sure?"
				confirmLabel="Delete"
				cancelLabel="Cancel"
				onConfirm={onConfirm}
				onCancel={onCancel}
			/>,
		);
		await screen.findByRole("alertdialog");
		// First session: Confirm.
		await user.click(screen.getByRole("button", { name: "Delete" }));
		expect(onConfirm).toHaveBeenCalledTimes(1);
		expect(onCancel).not.toHaveBeenCalled();

		// Close (open=false) then reopen (open=true) — the ref must
		// have been reset by the previous close path so Cancel still
		// routes to onCancel and NOT silently to onConfirm.
		rerender(
			<ConfirmDialog
				open={false}
				title="Delete?"
				message="Are you sure?"
				confirmLabel="Delete"
				cancelLabel="Cancel"
				onConfirm={onConfirm}
				onCancel={onCancel}
			/>,
		);
		await waitFor(() => {
			expect(screen.queryByRole("alertdialog")).toBeNull();
		});

		rerender(
			<ConfirmDialog
				open={true}
				title="Delete?"
				message="Are you sure?"
				confirmLabel="Delete"
				cancelLabel="Cancel"
				onConfirm={onConfirm}
				onCancel={onCancel}
			/>,
		);
		await screen.findByRole("alertdialog");
		await user.click(screen.getByRole("button", { name: "Cancel" }));
		expect(onCancel).toHaveBeenCalledTimes(1);
		// onConfirm must still be exactly 1 (not 2) — the second
		// session was a Cancel, not a Confirm.
		expect(onConfirm).toHaveBeenCalledTimes(1);
	});
	it("with dismissOnBackdrop, clicking the backdrop closes the dialog via onCancel (not onConfirm)", async () => {
		const onConfirm = vi.fn();
		const onCancel = vi.fn();
		render(
			<ConfirmDialog
				open={true}
				title="Clear All?"
				message="Are you sure?"
				confirmLabel="Clear All"
				cancelLabel="Cancel"
				dismissOnBackdrop
				onConfirm={onConfirm}
				onCancel={onCancel}
			/>,
		);
		await screen.findByRole("alertdialog");

		// A pointerdown anywhere outside the dialog content (the
		// dimmed backdrop). Radix AlertDialog hard-replaces any caller
		// onPointerDownOutside with preventDefault, so ConfirmDialog's
		// opt-in backdrop dismissal is its own document-level pointerdown
		// listener (containment-checked) — firing on body exercises it.
		fireEvent.pointerDown(document.body, { button: 0 });

		await waitFor(() => {
			expect(onCancel).toHaveBeenCalledTimes(1);
		});
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it("without dismissOnBackdrop, a backdrop click does NOT close (AlertDialog contract preserved)", async () => {
		const onConfirm = vi.fn();
		const onCancel = vi.fn();
		render(
			<ConfirmDialog
				open={true}
				title="Delete?"
				message="Are you sure?"
				confirmLabel="Delete"
				cancelLabel="Cancel"
				onConfirm={onConfirm}
				onCancel={onCancel}
			/>,
		);
		await screen.findByRole("alertdialog");

		fireEvent.pointerDown(document.body, { button: 0 });

		// No dismiss path (the opt-in listener is absent) — the dialog
		// stays open and neither callback fires (the user must
		// explicitly acknowledge).
		expect(screen.getByRole("alertdialog")).toBeTruthy();
		expect(onCancel).not.toHaveBeenCalled();
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it("accepts any Button variant (e.g. outline) — widened from the original destructive|warning union", async () => {
		render(
			<ConfirmDialog
				open={true}
				title="Neutral action?"
				message="Are you sure?"
				confirmLabel="Continue"
				cancelLabel="Cancel"
				variant="outline"
				onConfirm={vi.fn()}
				onCancel={vi.fn()}
			/>,
		);
		const confirmBtn = await screen.findByRole("button", { name: "Continue" });
		expect(confirmBtn).toHaveAttribute("data-variant", "outline");
	});
});
