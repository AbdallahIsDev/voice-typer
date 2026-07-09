/**
 * Tests for the shared hotkey validation system (HOTKEY-UNIFY-001).
 *
 * These tests pin the contract documented in hotkey-validation.ts:
 * - isReserved detects OS-reserved shortcuts per-platform.
 * - validateHotkey accepts modifier-only triggers (Shift alone is a
 *   valid dictation key via modifier-only release detection).
 * - validateHotkey rejects combos that end with a modifier
 *   ("Shift+Ctrl" → reject the WHOLE combo, not return a partial
 *   "<shift>" fragment). This is the unit-test side of the
 *   partial-assign fix (Problem 2.2): the function's return type
 *   declares `partial?: never`, and these tests assert the field is
 *   never set so a future refactor can't silently reintroduce the
 *   partial-assign bug by adding a `partial` field.
 *
 * The backend mirror of this list lives in
 * voice_typer/server/config_validators.py as _RESERVED_HOTKEYS; if
 * you add a shortcut here, add it there too (and update the existing
 * invariant tests in hotkey-utils.test.ts if appropriate).
 */
import { describe, expect, it } from "vitest";
import {
	detectPlatform,
	isReserved,
	MODIFIER_KEYS_SHARED,
	normalizeHotkey,
	RESERVED_SHORTCUTS,
	validateHotkey,
} from "../hotkey-validation";

describe("isReserved", () => {
	it("detects Win+E on Windows", () => {
		expect(isReserved("<win>+e", "win32")).toBe(true);
	});

	it("detects Win+V (clipboard history) on Windows", () => {
		expect(isReserved("<win>+v", "win32")).toBe(true);
	});

	it("detects Win+Space (input-language switch) on Windows", () => {
		expect(isReserved("<win>+space", "win32")).toBe(true);
	});

	it("detects Cmd+Space on macOS", () => {
		expect(isReserved("<cmd>+space", "darwin")).toBe(true);
	});

	it("detects Cmd+Q on macOS", () => {
		expect(isReserved("<cmd>+q", "darwin")).toBe(true);
	});

	it("detects Cmd+Shift+3 (screenshot) on macOS", () => {
		expect(isReserved("<cmd>+shift+3", "darwin")).toBe(true);
	});

	it("detects Super+L (lock) on Linux", () => {
		expect(isReserved("<super>+l", "linux")).toBe(true);
	});

	it("returns false for non-reserved shortcuts on Windows", () => {
		expect(isReserved("<f2>", "win32")).toBe(false);
		expect(isReserved("<caps_lock>", "win32")).toBe(false);
		expect(isReserved("<ctrl>+<alt>+v", "win32")).toBe(false);
	});

	it("returns false for non-reserved shortcuts on macOS", () => {
		expect(isReserved("<f2>", "darwin")).toBe(false);
		expect(isReserved("<caps_lock>", "darwin")).toBe(false);
		expect(isReserved("<cmd>+<shift>+v", "darwin")).toBe(false);
	});

	it("returns false for non-reserved shortcuts on Linux", () => {
		expect(isReserved("<f2>", "linux")).toBe(false);
		expect(isReserved("<super>+<space>", "linux")).toBe(false);
		expect(isReserved("<ctrl>+<alt>+v", "linux")).toBe(false);
	});

	it("is case-insensitive (Win+E and <WIN>+E are both reserved)", () => {
		expect(isReserved("<WIN>+E", "win32")).toBe(true);
		expect(isReserved("<Win>+E", "win32")).toBe(true);
		expect(isReserved("<CMD>+SPACE", "darwin")).toBe(true);
	});

	it("returns false for an empty hotkey", () => {
		expect(isReserved("", "win32")).toBe(false);
	});

	it("returns false on an unknown platform", () => {
		expect(isReserved("<win>+e", "unknown")).toBe(false);
		expect(isReserved("<cmd>+space", "freebsd")).toBe(false);
	});

	it("does NOT cross-contaminate platforms (Cmd+Space is not reserved on Windows)", () => {
		// Cmd+Space is macOS-only reserved; on Windows it's a no-op
		// (Cmd doesn't exist), and on Linux it's a regular combo.
		expect(isReserved("<cmd>+space", "win32")).toBe(false);
		expect(isReserved("<cmd>+space", "linux")).toBe(false);
		// Symmetric: Win+E is Windows-only reserved; on macOS and
		// Linux it's just an unbound combo.
		expect(isReserved("<win>+e", "darwin")).toBe(false);
		expect(isReserved("<win>+e", "linux")).toBe(false);
	});
});

