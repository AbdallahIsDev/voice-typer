/**
 * Tests for useGlobalKeyboardShortcuts — covers the modal-open guard
 * added for the Ctrl+B / Ctrl+, / Ctrl+H navigation shortcuts.
 *
 * Strategy: render a harness component that calls the hook, then
 * dispatch KeyboardEvent objects on `window` and assert whether the
 * navigate / setSidebarCollapsed callbacks fired. When a Radix Dialog
 * is "open" (simulated by appending a `[role="dialog"][data-state="open"]`
 * element to the DOM), the navigation shortcuts MUST be suppressed so
 * the user isn't routed away from a modal they're actively in.
 *
 * The zoom shortcuts (Ctrl+= / Ctrl+-) are intentionally NOT guarded —
 * they're page-zoom semantics that apply regardless of modal state.
 */
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useGlobalKeyboardShortcuts } from "@/hooks/useGlobalKeyboardShortcuts";

const mockNavigate = vi.fn();
const mockSetSidebarCollapsed = vi.fn(
	(cb: ((c: boolean) => boolean) | boolean) => {
		// Apply the updater so the toggling behaviour is observable;
		// also tolerate a direct boolean value (the SetStateAction
		// union includes both shapes).
		return typeof cb === "function" ? cb(false) : cb;
	},
);
const mockCall = vi.fn().mockResolvedValue(undefined);
const mockT = vi.fn((key: string) => key);

vi.mock("sonner", () => ({
	toast: { error: vi.fn() },
}));

function renderHook(textSize: number | null = 14) {
	const setTextSize = vi.fn();
	function Probe() {
		useGlobalKeyboardShortcuts({
			navigate: mockNavigate,
			textSize,
			setTextSize,
			call: mockCall,
			t: mockT,
			setSidebarCollapsed: mockSetSidebarCollapsed,
		});
		return null;
	}
	return render(<Probe />);
}

function dispatchKey(
	key: string,
	opts: { ctrl?: boolean; meta?: boolean; shift?: boolean; alt?: boolean } = {},
) {
	fireEvent.keyDown(window, {
		key,
		ctrlKey: opts.ctrl ?? true,
		metaKey: opts.meta ?? false,
		shiftKey: opts.shift ?? false,
		altKey: opts.alt ?? false,
	});
}

function attachOpenDialog() {
	const el = document.createElement("div");
	el.setAttribute("role", "dialog");
	el.setAttribute("data-state", "open");
	document.body.appendChild(el);
	return el;
}

beforeEach(() => {
	mockNavigate.mockClear();
	mockSetSidebarCollapsed.mockClear();
	mockCall.mockClear();
	mockT.mockClear();
});

afterEach(() => {
	cleanup();
	// Remove any leftover dialogs.
	document
		.querySelectorAll('[role="dialog"][data-state="open"]')
		.forEach((el) => {
			el.remove();
		});
});

describe("useGlobalKeyboardShortcuts — modal-open guard", () => {
	it("Ctrl+B toggles the sidebar when NO modal is open", () => {
		renderHook();
		dispatchKey("b");
		expect(mockSetSidebarCollapsed).toHaveBeenCalledTimes(1);
	});

	it("Ctrl+B is suppressed when a Radix Dialog is open", () => {
		renderHook();
		const dialog = attachOpenDialog();
		dispatchKey("b");
		expect(mockSetSidebarCollapsed).not.toHaveBeenCalled();
		dialog.remove();
	});

	it("Ctrl+, navigates to settings when NO modal is open", () => {
		renderHook();
		dispatchKey(",");
		expect(mockNavigate).toHaveBeenCalledWith("settings");
	});

	it("Ctrl+, is suppressed when a Radix Dialog is open", () => {
		renderHook();
		const dialog = attachOpenDialog();
		dispatchKey(",");
		expect(mockNavigate).not.toHaveBeenCalled();
		dialog.remove();
	});

	it("Ctrl+H is suppressed when a Radix Dialog is open", () => {
		renderHook();
		const dialog = attachOpenDialog();
		dispatchKey("h");
		expect(mockNavigate).not.toHaveBeenCalled();
		dialog.remove();
	});

	it("Ctrl+= (zoom in) still fires when a modal is open (zoom is not gated)", () => {
		renderHook();
		const dialog = attachOpenDialog();
		dispatchKey("=");
		expect(mockCall).toHaveBeenCalledWith("set_config", { text_size: 15 });
		dialog.remove();
	});

	it("Ctrl+- (zoom out) still fires when a modal is open (zoom is not gated)", () => {
		renderHook(14);
		const dialog = attachOpenDialog();
		dispatchKey("-");
		expect(mockCall).toHaveBeenCalledWith("set_config", { text_size: 13 });
		dialog.remove();
	});

	it("does not gate when a closed dialog is present (data-state != open)", () => {
		renderHook();
		const el = document.createElement("div");
		el.setAttribute("role", "dialog");
		el.setAttribute("data-state", "closed");
		document.body.appendChild(el);
		dispatchKey("b");
		expect(mockSetSidebarCollapsed).toHaveBeenCalledTimes(1);
		el.remove();
	});
});
