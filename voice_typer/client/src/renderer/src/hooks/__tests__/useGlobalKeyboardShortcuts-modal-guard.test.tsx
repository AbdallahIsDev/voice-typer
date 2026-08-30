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
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Shared stable-mocks singleton: the sonner `toast.error` wired into
// the stableMocks error channel so the tests can assert on the
// loud/silent error surfaces across resets.
import { sonnerMock, stableMocks } from "@/__tests__/helpers/stableMocks";

import { useGlobalKeyboardShortcuts } from "@/hooks/useGlobalKeyboardShortcuts";

const { mockToastError } = stableMocks;

vi.mock("sonner", () => sonnerMock({ errorTo: "mockToastError" }));

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
	const result = render(<Probe />);
	return { result, setTextSize };
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

function dispatchWheel(deltaY: number) {
	fireEvent.wheel(window, { ctrlKey: true, deltaY });
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
	mockCall.mockResolvedValue(undefined);
	mockT.mockClear();
	mockToastError.mockClear();
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

	it("Ctrl++ (zoom in via the '+' alternative key) fires the same set_config", () => {
		// The catalog pins eventKeys ["=", "+"] — the "+" form is the
		// unshifted alternative on some layouts. Both must dispatch to
		// the same zoom-in handler.
		renderHook();
		dispatchKey("+");
		expect(mockCall).toHaveBeenCalledWith("set_config", { text_size: 15 });
	});

	it("Ctrl+Wheel set_config failure stays SILENT (documented wheel contract)", async () => {
		// The wheel error path swallows set_config failures — the key
		// shortcuts toast, the wheel does not (matches the original
		// inline effect). Pinned here so a refactor can't re-unify the
		// two error surfaces.
		renderHook();
		mockCall.mockRejectedValueOnce(new Error("backend restart"));
		dispatchWheel(-1);
		// Let the rejected promise's catch run.
		await new Promise((resolve) => setTimeout(resolve, 0));
		expect(mockCall).toHaveBeenCalledWith("set_config", { text_size: 15 });
		expect(mockToastError).not.toHaveBeenCalled();
	});

	it("Ctrl+= set_config failure surfaces a toast (key path is loud)", async () => {
		// The asymmetry pinned by the test above: key shortcuts DO toast
		// on set_config failure. Guards the loud path from being
		// accidentally silenced too.
		renderHook();
		mockCall.mockRejectedValueOnce(new Error("backend restart"));
		dispatchKey("=");
		await waitFor(() => {
			expect(mockToastError).toHaveBeenCalledWith("errorBoundary.unknownError");
		});
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

describe("useGlobalKeyboardShortcuts — rapid consecutive zoom events", () => {
	// The bumpTextSize body mirrors textSize into a ref and advances it
	// SYNCHRONOUSLY, so a burst of events between renders accumulates
	// (14 → 15 → 16) instead of replaying the stale rendered value.
	// The harness never re-renders between the two events (setTextSize
	// is a bare mock that doesn't change state), which is exactly the
	// window the ref mirror covers.
	it("accumulates two consecutive Ctrl+Wheel zoom-in events (14 → 15 → 16)", () => {
		const { setTextSize } = renderHook(14);
		dispatchWheel(-1);
		dispatchWheel(-1);
		expect(setTextSize).toHaveBeenNthCalledWith(1, 15);
		expect(setTextSize).toHaveBeenNthCalledWith(2, 16);
		expect(mockCall).toHaveBeenNthCalledWith(2, "set_config", {
			text_size: 16,
		});
	});

	it("accumulates two consecutive Ctrl+= key events (14 → 15 → 16)", () => {
		const { setTextSize } = renderHook(14);
		dispatchKey("=");
		dispatchKey("=");
		expect(setTextSize).toHaveBeenNthCalledWith(1, 15);
		expect(setTextSize).toHaveBeenNthCalledWith(2, 16);
	});

	it("accumulates two consecutive Ctrl+Wheel zoom-out events (14 → 13 → 12)", () => {
		const { setTextSize } = renderHook(14);
		dispatchWheel(1);
		dispatchWheel(1);
		expect(setTextSize).toHaveBeenNthCalledWith(1, 13);
		expect(setTextSize).toHaveBeenNthCalledWith(2, 12);
	});
});
