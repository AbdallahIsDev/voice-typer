/**
 * HOTKEY-MULTIKEY-001 + HOTKEY-FULLMSG-001 — Tests for the multi-key
 * capture architecture and full-shortcut error messages in HotkeyPicker.
 *
 * These tests verify the behavioral requirements added in Tasks 1.1 and 1.3:
 *
 *   - Pressing multiple non-modifier keys (e.g. Delete+End) captures the
 *     FULL combo, not just the last-pressed key.
 *   - Pressing modifiers alongside a non-modifier in single mode shows an
 *     error referencing the FULL attempted combo (e.g. "Shift+Z can't be
 *     used as a dictation key…"), not just the bare non-modifier.
 *   - Pressing an unsupported key alongside modifiers shows the full
 *     attempted combo in the error message (e.g. "Shift+F13 is not
 *     supported.").
 *   - Release order does NOT affect the captured combo: releasing Ctrl
 *     before Shift produces the same combo as releasing Shift before Ctrl.
 *   - Modifier-only combos (e.g. Ctrl+Shift) are captured in combo mode.
 *   - ESC during capture still cancels (preserved ESC-KEYUP-FIX behavior).
 *
 * The tests use @testing-library/react to mount HotkeyPicker and dispatch
 * realistic keydown/keyup sequences to window (HotkeyPicker listens on
 * window, not the button). All dispatches are wrapped in `act()` to
 * ensure React flushes state updates synchronously between events.
 */
import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { HotkeyPicker } from "../hotkey/HotkeyPicker";

interface DispatchOpts {
	code: string;
	key: string;
	ctrlKey?: boolean;
	shiftKey?: boolean;
	altKey?: boolean;
	metaKey?: boolean;
	type: "keydown" | "keyup";
}

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
	// Wrap in act() so React flushes any state updates triggered by the
	// event handler synchronously, before the test continues.
	act(() => {
		window.dispatchEvent(ev);
	});
}

async function enterCaptureMode() {
	const btn = screen.getByRole("button", { name: /record new hotkey/i });
	await act(async () => {
		btn.click();
	});
	// Wait for the button's aria-label to flip to "Cancel recording" —
	// that's the most reliable signal that recording state is now true.
	await screen.findByRole("button", { name: /cancel recording/i });
}

async function waitForError(): Promise<string> {
	const alert = await screen.findByRole("alert");
	return alert.textContent ?? "";
}

