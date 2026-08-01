/**
 * F-17: axe-core automated WCAG scan for the ConfirmDialog component.
 *
 * ConfirmDialog wraps Radix `AlertDialog` and is used by several pages
 * (Onboarding skip-confirmation, History clear-all, Vocabulary delete,
 * etc.) for destructive / irreversible actions. The existing
 * `components/common/__tests__/ConfirmDialog.test.tsx` pins the
 * behavioural contract (Confirm vs Cancel routing, variant mapping)
 * but does not run an a11y scan, so a regression like a missing dialog
 * title or an unlabelled action button would slip past CI.
 *
 * Radix AlertDialog renders its content into a Portal at
 * `document.body`, so this test runs axe against `document.body`
 * (rather than the empty react-testing-library container) to ensure
 * the dialog content is actually scanned.
 *
 * The color-contrast rule is disabled because the test environment
 * doesn't load the full Tailwind stylesheet (same approach as
 * `a11y/axe-core.test.tsx`).
 */
import { cleanup, render, screen } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import ConfirmDialog from "@/components/common/ConfirmDialog";

// Disable color-contrast — the test environment doesn't load the full
// Tailwind stylesheet, so axe's computed contrast values would be
// meaningless and produce false positives.
const AXE_OPTIONS: axe.RunOptions = {
	rules: {
		"color-contrast": { enabled: false },
	},
};

/** Axe helper — scans document.body (Radix Portal target) and filters
 *  out the disabled color-contrast rule. */
async function expectNoAxeViolationsOnBody(): Promise<void> {
	const results = await axe.run(document.body, AXE_OPTIONS);
	const violations = results.violations.filter(
		(v) => v.id !== "color-contrast",
	);
	expect(violations).toEqual([]);
}

describe("F-17: axe-core WCAG scan — ConfirmDialog (open)", () => {
	afterEach(() => {
		cleanup();
	});

	it("open dialog (destructive variant): no axe violations", async () => {
		render(
			<ConfirmDialog
				open={true}
				title="Delete vocabulary entry?"
				message="This action cannot be undone."
				confirmLabel="Delete"
				cancelLabel="Cancel"
				variant="destructive"
				onConfirm={vi.fn()}
				onCancel={vi.fn()}
			/>,
		);
		// Wait for the portal content to mount before scanning — Radix
		// AlertDialog animates in on open and the content node may not
		// be attached synchronously.
		await screen.findByRole("alertdialog");
		await expectNoAxeViolationsOnBody();
	});

	it("open dialog (warning variant): no axe violations", async () => {
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
		await screen.findByRole("alertdialog");
		await expectNoAxeViolationsOnBody();
	});
});
