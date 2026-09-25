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

describe("TemplateDialog theme wiring", () => {
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
