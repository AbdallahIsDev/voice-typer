/**
 * Regression test for the template save-toast ordering bug (2026-08-28).
 *
 * Root cause: `useTemplateDialog.saveTemplate` showed the SUCCESS toast
 * BEFORE awaiting the IPC save. When the backend rejected the write
 * (e.g. `'output' value too long in templates[3] (32913 > 1024)` — the
 * backend caps template output at 1024 chars), the success toast had
 * already fired, then the catch block queued an error toast too — so
 * the user saw a green "Template added" AND a red "Failed to save
 * template" simultaneously, and the template never appeared.
 *
 * Fix: the success toast now fires AFTER `await saveTemplates(...)`
 * resolves, and the error toast surfaces the backend's rejection reason
 * (err.message) instead of the opaque generic "Failed to save template".
 */
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
	pythonMock,
	snackbarMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";
import { useTemplateDialog } from "@/pages/templates/hooks/useTemplateDialog";

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useSnackbar", () => snackbarMock());
vi.mock("@/i18n/i18n", () => ({
	t: (key: string, params?: Record<string, string>) => {
		const catalog: Record<string, string> = {
			"templates.addedTemplate": "Template added: {name}",
			"templates.updatedTemplate": "Template updated: {name}",
			"templates.duplicateTrigger":
				"A template with this trigger and match mode already exists",
			"templates.fillBothFields": "Please fill in both fields",
			"templates.saveFailed": "Failed to save template",
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

const showSnack = stableMocks.showSnack;
const mockCall = stableMocks.mockCall;

const templatesRef = { current: [] } as React.RefObject<
	{ id: string; trigger: string; expansion: string; match_mode: string }[]
>;

function setup() {
	return renderHook(() =>
		useTemplateDialog({
			call: mockCall as never,
			showSnack,
			templatesRef: templatesRef as never,
			loadRows: vi.fn().mockResolvedValue(undefined),
		}),
	);
}

describe("useTemplateDialog.saveTemplate — toast ordering", () => {
	beforeEach(() => {
		showSnack.mockReset();
		mockCall.mockReset();
	});

	it("fires NO success toast when the backend rejects the save", async () => {
		// Seed one existing template + the ref list it mutates.
		templatesRef.current = [
			{
				id: "1",
				trigger: "brb",
				expansion: "be right back",
				match_mode: "exact",
			},
		];
		// Backend rejects: output exceeds the 1024-char cap.
		mockCall.mockRejectedValue(
			new Error("'output' value too long in templates[1] (5000 > 1024)"),
		);

		const { result } = setup();
		act(() => result.current.openAddDialog());
		act(() =>
			result.current.handleTriggerChange({
				target: { value: "x" },
			} as React.ChangeEvent<HTMLInputElement>),
		);
		act(() =>
			result.current.handleExpansionChange({
				target: { value: "y".repeat(5000) },
			} as React.ChangeEvent<HTMLTextAreaElement>),
		);

		await act(async () => {
			await result.current.saveTemplate();
		});

		// Only the ERROR toast fires — never a success toast for a
		// rejected write (the old bug showed both simultaneously).
		const successCalls = showSnack.mock.calls.filter(
			(call) => call[1] === "success",
		);
		expect(successCalls).toHaveLength(0);

		// The error toast surfaces the BACKEND reason, not the opaque
		// generic message.
		expect(showSnack).toHaveBeenCalledWith(
			"'output' value too long in templates[1] (5000 > 1024)",
			"error",
		);
	});

	it("fires the success toast ONLY after the IPC save resolves", async () => {
		templatesRef.current = [];
		let resolveSave: (v: unknown) => void = () => {};
		mockCall.mockImplementation(
			() =>
				new Promise((resolve) => {
					resolveSave = resolve;
				}),
		);

		const { result } = setup();
		act(() => result.current.openAddDialog());
		act(() =>
			result.current.handleTriggerChange({
				target: { value: "hi" },
			} as React.ChangeEvent<HTMLInputElement>),
		);
		act(() =>
			result.current.handleExpansionChange({
				target: { value: "hello" },
			} as React.ChangeEvent<HTMLTextAreaElement>),
		);

		// Start the save but DON'T resolve the IPC yet.
		let pending: Promise<void> = Promise.resolve();
		act(() => {
			pending = result.current.saveTemplate();
		});

		// While the IPC save is in flight, NO toast of any kind should
		// have fired.
		expect(showSnack).not.toHaveBeenCalled();

		// Resolve the IPC save → only NOW does the success toast fire.
		await act(async () => {
			resolveSave({ success: true });
			await pending;
		});

		expect(showSnack).toHaveBeenCalledWith("Template added: hi", "success");
	});
});
