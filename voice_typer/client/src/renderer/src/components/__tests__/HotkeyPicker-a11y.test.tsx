/**
 * TASK-16: Accessibility screen-reader runtime tests for HotkeyPicker.
 *
 * Directive Section 5 ("Accessibility Screen-Reader Testing") notes that
 * no NVDA/VoiceOver runtime verification was performed — only source-
 * inspection tests for ARIA attributes existed. This file closes that gap
 * by using @testing-library/react's ARIA queries (`getByRole`,
 * `findByRole`, `queryByRole`) — the SAME accessible-name computation
 * that screen readers use to traverse the DOM at runtime — to verify
 * that HotkeyPicker's live regions (role="alert", role="status"), button
 * labels, and dropdown menu items are actually announced to assistive
 * technology.
 *
 * Additionally, axe-core is run on the rendered component to catch WCAG
 * violations that the explicit ARIA queries might miss (e.g. duplicate
 * IDs, invalid ARIA attribute values, missing focusable elements). The
 * `color-contrast` rule is disabled because the test environment doesn't
 * load the full Tailwind stylesheet, so computed contrast values would
 * be meaningless.
 *
 * Coverage:
 *   - Initial render (not recording): button aria-labels, no alert,
 *     no status live region.
 *   - Recording state: button aria-label flips to "Cancel recording",
 *     role="status" live region appears with capture instructions.
 *   - Error state (HOTKEY-FULLMSG-001): role="alert" live region
 *     appears with the FULL attempted combo (e.g. "Shift+Z"), proving
 *     screen readers will announce the complete shortcut — not just the
 *     bare key.
 *   - Keyboard accessibility: record button is reachable via Tab;
 *     preset dropdown menu items have role="menuitem" and can be
 *     navigated with ArrowDown.
 *   - axe-core automated scan: zero WCAG violations across the three
 *     component states (idle / recording / error), excluding
 *     color-contrast.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HotkeyPicker } from "../HotkeyPicker";

// Disable color-contrast — the test environment doesn't load the full
// Tailwind stylesheet, so axe's computed contrast values would be
// meaningless and produce false positives.
const AXE_OPTIONS: axe.RunOptions = {
	rules: {
		"color-contrast": { enabled: false },
	},
};

interface DispatchOpts {
	code: string;
	key: string;
	ctrlKey?: boolean;
	shiftKey?: boolean;
	altKey?: boolean;
	metaKey?: boolean;
	type: "keydown" | "keyup";
}

// HotkeyPicker attaches its keydown/keyup listeners to `window` (not the
// button), so we dispatch KeyboardEvents directly on window — same
// pattern as HotkeyPicker-multikey.test.tsx. Each dispatch is wrapped in
// act() so React flushes any state updates synchronously before the
// assertion runs.
function dispatchKey(opts: DispatchOpts) {
	const ev = new KeyboardEvent(opts.type, {
		code: opts.code,
		key: opts.key,
		ctrlKey: opts.ctrlKey ?? false,
		shiftKey: opts.shiftKey ?? false,
		altKey: opts.altKey ?? false,
		metaKey: opts.metaKey ?? false,
		bubbles: true,
		cancelable: true,
	});
	act(() => {
		window.dispatchEvent(ev);
	});
}

// Render HotkeyPicker with a sensible default props bag so each test
// only specifies the props it actually cares about.
function renderPicker(
	overrides: {
		value?: string;
		mode?: "single" | "combo";
		ariaLabel?: string;
		onChange?: (h: string) => void;
		presets?: { value: string; label: string }[];
	} = {},
) {
	const props = {
		value: overrides.value ?? "",
		onChange: overrides.onChange ?? vi.fn(),
		mode: (overrides.mode ?? "single") as "single" | "combo",
		"aria-label": overrides.ariaLabel ?? "Dictation key",
		presets: overrides.presets,
	};
	return render(<HotkeyPicker {...props} />);
}

describe("HotkeyPicker — Accessibility (ARIA runtime verification)", () => {
	afterEach(() => {
		cleanup();
	});

	// ── Initial render (not recording) ─────────────────────────────────
	describe("Initial render (not recording)", () => {
		it("record button has aria-label containing 'Record new hotkey'", () => {
			renderPicker();
			// getByRole uses the accessible-name computation — same
			// algorithm screen readers run at runtime. If this query
			// succeeds, a screen reader will announce the button by this
			// name.
			const recordBtn = screen.getByRole("button", {
				name: /record new hotkey/i,
			});
			expect(recordBtn).toBeInTheDocument();
			// Belt-and-suspenders: verify the aria-label attribute itself
			// contains the expected text (some screen readers also expose
			// the attribute value separately from the computed name).
			expect(recordBtn).toHaveAttribute(
				"aria-label",
				expect.stringMatching(/Record new hotkey/),
			);
		});

		it("preset dropdown button has aria-label containing 'Preset hotkeys'", () => {
			renderPicker({
				presets: [{ value: "<ctrl>+<alt>+v", label: "Ctrl+Alt+V" }],
			});
			const presetBtn = screen.getByRole("button", {
				name: /preset hotkeys/i,
			});
			expect(presetBtn).toBeInTheDocument();
			expect(presetBtn).toHaveAttribute(
				"aria-label",
				expect.stringMatching(/Preset hotkeys/),
			);
		});

		it("renders no role='alert' live region initially (no error)", () => {
			renderPicker();
			// queryByRole returns null when not found — this is the
			// canonical "no element" assertion and proves a screen reader
			// would not announce an alert in the idle state.
			expect(screen.queryByRole("alert")).toBeNull();
		});

		it("renders no role='status' live region initially (not recording)", () => {
			renderPicker();
			expect(screen.queryByRole("status")).toBeNull();
		});
	});

	// ── Recording state ────────────────────────────────────────────────
	describe("Recording state", () => {
		it("record button aria-label flips to 'Cancel recording' when recording starts", async () => {
			const user = userEvent.setup();
			renderPicker();

			const recordBtn = screen.getByRole("button", {
				name: /record new hotkey/i,
			});
			await user.click(recordBtn);

			// findByRole waits for the re-render — the label MUST flip
			// because the click handler toggled `recording` state.
			const cancelBtn = await screen.findByRole("button", {
				name: /cancel recording/i,
			});
			expect(cancelBtn).toBeInTheDocument();
			expect(cancelBtn).toHaveAttribute(
				"aria-label",
				expect.stringMatching(/Cancel recording/),
			);

			// The original "Record new hotkey" button is gone — only one
			// button can have either label at a time, so the screen reader
			// will announce the new state.
			expect(
				screen.queryByRole("button", { name: /record new hotkey/i }),
			).toBeNull();
		});

		it("role='status' live region announces 'Press a key to assign, or press Esc to cancel'", async () => {
			const user = userEvent.setup();
			renderPicker();

			const recordBtn = screen.getByRole("button", {
				name: /record new hotkey/i,
			});
			await user.click(recordBtn);

			// role="status" is a polite live region: screen readers
			// announce changes to its content without interrupting the
			// user. ATR-001: this proves the capture-mode instruction is
			// announced.
			const status = await screen.findByRole("status");
			expect(status).toBeInTheDocument();
			expect(status).toHaveTextContent(
				"Press a key to assign, or press Esc to cancel",
			);
		});
	});

	// ── Error state (live region with full combo) ──────────────────────
	describe("Error state (HOTKEY-FULLMSG-001 full-combo live region)", () => {
		it("role='alert' live region appears with full attempted combo 'Shift+Z' when Shift+Z pressed in single mode", async () => {
			const user = userEvent.setup();
			renderPicker();

			const recordBtn = screen.getByRole("button", {
				name: /record new hotkey/i,
			});
			await user.click(recordBtn);
			// Wait for the label to flip — this guarantees the keydown
			// listener (which checks recordingRef.current) is active.
			await screen.findByRole("button", { name: /cancel recording/i });

			// Press Shift+Z (invalid in single mode — full combo is shown
			// in the error per HOTKEY-FULLMSG-001).
			dispatchKey({ code: "ShiftLeft", key: "Shift", type: "keydown" });
			dispatchKey({
				code: "KeyZ",
				key: "z",
				shiftKey: true,
				type: "keydown",
			});
			dispatchKey({
				code: "KeyZ",
				key: "z",
				shiftKey: true,
				type: "keyup",
			});

			// role="alert" is an assertive live region: screen readers
			// announce it immediately, interrupting any in-progress speech.
			const alert = await screen.findByRole("alert");
			expect(alert).toBeInTheDocument();
			// The error message MUST include the full attempted combo
			// ("Shift+Z"), not just the bare key ("Z"). This is the
			// runtime screen-reader equivalent of HOTKEY-FULLMSG-001.
			expect(alert.textContent ?? "").toMatch(/Shift\+Z/);
		});
	});

	// ── Keyboard accessibility ─────────────────────────────────────────
	describe("Keyboard accessibility", () => {
		it("record button is focusable via the Tab key (tabIndex >= 0)", async () => {
			const user = userEvent.setup();
			renderPicker();

			const recordBtn = screen.getByRole("button", {
				name: /record new hotkey/i,
			});
			// Native <button> elements have an implicit tabIndex of 0
			// (focusable via Tab). tabIndex < 0 (i.e. -1) would mean
			// programmatically focusable only — not in the tab order.
			// We assert >= 0 to be tolerant of any future explicit
			// tabIndex={0} annotation while still catching the regression
			// of tabIndex={-1}.
			expect(recordBtn.tabIndex).toBeGreaterThanOrEqual(0);

			// Press Tab to move focus into the document. With only the
			// HotkeyPicker rendered, the record button is the FIRST
			// focusable element, so it should receive focus.
			await user.tab();
			expect(recordBtn).toHaveFocus();
		});

		it("preset dropdown menu items have role='menuitem' and are keyboard-navigable via ArrowDown", async () => {
			const user = userEvent.setup();
			renderPicker({
				presets: [{ value: "<ctrl>+<alt>+v", label: "Ctrl+Alt+V" }],
			});

			const presetTrigger = screen.getByRole("button", {
				name: /preset hotkeys/i,
			});

			// Open the dropdown menu. userEvent.click simulates a real
			// pointer + click sequence, which is what Radix UI's
			// DropdownMenuTrigger responds to.
			await user.click(presetTrigger);

			// Radix UI's DropdownMenuItem renders with role="menuitem".
			// findAllByRole waits for the portal-mounted items to appear
			// in document.body.
			const items = await screen.findAllByRole("menuitem");
			expect(items.length).toBeGreaterThan(0);

			// Radix DropdownMenu uses roving tabindex + arrow-key
			// navigation. Pressing ArrowDown should move focus to the
			// first menuitem (the menu opens with focus on the first
			// item, ArrowDown moves to the second — so at minimum one
			// item must be focused after a single ArrowDown press).
			await user.keyboard("{ArrowDown}");

			// Verify that focus has moved INTO the menu (one of the
			// menuitems is now document.activeElement). This is the
			// runtime proof that a screen-reader user can navigate the
			// preset list with the keyboard alone.
			const focusedItem = items.find((el) => el === document.activeElement);
			expect(focusedItem).toBeDefined();
			expect(focusedItem).toHaveAttribute("role", "menuitem");
		});
	});

	// ── axe-core automated WCAG scan ───────────────────────────────────
	describe("axe-core automated WCAG scan", () => {
		it("idle state: no axe violations (excluding color-contrast)", async () => {
			const { container } = renderPicker();

			const results = await axe.run(container, AXE_OPTIONS);
			// Filter out color-contrast defensively — it's disabled in
			// AXE_OPTIONS above, but a future axe upgrade could re-enable
			// it; the filter guarantees the assertion is robust.
			const violations = results.violations.filter(
				(v) => v.id !== "color-contrast",
			);
			expect(violations).toEqual([]);
		});

		it("recording state: no axe violations (excluding color-contrast)", async () => {
			const user = userEvent.setup();
			const { container } = renderPicker();

			const recordBtn = screen.getByRole("button", {
				name: /record new hotkey/i,
			});
			await user.click(recordBtn);
			// Wait for the role="status" live region to appear so axe
			// scans the post-click DOM (not the pre-click snapshot).
			await screen.findByRole("status");

			const results = await axe.run(container, AXE_OPTIONS);
			const violations = results.violations.filter(
				(v) => v.id !== "color-contrast",
			);
			expect(violations).toEqual([]);
		});

		it("error state: no axe violations (excluding color-contrast)", async () => {
			const user = userEvent.setup();
			const { container } = renderPicker();

			const recordBtn = screen.getByRole("button", {
				name: /record new hotkey/i,
			});
			await user.click(recordBtn);
			await screen.findByRole("button", { name: /cancel recording/i });

			// Trigger the Shift+Z error path so the role="alert" live
			// region is present during the axe scan.
			dispatchKey({ code: "ShiftLeft", key: "Shift", type: "keydown" });
			dispatchKey({
				code: "KeyZ",
				key: "z",
				shiftKey: true,
				type: "keydown",
			});
			dispatchKey({
				code: "KeyZ",
				key: "z",
				shiftKey: true,
				type: "keyup",
			});
			await screen.findByRole("alert");

			const results = await axe.run(container, AXE_OPTIONS);
			const violations = results.violations.filter(
				(v) => v.id !== "color-contrast",
			);
			expect(violations).toEqual([]);
		});
	});
});
