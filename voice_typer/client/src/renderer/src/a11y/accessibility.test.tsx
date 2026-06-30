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
				const switchAriaCount = (src.match(/Switch[^>]*aria-label/g) || [])
					.length;
				expect(switchAriaCount).toBeGreaterThanOrEqual(switchCount);
			}
		}
	});
});

describe("NEW-UX-012: Dialog accessibility", () => {
	it('ConfirmDialog should have role="dialog" and aria-modal', () => {
		const dialogPath = path.resolve(
			__dirname,
			"..",
			"components",
			"ConfirmDialog.tsx",
		);
		if (fs.existsSync(dialogPath)) {
			const src = fs.readFileSync(dialogPath, "utf-8");
			expect(
				src.includes('role="dialog"') ||
					src.includes("role='dialog'") ||
					src.includes('role="alertdialog"') ||
					src.includes("role='alertdialog'"),
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