describe("validateHotkey — reserved shortcut rejection", () => {
	it("rejects Win+E on Windows with a reason", () => {
		const result = validateHotkey("<win>+e", "win32");
		expect(result.valid).toBe(false);
		expect(result.reason).toBeTruthy();
	});

	it("rejects Cmd+Space on macOS with a reason", () => {
		const result = validateHotkey("<cmd>+space", "darwin");
		expect(result.valid).toBe(false);
		expect(result.reason).toBeTruthy();
	});

	it("rejects Super+L on Linux with a reason", () => {
		const result = validateHotkey("<super>+l", "linux");
		expect(result.valid).toBe(false);
		expect(result.reason).toBeTruthy();
	});

	it("does NOT reject non-reserved combos on the same platform", () => {
		expect(validateHotkey("<ctrl>+<alt>+v", "win32").valid).toBe(true);
		// HOTKEY-VALIDATION-002: Cmd+<letter> is blocked on macOS
		// (even with other modifiers) because Cmd+Shift+V is
		// "Paste and Match Style" in many apps. Use Cmd+F-key
		// instead, which is allowed.
		expect(validateHotkey("<cmd>+<f5>", "darwin").valid).toBe(true);
		expect(validateHotkey("<super>+<space>", "linux").valid).toBe(true);
	});
});

describe("validateHotkey — modifier-only (single-key triggers)", () => {
	it("accepts Shift alone (modifier-only release is a valid single-key trigger)", () => {
		const result = validateHotkey("<shift>", "win32");
		expect(result.valid).toBe(true);
	});

	it("accepts Ctrl alone", () => {
		expect(validateHotkey("<ctrl>", "win32").valid).toBe(true);
	});

	it("accepts Alt alone", () => {
		expect(validateHotkey("<alt>", "win32").valid).toBe(true);
	});

	it("rejects Cmd alone (universally reserved — conflicts with system shortcuts)", () => {
		expect(validateHotkey("<cmd>", "darwin").valid).toBe(false);
	});

	it("accepts Caps Lock alone", () => {
		expect(validateHotkey("<caps_lock>", "win32").valid).toBe(true);
	});

	it("accepts a function key alone", () => {
		expect(validateHotkey("<f2>", "win32").valid).toBe(true);
		expect(validateHotkey("<f12>", "darwin").valid).toBe(true);
	});
});

