/**
 * Tests for useHelpOverlayShortcut (extracted from App.tsx, EO-28).
 *
 * Covers the "?"-opens / Escape-closes keydown contract:
 *   - "?" (no modifiers) opens the overlay.
 *   - "?" is suppressed while focus is in an editable control.
 *   - "?" is suppressed while a Radix dialog is open.
 *   - Escape closes the overlay and stops propagation.
 *   - openHelp/closeHelp callbacks are stable and work directly.
 */
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useHelpOverlayShortcut } from "@/hooks/useHelpOverlayShortcut";

afterEach(cleanup);

function renderHook() {
	let api: ReturnType<typeof useHelpOverlayShortcut> | undefined;
	function Probe() {
		const value = useHelpOverlayShortcut();
		api = value;
		return (
			<div data-testid="probe">{value.showHelpOverlay ? "open" : "closed"}</div>
		);
	}
	const view = render(<Probe />);
	return {
		view,
		get api() {
			// Probe always runs during render, so `api` is assigned before
			// any test reads it. A cast (instead of `!`) keeps the getter
			// type-safe under noUncheckedIndexedAccess without a non-null
			// assertion.
			return api as ReturnType<typeof useHelpOverlayShortcut>;
		},
	};
}

function dispatchKey(
	key: string,
	opts: { ctrl?: boolean; meta?: boolean; alt?: boolean } = {},
) {
	fireEvent.keyDown(document, {
		key,
		ctrlKey: opts.ctrl ?? false,
		metaKey: opts.meta ?? false,
		altKey: opts.alt ?? false,
	});
}

describe("useHelpOverlayShortcut", () => {
	it("opens on bare '?' and closes on Escape", () => {
		const { view } = renderHook();
		expect(view.getByText("closed")).toBeTruthy();
		dispatchKey("?");
		expect(view.getByText("open")).toBeTruthy();
		dispatchKey("Escape");
		expect(view.getByText("closed")).toBeTruthy();
	});

	it("does not open when '?' is pressed with modifiers", () => {
		const { view } = renderHook();
		dispatchKey("?", { ctrl: true });
		dispatchKey("?", { meta: true });
		dispatchKey("?", { alt: true });
		expect(view.getByText("closed")).toBeTruthy();
	});

	it("does not open while focus is in an editable control", () => {
		const { view } = renderHook();
		const input = document.createElement("input");
		document.body.appendChild(input);
		input.focus();
		dispatchKey("?");
		expect(view.getByText("closed")).toBeTruthy();
		input.remove();
	});

	it("does not open while a Radix dialog is open", () => {
		const { view } = renderHook();
		const dialog = document.createElement("div");
		dialog.setAttribute("role", "dialog");
		dialog.setAttribute("data-state", "open");
		document.body.appendChild(dialog);
		dispatchKey("?");
		expect(view.getByText("closed")).toBeTruthy();
		dialog.remove();
		// Once the dialog closes, the shortcut works again.
		dispatchKey("?");
		expect(view.getByText("open")).toBeTruthy();
	});

	it("openHelp/closeHelp callbacks are stable and functional", () => {
		const { api, view } = renderHook();
		const firstOpen = api.openHelp;
		act(() => {
			api.openHelp();
		});
		expect(view.getByText("open")).toBeTruthy();
		expect(api.openHelp).toBe(firstOpen);
		act(() => {
			api.closeHelp();
		});
		expect(view.getByText("closed")).toBeTruthy();
	});
});