describe("HotkeyPicker — HOTKEY-MULTIKEY-001 multi-key capture", () => {
	it("captures a single non-modifier key in single mode (Delete)", async () => {
		const onChange = vi.fn();
		render(
			<HotkeyPicker
				value=""
				onChange={onChange}
				mode="single"
				aria-label="Dictation key"
			/>,
		);
		await enterCaptureMode();

		dispatchKey({ code: "Delete", key: "Delete", type: "keydown" });
		dispatchKey({ code: "Delete", key: "Delete", type: "keyup" });

		expect(onChange).toHaveBeenCalledWith("<delete>");
	});

	it("captures a single modifier in single mode (Alt alone)", async () => {
		const onChange = vi.fn();
		render(
			<HotkeyPicker
				value=""
				onChange={onChange}
				mode="single"
				aria-label="Dictation key"
			/>,
		);
		await enterCaptureMode();

		dispatchKey({ code: "AltLeft", key: "Alt", type: "keydown" });
		dispatchKey({ code: "AltLeft", key: "Alt", type: "keyup" });

		expect(onChange).toHaveBeenCalledWith("<alt>");
	});

	it("captures a modifier-only combo in combo mode (Ctrl+Shift)", async () => {
		const onChange = vi.fn();
		render(
			<HotkeyPicker
				value=""
				onChange={onChange}
				mode="combo"
				aria-label="Re-paste hotkey"
			/>,
		);
		await enterCaptureMode();

		dispatchKey({ code: "ControlLeft", key: "Control", type: "keydown" });
		dispatchKey({ code: "ShiftLeft", key: "Shift", type: "keydown" });
		dispatchKey({ code: "ControlLeft", key: "Control", type: "keyup" });
		dispatchKey({ code: "ShiftLeft", key: "Shift", type: "keyup" });

		expect(onChange).toHaveBeenCalledWith("<ctrl>+<shift>");
	});

	it("captures a multi-non-modifier combo in combo mode (Delete+End)", async () => {
		const onChange = vi.fn();
		render(
			<HotkeyPicker
				value=""
				onChange={onChange}
				mode="combo"
				aria-label="Re-paste hotkey"
			/>,
		);
		await enterCaptureMode();

		dispatchKey({ code: "Delete", key: "Delete", type: "keydown" });
		dispatchKey({ code: "End", key: "End", type: "keydown" });
		dispatchKey({ code: "End", key: "End", type: "keyup" });
		expect(onChange).not.toHaveBeenCalled();
		dispatchKey({ code: "Delete", key: "Delete", type: "keyup" });

		expect(onChange).toHaveBeenCalledWith("<delete>+<end>");
	});

	it("is release-order independent: Ctrl+Shift release order doesn't matter", async () => {
		const onChange = vi.fn();
		render(
			<HotkeyPicker
				value=""
				onChange={onChange}
				mode="combo"
				aria-label="Re-paste hotkey"
			/>,
		);
		await enterCaptureMode();

		dispatchKey({ code: "ShiftLeft", key: "Shift", type: "keydown" });
		dispatchKey({ code: "ControlLeft", key: "Control", type: "keydown" });
		dispatchKey({ code: "ShiftLeft", key: "Shift", type: "keyup" });
		dispatchKey({ code: "ControlLeft", key: "Control", type: "keyup" });

		expect(onChange).toHaveBeenCalledWith("<ctrl>+<shift>");
	});

	it("captures Ctrl+Alt+U in combo mode", async () => {
		const onChange = vi.fn();
		render(
			<HotkeyPicker
				value=""
				onChange={onChange}
				mode="combo"
				aria-label="Re-paste hotkey"
			/>,
		);
		await enterCaptureMode();

		dispatchKey({ code: "ControlLeft", key: "Control", type: "keydown" });
		dispatchKey({ code: "AltLeft", key: "Alt", type: "keydown" });
		dispatchKey({
			code: "KeyU",
			key: "u",
			ctrlKey: true,
			altKey: true,
			type: "keydown",
		});
		dispatchKey({
			code: "KeyU",
			key: "u",
			ctrlKey: true,
			altKey: true,
			type: "keyup",
		});

		expect(onChange).toHaveBeenCalledWith("<ctrl>+<alt>+<u>");
	});

	it("HOTKEY-FULLMSG-001: shows full combo in error when Shift+Z pressed in single mode", async () => {
		const onChange = vi.fn();
		render(
			<HotkeyPicker
				value=""
				onChange={onChange}
				mode="single"
				aria-label="Dictation key"
			/>,
		);
		await enterCaptureMode();

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

		expect(onChange).not.toHaveBeenCalled();
		const err = await waitForError();
		expect(err).toMatch(/Shift\+Z/);
	});

	it("HOTKEY-FULLMSG-001: shows full combo in error when unsupported key pressed with modifiers", async () => {
		const onChange = vi.fn();
		render(
			<HotkeyPicker
				value=""
				onChange={onChange}
				mode="single"
				aria-label="Dictation key"
			/>,
		);
		await enterCaptureMode();

		dispatchKey({ code: "ShiftLeft", key: "Shift", type: "keydown" });
		dispatchKey({
			code: "F24",
			key: "F24",
			shiftKey: true,
			type: "keydown",
		});

		expect(onChange).not.toHaveBeenCalled();
		const err = await waitForError();
		expect(err).toMatch(/Shift\+F24/);
		expect(err).toMatch(/not supported/i);
	});

	it("ESC cancels capture without committing (preserved ESC-KEYUP-FIX)", async () => {
		const onChange = vi.fn();
		const onCaptureEnd = vi.fn();
		render(
			<HotkeyPicker
				value=""
				onChange={onChange}
				mode="single"
				aria-label="Dictation key"
				onCaptureEnd={onCaptureEnd}
			/>,
		);
		await enterCaptureMode();

		dispatchKey({ code: "Escape", key: "Escape", type: "keydown" });
		expect(onChange).not.toHaveBeenCalled();
		expect(onCaptureEnd).not.toHaveBeenCalled();

		dispatchKey({ code: "Escape", key: "Escape", type: "keyup" });

		expect(onChange).not.toHaveBeenCalled();
		expect(onCaptureEnd).toHaveBeenCalledTimes(1);
	});

	it("rejects single-letter hotkey in single mode (e.g. <z>)", async () => {
		const onChange = vi.fn();
		render(
			<HotkeyPicker
				value=""
				onChange={onChange}
				mode="single"
				aria-label="Dictation key"
			/>,
		);
		await enterCaptureMode();

		dispatchKey({ code: "KeyZ", key: "z", type: "keydown" });
		dispatchKey({ code: "KeyZ", key: "z", type: "keyup" });

		expect(onChange).not.toHaveBeenCalled();
		await waitForError();
	});
});
