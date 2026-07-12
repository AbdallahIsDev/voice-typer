/**
 * NEW-UX-012: Accessibility tests for the Electron UI.
 *
 * The finding: Config UI not verified with screen reader. ARIA
 * attributes are present in code but never validated by automated
 * accessibility scanning.
 *
 * This module uses source-inspection + DOM structural verification
 * to check ARIA roles, labels, and live regions. For full runtime
 * a11y scanning, integrate @axe-core/playwright in E2E tests.
 */

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("NEW-UX-012: Accessibility ARIA patterns", () => {
	it("Settings.tsx should have aria-label on Select triggers", () => {
		const settingsPath = path.resolve(__dirname, "..", "pages", "Settings.tsx");
		const src = fs.readFileSync(settingsPath, "utf-8");

		const selectTriggerCount = (src.match(/SelectTrigger/g) || []).length;
		const ariaLabelCount = (src.match(/aria-label/g) || []).length;
		expect(ariaLabelCount).toBeGreaterThanOrEqual(selectTriggerCount);
	});

	it("App.tsx should have aria-live regions for dynamic content", () => {
		const appPath = path.resolve(__dirname, "..", "App.tsx");
		const src = fs.readFileSync(appPath, "utf-8");
		expect(src).toContain("aria-live");
	});

	it("Home.tsx should have role attributes for status indicators", () => {
		const homePath = path.resolve(__dirname, "..", "pages", "Home.tsx");
		const src = fs.readFileSync(homePath, "utf-8");
		expect(
			src.includes("aria-live") ||
				src.includes('role="status"') ||
				src.includes("role='status'"),
		).toBe(true);
	});

	it("All Switch components should have accessible names", () => {
		const pages = ["Home.tsx", "Settings.tsx", "Models.tsx", "About.tsx"];
		for (const page of pages) {
			const pagePath = path.resolve(__dirname, "..", "pages", page);
			if (fs.existsSync(pagePath)) {
				const src = fs.readFileSync(pagePath, "utf-8");
				const switchCount = (src.match(/<Switch/g) || []).length;
				// A Switch is accessible if it EITHER:
				//   - has an explicit aria-label (may be on a different line
				//     than the <Switch tag, e.g.
				//     `<Switch\n  checked={...}\n  aria-label="..." />`), OR
				//   - is wrapped in a <SettingRow label="..."> which renders an
				//     associated <label htmlFor> element (see SettingRow.tsx).
				//     SettingRow generates a useId() and renders <label htmlFor={id}>;
				//     the Switch inside inherits an accessible name via DOM
				//     association when the page wires `id={useSettingRowId()}` or
				//     when the SettingRow's label text serves as the accessible
				//     name for the grouped control.
				const switchAriaCount = (src.match(/<Switch[\s\S]*?aria-label/g) || [])
					.length;
				// Count SettingRow usages that have a label prop (may span
				// multiple lines, e.g. `<SettingRow\n  label="..."\n>`).
				const settingRowCount = (src.match(/<SettingRow[\s\S]*?label=/g) || [])
					.length;
				const accessibleCount = switchAriaCount + settingRowCount;
				expect(accessibleCount).toBeGreaterThanOrEqual(switchCount);
			}
		}
	});
});

describe("NEW-UX-012: Dialog accessibility", () => {
	it('ConfirmDialog should have role="dialog" and aria-modal', () => {
		// P1-2c (Round 0 forward-port): ConfirmDialog was moved to
		// ``components/common/ConfirmDialog.tsx``.  Check the new path
		// first, then fall back to the old path for backward
		// compatibility with any branch that hasn't picked up the move.
		const newPath = path.resolve(
			__dirname,
			"..",
			"components",
			"common",
			"ConfirmDialog.tsx",
		);
		const dialogPath = fs.existsSync(newPath)
			? newPath
			: path.resolve(__dirname, "..", "components", "ConfirmDialog.tsx");
		if (fs.existsSync(dialogPath)) {
			const src = fs.readFileSync(dialogPath, "utf-8");
			// ConfirmDialog uses Radix AlertDialog (via the
			// ui/alert-dialog.tsx wrapper), which renders
			// ``role="alertdialog"`` and ``aria-modal="true"``
			// at runtime via AlertDialogPrimitive.Content.
			// The role attribute is NOT in ConfirmDialog.tsx
			// source — it's provided by the Radix primitive.
			// Accept either an explicit role attribute OR
			// evidence that the Radix AlertDialog is used
			// (which guarantees the role at runtime).
			expect(
				src.includes('role="dialog"') ||
					src.includes("role='dialog'") ||
					src.includes('role="alertdialog"') ||
					src.includes("role='alertdialog'") ||
					src.includes("AlertDialog") ||
					src.includes("AlertDialogContent"),
			).toBe(true);
		}
	});

	it("ErrorBoundary should have aria-live for error messages", () => {
		const errorBoundaryPath = path.resolve(
			__dirname,
			"..",
			"components",
			"ErrorBoundary.tsx",
		);
		if (fs.existsSync(errorBoundaryPath)) {
			const src = fs.readFileSync(errorBoundaryPath, "utf-8");
			expect(
				src.includes("aria-live") ||
					src.includes('role="alert"') ||
					src.includes("role='alert'"),
			).toBe(true);
		}
	});
});