describe("validateHotkey — partial-assign contract (Problem 2.2)", () => {
	it("accepts Shift+Ctrl (pure-modifier combo — HOTKEY-MULTIKEY-001)", () => {
		// HOTKEY-MULTIKEY-001 (Task 1.3): pure-modifier combos like
		// ``<shift>+<ctrl>`` are now ALLOWED — they're valid modifier-only
		// release triggers in the native backends. The previous rule
		// "combo must not end with a modifier" incorrectly rejected these
		// and caused a frontend/backend mismatch (the backend has never
		// had this rule). The partial-assign contract still holds: the
		// function must NOT return a "partial" field that the caller
		// could misinterpret as "assign just <shift>".
		const result = validateHotkey("<shift>+<ctrl>", "win32");
		expect(result.valid).toBe(true);
		// The contract: validateHotkey never returns a partial result.
		// The `partial` field is typed as `never` on ValidationResult;
		// we assert its absence here so a future refactor that adds
		// the field (re-introducing the partial-assign bug) fails
		// this test loudly.
		expect(result).not.toHaveProperty("partial");
	});

	it("accepts Ctrl+Alt (pure-modifier combo — HOTKEY-MULTIKEY-001)", () => {
		const result = validateHotkey("<ctrl>+<alt>", "win32");
		expect(result.valid).toBe(true);
		expect(result).not.toHaveProperty("partial");
	});

	it("accepts Shift+Alt+Cmd (multi-modifier combo on macOS — HOTKEY-MULTIKEY-001)", () => {
		const result = validateHotkey("<shift>+<alt>+<cmd>", "darwin");
		expect(result.valid).toBe(true);
		expect(result).not.toHaveProperty("partial");
	});

	it("rejects mixed combo ending with modifier (Ctrl+V+Alt — partial-assign guard)", () => {
		// HOTKEY-MULTIKEY-001: the structural rule still rejects combos
		// that MIX modifiers AND non-modifiers but end with a modifier
		// (e.g. ``<ctrl>+<v>+<alt>``). This is the partial-assign guard
		// — the user almost certainly meant ``<ctrl>+<alt>+<v>``.
		const result = validateHotkey("<ctrl>+<v>+<alt>", "win32");
		expect(result.valid).toBe(false);
		expect(result.reason).toBeTruthy();
		expect(result).not.toHaveProperty("partial");
	});

	it("accepts a valid combo (Ctrl+Alt+V — non-modifier terminator)", () => {
		// Counter-example: a combo that DOES end with a non-modifier
		// is valid. This makes sure the "ends with modifier" check
		// isn't over-rejecting. Uses Ctrl+Alt+V (not Shift+V) because
		// pure Shift+<letter> is now correctly rejected (interferes
		// with capitalization) — see HOTKEY-VALIDATION-002.
		const result = validateHotkey("<ctrl>+<alt>+<v>", "win32");
		expect(result.valid).toBe(true);
	});

	it("never sets `partial` on any valid result", () => {
		// Sweep every modifier alone — all should be valid with no
		// `partial` field. This locks the contract for the
		// modifier-only-release-detection path that drove the
		// original partial-assign bug.
		// HOTKEY-VALIDATION-002: `<win>` and `<super>` are excluded
		// on win32 because they are OS-shell-reserved (Win opens
		// Start menu, Super opens Activities on Linux). They are
		// still valid on darwin where the Win/Super key doesn't
		// exist as a system modifier.
		for (const mod of MODIFIER_KEYS_SHARED) {
			// HOTKEY-VALIDATION-002: `<win>`, `<super>`, and `<cmd>`
			// are excluded on win32 because they're in
			// UNIVERSAL_RESERVED_SHORTCUTS (system gestures).
			if (mod === "win" || mod === "super" || mod === "cmd") continue;
			const result = validateHotkey(`<${mod}>`, "win32");
			expect(result.valid).toBe(true);
			expect(result).not.toHaveProperty("partial");
		}
	});

	it("never sets `partial` on any invalid result", () => {
		// Sweep a representative set of invalid inputs: empty,
		// reserved, and mixed-combo-ends-with-modifier. All inputs are
		// invalid on win32 specifically (the platform passed below).
		// HOTKEY-MULTIKEY-001: pure-modifier combos (``<shift>+<ctrl>``,
		// ``<ctrl>+<alt>``) are now VALID, so they're removed from this
		// list. We use ``<ctrl>+<v>+<alt>`` (mixed combo ending with
		// modifier) as the structural-rule representative instead.
		const invalidInputs = [
			"",
			"   ",
			"<win>+e",
			"<win>+v",
			"<ctrl>+<v>+<alt>", // mixed combo ending with modifier
			"+++",
			"<>",
		];
		for (const hotkey of invalidInputs) {
			const result = validateHotkey(hotkey, "win32");
			expect(result.valid).toBe(false);
			expect(result).not.toHaveProperty("partial");
		}
	});
});

