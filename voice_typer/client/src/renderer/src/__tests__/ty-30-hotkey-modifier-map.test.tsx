/**
 * TY-30 regression test: `MODIFIER_CODE_MAP` is a module-level constant.
 *
 * `HotkeyPicker.tsx` previously called `getModifierCodeMap(IS_MAC)`
 * inside `handleKeyDown` and `handleKeyUp` on every keystroke. Each
 * call allocated a fresh 8-key object literal — at 60–120 keystrokes
 * per second during typing bursts, that was non-trivial GC pressure
 * for no benefit (the map depends only on `IS_MAC`, which is fixed
 * at module load).
 *
 * After TY-30: the map is hoisted to module scope as
 * `const MODIFIER_CODE_MAP = getModifierCodeMap(IS_MAC);` and both
 * handlers reference the module-level constant.
 *
 * This test verifies:
 *   1. `getModifierCodeMap` is called EXACTLY ONCE at module load
 *      (not per keystroke). We mock the `hotkey-utils` module to spy
 *      on `getModifierCodeMap`, then render `HotkeyPicker` and
 *      dispatch keydown / keyup events. The spy's call count must
 *      stay at 1.
 *   2. The map's value is stable across renders (snapshot test).
 *      The first call's return value is captured; subsequent renders
 *      must produce the same value (which they do, because the
 *      module-level constant is shared).
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// TY-30: spy on `getModifierCodeMap` BEFORE HotkeyPicker is imported.
// `vi.mock` is hoisted by vitest so it runs before any import. We wrap
// the real implementation so the spy returns a real map (otherwise
// HotkeyPicker's `MODIFIER_CODE_MAP` would be `undefined` and the
// handlers would crash on `MODIFIER_CODE_MAP[e.code]`).
vi.mock("@/components/hotkey/hotkey-utils", async (importOriginal) => {
	const actual =
		await importOriginal<typeof import("@/components/hotkey/hotkey-utils")>();
	return {
		...actual,
		getModifierCodeMap: vi.fn(actual.getModifierCodeMap),
	};
});

// Import AFTER the mock is set up so the module-load call to
// `getModifierCodeMap(IS_MAC)` (which creates `MODIFIER_CODE_MAP`) is
// captured by the spy.
const { getModifierCodeMap } = await import("@/components/hotkey/hotkey-utils");
const { HotkeyPicker } = await import("@/components/hotkey/HotkeyPicker");

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
	act(() => {
		window.dispatchEvent(ev);
	});
}

async function enterCaptureMode() {
	const btn = screen.getByRole("button", { name: /record new hotkey/i });
	await act(async () => {
		btn.click();
	});
	await screen.findByRole("button", { name: /cancel recording/i });
}

describe("TY-30: MODIFIER_CODE_MAP hoisted to module scope", () => {
	afterEach(() => {
		cleanup();
	});

	it("calls getModifierCodeMap exactly once at module load", () => {
		// The module-load call already happened when HotkeyPicker was
		// imported at the top of this file.
		expect(getModifierCodeMap).toHaveBeenCalledTimes(1);
	});

	it("does NOT call getModifierCodeMap per keystroke (single mode)", async () => {
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

		// Dispatch a modifier keydown + keyup (these used to call
		// getModifierCodeMap twice per pair — once in handleKeyDown,
		// once in handleKeyUp).
		dispatchKey({ code: "AltLeft", key: "Alt", type: "keydown" });
		dispatchKey({ code: "AltLeft", key: "Alt", type: "keyup" });

		// Dispatch a non-modifier keydown + keyup (these used to call
		// getModifierCodeMap once per event — handleKeyDown checks
		// MODIFIER_CODE_MAP to detect modifiers, handleKeyUp does too).
		dispatchKey({ code: "KeyA", key: "a", type: "keydown" });
		dispatchKey({ code: "KeyA", key: "a", type: "keyup" });

		// The spy should still have exactly 1 call — the module-load
		// call. No per-keystroke allocations.
		expect(getModifierCodeMap).toHaveBeenCalledTimes(1);
	});

	it("does NOT call getModifierCodeMap per keystroke (combo mode)", async () => {
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

		// Dispatch a multi-modifier combo (Ctrl+Shift) — both modifiers
		// generate keydown + keyup events.
		dispatchKey({
			code: "ControlLeft",
			key: "Control",
			type: "keydown",
			ctrlKey: true,
		});
		dispatchKey({
			code: "ShiftLeft",
			key: "Shift",
			type: "keydown",
			ctrlKey: true,
			shiftKey: true,
		});
		dispatchKey({
			code: "ControlLeft",
			key: "Control",
			type: "keyup",
			shiftKey: true,
		});
		dispatchKey({
			code: "ShiftLeft",
			key: "Shift",
			type: "keyup",
		});

		// Still exactly 1 call (module-load).
		expect(getModifierCodeMap).toHaveBeenCalledTimes(1);
	});

	it("snapshot: MODIFIER_CODE_MAP value is stable (matches the first call's return)", () => {
		// The first call (at module load) returned the map that became
		// MODIFIER_CODE_MAP. Subsequent renders must NOT produce a
		// different value — the module-level constant is shared.
		const firstResult = (getModifierCodeMap as ReturnType<typeof vi.fn>).mock
			.results[0]?.value as Record<string, string> | undefined;
		expect(firstResult).toBeDefined();
		expect(firstResult).toMatchInlineSnapshot(`
			{
			  "AltLeft": "alt",
			  "AltRight": "alt",
			  "ControlLeft": "ctrl",
			  "ControlRight": "ctrl",
			  "MetaLeft": "win",
			  "MetaRight": "win",
			  "ShiftLeft": "shift",
			  "ShiftRight": "shift",
			}
		`);

		// The map IS the platform-correct one (this snapshot was
		// captured on Linux, so Meta* → "win". On macOS the snapshot
		// would have Meta* → "cmd" — that test runs in the same sandbox
		// so IS_MAC is stable, and the snapshot reflects the current
		// platform).
	});
});
