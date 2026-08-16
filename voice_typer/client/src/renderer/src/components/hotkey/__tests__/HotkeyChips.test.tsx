import { cleanup, render } from "@testing-library/react";
import {
	afterEach,
	beforeAll,
	beforeEach,
	describe,
	expect,
	it,
	vi,
} from "vitest";
import { HotkeyChips } from "@/components/hotkey/HotkeyChips";

// The platform transform (``formatHotkeyForPlatform``) keys off
// navigator.userAgent, so every test pins its platform explicitly:
// win32 for the baseline chip-splitting behavior (the canonical
// display form), darwin for the macOS glyph cases. Without the stubs
// the suite's outcome would depend on the host OS.
const WIN_UA =
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36";
const MAC_UA =
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36";

beforeAll(() => {
	vi.stubGlobal("navigator", { userAgent: WIN_UA });
});

afterEach(() => {
	vi.unstubAllGlobals();
	vi.stubGlobal("navigator", { userAgent: WIN_UA });
	cleanup();
});

function chips(): HTMLElement[] {
	// Only the per-key chips carry data-slot="kbd"; the KbdGroup wrapper
	// is also a <kbd> element (data-slot="kbd-group") and must be
	// excluded from chip counts.
	return Array.from(document.querySelectorAll('kbd[data-slot="kbd"]'));
}

function groups(): HTMLElement[] {
	return Array.from(document.querySelectorAll('kbd[data-slot="kbd-group"]'));
}

describe("HotkeyChips", () => {
	it("renders a single key as one Kbd chip", () => {
		render(<HotkeyChips keys="Esc" />);
		const kbds = chips();
		expect(kbds).toHaveLength(1);
		expect(kbds[0]?.textContent).toBe("Esc");
	});

	it("splits a combo on '+' into a group of Kbd chips with a '+' separator", () => {
		render(<HotkeyChips keys="Ctrl+Alt+V" />);
		const kbds = chips();
		expect(kbds).toHaveLength(3);
		expect(kbds.map((k) => k.textContent)).toEqual(["Ctrl", "Alt", "V"]);
		// Each key chip carries the design-system kbd slot marker.
		for (const k of kbds) {
			expect(k.getAttribute("data-slot")).toBe("kbd");
		}
		// The group wrapper carries the kbd-group slot marker.
		expect(groups()).toHaveLength(1);
		// Two "+" separators between the three chips.
		const plusCount = Array.from(
			document.querySelectorAll('[data-slot="kbd-group"] span'),
		).filter((s) => s.textContent === "+").length;
		expect(plusCount).toBe(2);
	});

	it("splits alternative bindings on ' / ' into separate groups", () => {
		render(<HotkeyChips keys="Tab / Shift+Tab" />);
		const kbds = chips();
		expect(kbds).toHaveLength(3);
		expect(kbds.map((k) => k.textContent)).toEqual(["Tab", "Shift", "Tab"]);
		// "Tab" is a single key (plain chip) and "Shift+Tab" is a combo
		// (KbdGroup) → exactly one kbd-group wrapper.
		expect(groups()).toHaveLength(1);
		// The plain " / " separator text is present between alternatives
		// (it's aria-hidden so it must be queried via the DOM, not the
		// testing-library text matcher).
		const separators = Array.from(document.querySelectorAll("span")).filter(
			(s) => s.textContent === " / ",
		);
		expect(separators).toHaveLength(1);
	});

	it("falls back to a single chip holding the whole string for unexpected formats", () => {
		render(<HotkeyChips keys="Ctrl+Plus / Ctrl+Minus" />);
		const kbds = chips();
		// "Ctrl+Plus" → ["Ctrl", "Plus"], "Ctrl+Minus" → ["Ctrl", "Minus"].
		expect(kbds.map((k) => k.textContent)).toEqual([
			"Ctrl",
			"Plus",
			"Ctrl",
			"Minus",
		]);
	});

	it("handles an unresolved i18n key (no '+' / ' / ') as a single chip", () => {
		render(<HotkeyChips keys="help.keys.cancel" />);
		const kbds = chips();
		expect(kbds).toHaveLength(1);
		expect(kbds[0]?.textContent).toBe("help.keys.cancel");
	});
});

describe("HotkeyChips on macOS", () => {
	beforeEach(() => {
		// The outer describe's afterEach re-stubs WIN after each test,
		// so re-apply the macOS UA before every test in this group.
		vi.stubGlobal("navigator", { userAgent: MAC_UA });
	});

	it("renders modifier glyphs joined without '+' (Ctrl+B → ⌃B)", () => {
		render(<HotkeyChips keys="Ctrl+B" />);
		const kbds = chips();
		expect(kbds).toHaveLength(1);
		expect(kbds[0]?.textContent).toBe("\u2303B"); // ⌃B
	});

	it("renders the catalog's Alt+arrow shortcuts as glyph + arrow (Alt+← → ⌥←)", () => {
		render(<HotkeyChips keys="Alt+←" />);
		const kbds = chips();
		expect(kbds).toHaveLength(1);
		expect(kbds[0]?.textContent).toBe("\u2325←"); // ⌥←
	});

	it("keeps the ' / ' alternative separator with per-alternative glyphs", () => {
		render(<HotkeyChips keys="Tab / Shift+Tab" />);
		const kbds = chips();
		// "Tab" stays text; "Shift+Tab" → "⇧Tab" (glyph + text, no '+'
		// separator on macOS).
		expect(kbds.map((k) => k.textContent)).toEqual(["Tab", "\u21E7Tab"]);
		// The " / " separator is still rendered between alternatives.
		const separators = Array.from(document.querySelectorAll("span")).filter(
			(s) => s.textContent === " / ",
		);
		expect(separators).toHaveLength(1);
	});

	it("renders a multi-modifier combo as one glyph string (Ctrl+Alt+V → ⌃⌥V)", () => {
		render(<HotkeyChips keys="Ctrl+Alt+V" />);
		const kbds = chips();
		expect(kbds).toHaveLength(1);
		expect(kbds[0]?.textContent).toBe("\u2303\u2325V"); // ⌃⌥V
	});

	it("is a no-op for already-formatted glyph input (idempotent)", () => {
		// NB: the glyph string goes through a JS expression (JSX
		// attribute strings don't process `\u` escapes).
		render(<HotkeyChips keys={"\u2303\u2325V"} />); // already "⌃⌥V"
		const kbds = chips();
		expect(kbds).toHaveLength(1);
		expect(kbds[0]?.textContent).toBe("\u2303\u2325V");
	});

	it("leaves non-modifier keys (Esc, ?, Space, Enter) untouched", () => {
		for (const keys of ["Esc", "?", "Space", "Enter"]) {
			render(<HotkeyChips keys={keys} />);
			const kbds = chips();
			expect(kbds).toHaveLength(1);
			expect(kbds[0]?.textContent).toBe(keys);
			cleanup();
		}
	});
});