describe("validateHotkey — empty / malformed inputs", () => {
	it("rejects an empty string", () => {
		const result = validateHotkey("", "win32");
		expect(result.valid).toBe(false);
		expect(result.reason).toBeTruthy();
	});

	it("rejects a whitespace-only string", () => {
		const result = validateHotkey("   ", "win32");
		expect(result.valid).toBe(false);
	});

	it("rejects a string with only angle brackets", () => {
		const result = validateHotkey("<>", "win32");
		expect(result.valid).toBe(false);
	});

	it("rejects a string with only plus signs", () => {
		const result = validateHotkey("+++", "win32");
		expect(result.valid).toBe(false);
	});
});

describe("RESERVED_SHORTCUTS table invariants", () => {
	it("has entries for win32, darwin, and linux", () => {
		expect(Array.isArray(RESERVED_SHORTCUTS.win32)).toBe(true);
		expect(RESERVED_SHORTCUTS.win32.length).toBeGreaterThan(0);
		expect(Array.isArray(RESERVED_SHORTCUTS.darwin)).toBe(true);
		expect(RESERVED_SHORTCUTS.darwin.length).toBeGreaterThan(0);
		expect(Array.isArray(RESERVED_SHORTCUTS.linux)).toBe(true);
		expect(RESERVED_SHORTCUTS.linux.length).toBeGreaterThan(0);
	});

	it("does NOT reserve <super>+<space> on Linux (existing invariant)", () => {
		// The existing hotkey-utils.test.ts asserts that
		// COMBO_PRESETS still offers <super>+<space> on Linux. This
		// test pins the corresponding root cause: <super>+<space>
		// must NOT appear in RESERVED_SHORTCUTS.linux. Most Linux
		// desktop environments allow Super+Space to be reassigned,
		// so we don't block it.
		expect(RESERVED_SHORTCUTS.linux).not.toContain("<super>+<space>");
	});

	it("every entry is lowercase (so isReserved can compare lowercase-to-lowercase)", () => {
		// The isReserved() helper lowercases both sides before
		// comparing. If the source list ever drifts to mixed case,
		// the comparison still works — but keeping the source
		// lowercase is a useful convention so a future contributor
		// reading the table sees the same form isReserved produces.
		for (const platform of ["win32", "darwin", "linux"]) {
			for (const entry of RESERVED_SHORTCUTS[platform]) {
				expect(entry).toBe(entry.toLowerCase());
			}
		}
	});
});

describe("detectPlatform", () => {
	it("returns one of the known platform keys or 'unknown'", () => {
		const platform = detectPlatform();
		expect(["win32", "darwin", "linux", "unknown"]).toContain(platform);
	});

	it("returns 'unknown' when navigator is undefined", () => {
		// Save the original navigator so we can restore it after
		// stubbing it. jsdom provides a navigator by default.
		const originalNavigator = globalThis.navigator;
		// @ts-expect-error — deliberately assigning undefined to
		// navigator to simulate an environment without it (SSR).
		globalThis.navigator = undefined;
		try {
			expect(detectPlatform()).toBe("unknown");
		} finally {
			globalThis.navigator = originalNavigator;
		}
	});
});

describe("normalizeHotkey", () => {
	it("lowercases and strips angle brackets", () => {
		expect(normalizeHotkey("<Ctrl>+<V>")).toBe("ctrl+v");
		expect(normalizeHotkey("<CMD>+<SHIFT>+<3>")).toBe("cmd+shift+3");
	});

	it("handles a single key (no plus signs)", () => {
		expect(normalizeHotkey("<Caps_Lock>")).toBe("caps_lock");
		expect(normalizeHotkey("F2")).toBe("f2");
	});

	it("returns an empty string for empty input", () => {
		expect(normalizeHotkey("")).toBe("");
		expect(normalizeHotkey("   ")).toBe("");
	});

	it("trims whitespace around parts", () => {
		expect(normalizeHotkey("<Ctrl> + <V>")).toBe("ctrl+v");
	});

	it("drops empty parts", () => {
		expect(normalizeHotkey("<Ctrl>++<V>")).toBe("ctrl+v");
	});
});
