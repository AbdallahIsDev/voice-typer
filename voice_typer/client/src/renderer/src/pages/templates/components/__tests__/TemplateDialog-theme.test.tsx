/**
 * Theme/wiring regression tests for the Add/Edit Template dialog.
 *
 * Pins the 2026-09-02 theme pass — the dialog must render with the
 * app's native primitives instead of the removed custom field shell:
 *
 *   (1) The panel is the roomier size="lg" + w-130 composition (520px
 *       desktop panel) — the old w-105 cramped the form.
 *   (2) The output textarea is the shared ``ui/textarea`` primitive
 *       (pill surface, ``rounded-3xl``, same focus contract as Input),
 *       not a bespoke styled raw <textarea>.
 *   (3) The match-mode trigger is the native ``SelectTrigger`` (only
 *       width overridden), matching the outline-Button pill language.
 *   (4) The unknown-variable alert uses the ``--warning`` theme token,
 *       not hardcoded ``amber-500``.
 *
 * Unlike the sibling gate tests (which mock ``Modal``), these render
 * the REAL Modal → DialogContent chain so the panel/field classes are
 * actually exercised in the DOM (Radix renders through a portal, so
 * queries go through document.body).
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n/i18n", () => {
	const tFn = (key: string, params?: Record<string, string>) => {
		const catalog: Record<string, string> = {
			"common.cancel": "Cancel",
			"common.save": "Save",
			"templates.addTitle": "Add Template",
			"templates.triggerPhrase": "Trigger phrase",
			"templates.triggerPlaceholder": "my email",
			"templates.triggerHelp": "The phrase you'll say",
			"templates.outputText": "Output text",
			"templates.outputPlaceholder": "john@example.com",
			"templates.outputHelp": "Supports variables:",
			"templates.matchMode": "Match mode",
			"templates.exactMatch": "Exact match",
			"templates.contains": "Contains",
			"templates.insertVariable": "Insert {token}",
			"a11y.moreInfoAbout": "More info about {label}",
		};
		let result = catalog[key] ?? key;
		if (params) {
			for (const [k, v] of Object.entries(params)) {
				result = result.replace(new RegExp(`\\{${k}\\}`, "g"), v);
			}
		}
		return result;
	};
	return { t: tFn, useT: () => tFn, getLocale: () => "en" };
});

import { TooltipProvider } from "@/components/ui/tooltip";

import { TemplateDialog } from "@/pages/templates/components/TemplateDialog";

function renderDialog(
	overrides?: Partial<Parameters<typeof TemplateDialog>[0]>,
) {
	const props: Parameters<typeof TemplateDialog>[0] = {
		open: true,
		editingTemplate: null,
		trigger: "",
		expansion: "",
		matchMode: "exact",
		onTriggerChange: () => {},
		onExpansionChange: () => {},
		onMatchModeChange: () => {},
		onClose: () => {},
		onSave: () => {},
		onInsertVariable: () => {},
		...overrides,
	};
	return render(
		<TooltipProvider delayDuration={200}>
			<TemplateDialog {...props} />
		</TooltipProvider>,
	);
}

afterEach(() => {
	cleanup();
});

function panel() {
	return document.body.querySelector('[data-slot="dialog-content"]');
}

describe("TemplateDialog theme wiring", () => {
	it("renders the roomier lg panel at w-130", () => {
		renderDialog();
		const el = panel();
		expect(el).toBeTruthy();
		expect(el?.getAttribute("data-size")).toBe("lg");
		expect(el?.className).toContain("w-130");
	});

	it("renders the output textarea as the shared pill Textarea primitive", () => {
		renderDialog();
		const el = document.body.querySelector('textarea[data-slot="textarea"]');
		expect(el).toBeTruthy();
		// Per-instance field polish: rounded-lg (matches the lg panel —
		// the shared primitive's default rounded-xl read too round) and a
		// softer bg-input/25 wash (the 50% was too visible on the bg
		// panel).
		expect(el?.className).toContain("rounded-lg");
		expect(el?.className).not.toContain("rounded-3xl");
		expect(el?.className).not.toContain("rounded-full");
		expect(el?.className).toContain("bg-input/25");
		expect(el?.getAttribute("id")).toBe("template-output");
	});

	it("moves the trigger description into an InfoTooltip beside the label", () => {
		renderDialog();
		// The "?" trigger is the Settings-page pattern: accessible name
		// composed as "More info about {label}".
		const tooltipTrigger = screen.getByLabelText(
			"More info about Trigger phrase",
		);
		expect(tooltipTrigger.tagName).toBe("BUTTON");
		// The description itself must NOT sit visibly in the body.
		expect(screen.queryByText("The phrase you'll say")).toBeNull();
	});

	it("splits the output helper into description row + chips row", () => {
		renderDialog();
		const desc = screen.getByText("Supports variables:");
		// Description is a standalone paragraph…
		expect(desc.tagName).toBe("P");
		// …and carries NO variable chips inside it (all chips live in
		// their own row below).
		expect(desc.querySelectorAll("button")).toHaveLength(0);
		const chips = document.body.querySelectorAll(
			'[data-slot="dialog-content"] button[title^="Insert"]',
		);
		expect(chips.length).toBe(4);
	});

	it("keeps the trigger field on the native Input primitive", () => {
		renderDialog();
		const el = document.body.querySelector('input[data-slot="input"]');
		expect(el).toBeTruthy();
		expect(el?.getAttribute("id")).toBe("template-trigger");
	});

	it("uses the shared SegmentedControl for match mode (two options, active highlighted)", () => {
		renderDialog();
		const group = document.body.querySelector('[role="radiogroup"]');
		expect(group).toBeTruthy();
		expect(group?.getAttribute("aria-label")).toBe("Match mode");
		const radios = document.body.querySelectorAll('input[type="radio"]');
		expect(radios.length).toBe(2);
		// The active option ("Exact match") is checked.
		expect((radios[0] as HTMLInputElement).checked).toBe(true);
		expect((radios[1] as HTMLInputElement).checked).toBe(false);
	});

	it("colors the unknown-variable alert with the warning token", () => {
		renderDialog({
			expansion: "hello {date}",
		});
		const alert = screen.getByRole("alert");
		expect(alert.className).toContain("text-warning");
		expect(alert.className).not.toContain("text-amber-500");
	});
});
