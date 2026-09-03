/**
 * Footer Cancel routes through the unsaved-edits gate.
 *
 * Previously the footer Cancel button called `onClose` directly,
 * bypassing the Modal `onCloseIntent` veto — an explicit Cancel click
 * after edits silently discarded them, inconsistent with Esc / overlay
 * / corner-X which open the discard confirm. Pinned contract:
 *   - Clean form → Cancel closes immediately.
 *   - Form with edits → Cancel opens the discard confirm instead.
 *   - Confirming the discard closes the dialog.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n/i18n", () => {
	const tFn = (key: string) => {
		const catalog: Record<string, string> = {
			"common.cancel": "Cancel",
			"common.save": "Save",
			"templates.addTitle": "Add Template",
			"templates.editTitle": "Edit Template",
			"templates.triggerPhrase": "Trigger phrase",
			"templates.triggerPlaceholder": "my email",
			"templates.triggerHelp": "The phrase you'll say",
			"templates.outputText": "Output text",
			"templates.outputPlaceholder": "john@example.com",
			"templates.outputHelp": "Supports variables:",
			"templates.matchMode": "Match mode",
			"templates.exactMatch": "Exact match",
			"templates.contains": "Contains",
			"templates.unknownVariableWarning": "Unknown variable {vars}",
			"dialog.discardChangesTitle": "Discard changes?",
			"dialog.discardChangesMessage":
				"Closing now will discard your unsaved edits.",
			"dialog.discardChangesConfirm": "Discard changes",
			"dialog.discardChangesStay": "Keep editing",
		};
		return catalog[key] ?? key;
	};
	return { t: tFn, useT: () => tFn, getLocale: () => "en" };
});

vi.mock("@/components/ui/button", () => ({
	Button: ({
		children,
		onClick,
		disabled,
		variant,
	}: {
		children: React.ReactNode;
		onClick?: () => void;
		disabled?: boolean;
		variant?: string;
	}) => (
		<button
			type="button"
			onClick={onClick}
			disabled={disabled}
			data-variant={variant}
		>
			{children}
		</button>
	),
}));

vi.mock("@/components/ui/input", () => ({
	Input: ({
		value,
		onChange,
		placeholder,
		id,
	}: {
		value: string;
		onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
		placeholder?: string;
		id?: string;
	}) => (
		<input
			id={id}
			value={value}
			onChange={onChange}
			placeholder={placeholder}
		/>
	),
}));

import { TooltipProvider } from "@/components/ui/tooltip";

import { TemplateDialog } from "@/pages/templates/components/TemplateDialog";

function renderDialog(overrides: Record<string, unknown> = {}) {
	const onClose = vi.fn();
	const onSave = vi.fn();
	render(
		<TooltipProvider delayDuration={200}>
			<TemplateDialog
				open={true}
				editingTemplate={null}
				trigger=""
				expansion=""
				matchMode="exact"
				onTriggerChange={vi.fn()}
				onExpansionChange={vi.fn()}
				onMatchModeChange={vi.fn()}
				onClose={onClose}
				onSave={onSave}
				onInsertVariable={vi.fn()}
				{...overrides}
			/>
		</TooltipProvider>,
	);
	return { onClose, onSave };
}

describe("TemplateDialog — footer Cancel routes through the unsaved-edits gate", () => {
	afterEach(() => {
		cleanup();
	});

	it("clean form: Cancel closes immediately without a confirm", () => {
		const { onClose } = renderDialog();
		fireEvent.click(screen.getByText("Cancel"));
		expect(onClose).toHaveBeenCalledTimes(1);
		expect(screen.queryByText("Discard changes?")).toBeNull();
	});

	it("form with edits: Cancel opens the discard confirm instead of closing", async () => {
		const { onClose } = renderDialog({
			trigger: "sig",
			expansion: "Best regards",
		});
		await screen.findByRole("dialog");
		fireEvent.click(screen.getByText("Cancel"));
		expect(onClose).not.toHaveBeenCalled();
		const confirm = await screen.findByRole("alertdialog");
		expect(confirm).toHaveTextContent("Discard changes?");
	});

	it("confirming the discard closes the dialog", async () => {
		const { onClose } = renderDialog({
			trigger: "sig",
			expansion: "Best regards",
		});
		await screen.findByRole("dialog");
		fireEvent.click(screen.getByText("Cancel"));
		await screen.findByRole("alertdialog");
		fireEvent.click(screen.getByText("Discard changes"));
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it("staying keeps the dialog open", async () => {
		const { onClose } = renderDialog({
			trigger: "sig",
			expansion: "Best regards",
		});
		await screen.findByRole("dialog");
		fireEvent.click(screen.getByText("Cancel"));
		await screen.findByRole("alertdialog");
		fireEvent.click(screen.getByText("Keep editing"));
		expect(onClose).not.toHaveBeenCalled();
	});
});
