/**
 * Unsaved-edits gate on the Template dialog.
 *
 * Closing the dialog via Escape / overlay / corner X while the form
 * holds unsaved content must first ask the user to confirm the
 * discard (ConfirmDialog "discard changes" preset) instead of
 * silently throwing the edits away. A clean form (no edits) closes
 * immediately.
 *
 * The dialog is a pure presentational wrapper, so the tests render it
 * with stub props and drive the REAL Modal + ConfirmDialog (only the
 * leaf UI primitives are mocked, mirroring the sibling dialog test).
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

vi.mock("@/i18n/i18n", () => ({
	t: (key: string, params?: Record<string, string>) => {
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
			"templates.unknownVariableWarning":
				"Unknown variable {vars} — supported: {today}, {now}, {clipboard}, {username}",
			"dialog.discardChangesTitle": "Discard changes?",
			"dialog.discardChangesMessage":
				"Closing now will discard your unsaved edits.",
			"dialog.discardChangesConfirm": "Discard changes",
			"dialog.discardChangesStay": "Keep editing",
		};
		let result = catalog[key] ?? key;
		if (params) {
			for (const [k, v] of Object.entries(params)) {
				result = result.replace(new RegExp(`\\{${k}\\}`, "g"), v);
			}
		}
		return result;
	},
}));

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

import { TemplateDialog } from "@/pages/templates/components/TemplateDialog";
import type { TemplateRow } from "@/pages/templates/lib/types";

function makeRow(overrides: Partial<TemplateRow> = {}): TemplateRow {
	return {
		index: 0,
		id: "row-1",
		trigger: "email",
		expansion: "john@example.com",
		match_mode: "exact",
		variables: 0,
		used_variables: [],
		...overrides,
	};
}

function renderDialog(overrides: Record<string, unknown> = {}) {
	const onClose = vi.fn();
	const onSave = vi.fn();
	render(
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
		/>,
	);
	return { onClose, onSave };
}

describe("TemplateDialog — unsaved-edits close gate", () => {
	afterEach(() => {
		cleanup();
	});

	it("clean add form: Escape closes immediately without a confirm", async () => {
		const user = userEvent.setup();
		const { onClose } = renderDialog();
		await screen.findByRole("dialog");
		await user.keyboard("{Escape}");
		expect(onClose).toHaveBeenCalledTimes(1);
		expect(screen.queryByText("Discard changes?")).toBeNull();
	});

	it("add form with typed content: Escape opens the discard confirm instead of closing", async () => {
		const user = userEvent.setup();
		const { onClose } = renderDialog({
			trigger: "sig",
			expansion: "Best regards",
		});
		await screen.findByRole("dialog");
		await user.keyboard("{Escape}");
		expect(onClose).not.toHaveBeenCalled();
		// The confirm presents as an alertdialog (Radix marks the
		// underlying edit dialog aria-hidden while the alert layer is up).
		const confirm = await screen.findByRole("alertdialog");
		expect(confirm).toHaveTextContent("Discard changes?");
	});

	it("confirming the discard closes the edit dialog", async () => {
		const user = userEvent.setup();
		const { onClose } = renderDialog({
			trigger: "sig",
			expansion: "Best regards",
		});
		await screen.findByRole("dialog");
		await user.keyboard("{Escape}");
		await screen.findByRole("alertdialog");
		fireEvent.click(screen.getByText("Discard changes"));
		await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
	});

	it("staying on the dialog keeps both the edit dialog and its content", async () => {
		const user = userEvent.setup();
		const { onClose } = renderDialog({
			trigger: "sig",
			expansion: "Best regards",
		});
		await screen.findByRole("dialog");
		await user.keyboard("{Escape}");
		await screen.findByRole("alertdialog");
		fireEvent.click(screen.getByText("Keep editing"));
		await waitFor(() =>
			expect(screen.queryByText("Discard changes?")).toBeNull(),
		);
		expect(onClose).not.toHaveBeenCalled();
	});

	it("edit form matching the saved template: Escape closes without a confirm", async () => {
		const user = userEvent.setup();
		const { onClose } = renderDialog({
			editingTemplate: makeRow(),
			trigger: "email",
			expansion: "john@example.com",
			matchMode: "exact",
		});
		await screen.findByRole("dialog");
		await user.keyboard("{Escape}");
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it("edited field differing from the saved template triggers the confirm", async () => {
		const { onClose } = renderDialog({
			editingTemplate: makeRow(),
			trigger: "email2",
			expansion: "john@example.com",
			matchMode: "exact",
		});
		await screen.findByRole("dialog");
		fireEvent.keyDown(document.activeElement ?? document.body, {
			key: "Escape",
		});
		await waitFor(() =>
			expect(screen.getByText("Discard changes?")).toBeInTheDocument(),
		);
		expect(onClose).not.toHaveBeenCalled();
	});
});
