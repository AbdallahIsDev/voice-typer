import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sonnerMock, stableMocks } from "@/__tests__/helpers/stableMocks";
import { useGlobalKeyboardShortcuts } from "@/hooks/useGlobalKeyboardShortcuts";

const { mockToastError } = stableMocks;

vi.mock("sonner", () => sonnerMock({ errorTo: "mockToastError" }));

const mockNavigate = vi.fn();
const mockSetSidebarCollapsed = vi.fn();
const mockSetTextSize = vi.fn();
const mockCall = vi.fn().mockResolvedValue(undefined);
const mockT = vi.fn((key: string) => key);

function renderHook() {
	function Probe() {
		useGlobalKeyboardShortcuts({
			navigate: mockNavigate,
			textSize: 14,
			setTextSize: mockSetTextSize,
			call: mockCall,
			t: mockT,
			setSidebarCollapsed: mockSetSidebarCollapsed,
		});
		return null;
	}
	render(<Probe />);
}

function dispatchKey(
	key: string,
	opts: { ctrl?: boolean; meta?: boolean; shift?: boolean; alt?: boolean } = {},
) {
	fireEvent.keyDown(window, {
		key,
		ctrlKey: opts.ctrl ?? true,
		metaKey: opts.meta ?? false,
		shiftKey: opts.shift ?? true,
		altKey: opts.alt ?? false,
	});
}

beforeEach(() => {
	mockNavigate.mockClear();
	mockSetSidebarCollapsed.mockClear();
	mockSetTextSize.mockClear();
	mockCall.mockClear();
	mockCall.mockResolvedValue(undefined);
	mockT.mockClear();
	mockToastError.mockClear();
});

afterEach(() => {
	cleanup();
});

describe("useGlobalKeyboardShortcuts, Ctrl+Shift+M toggle dictation", () => {
	it("Ctrl+Shift+M calls toggle_dictation (the mic-button IPC path)", () => {
		renderHook();
		dispatchKey("M");
		expect(mockCall).toHaveBeenCalledWith("toggle_dictation");
	});

	it("Cmd+Shift+M fires on macOS-style meta modifier", () => {
		renderHook();
		dispatchKey("M", { ctrl: false, meta: true });
		expect(mockCall).toHaveBeenCalledWith("toggle_dictation");
	});

	it("surfaces a rejection via toast.error (no silent swallow)", async () => {
		renderHook();
		mockCall.mockRejectedValueOnce(new Error("backend refused"));
		dispatchKey("M");
		await waitFor(() => {
			expect(mockToastError).toHaveBeenCalled();
		});
		expect(mockT).toHaveBeenCalledWith("home.toggleFailed");
	});

	it("is NOT typing-gated (dictating into a focused field is legitimate)", () => {
		renderHook();
		const input = document.createElement("input");
		document.body.appendChild(input);
		fireEvent.keyDown(input, {
			key: "M",
			ctrlKey: true,
			shiftKey: true,
			altKey: false,
			metaKey: false,
			bubbles: true,
		});
		expect(mockCall).toHaveBeenCalledWith("toggle_dictation");
	});

	it("is NOT modal-gated (works while a dialog is open)", () => {
		renderHook();
		const dialog = document.createElement("div");
		dialog.setAttribute("role", "dialog");
		dialog.setAttribute("data-state", "open");
		document.body.appendChild(dialog);
		dispatchKey("M");
		expect(mockCall).toHaveBeenCalledWith("toggle_dictation");
	});

	it("plain Ctrl+M (no Shift) does NOT fire the binding", () => {
		renderHook();
		dispatchKey("m", { shift: false });
		expect(mockCall).not.toHaveBeenCalledWith("toggle_dictation");
	});

	it("Ctrl+Alt+M does NOT fire (Alt excluded from the profile)", () => {
		renderHook();
		dispatchKey("M", { alt: true });
		expect(mockCall).not.toHaveBeenCalledWith("toggle_dictation");
	});
});
